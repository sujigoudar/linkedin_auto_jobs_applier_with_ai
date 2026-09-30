"""PU-A3: real, persisted equity/P&L snapshots per account.

Phase B1 (the trading command center batch, running concurrently with
this one) investigated and confirmed: `GET /accounts/{id}/economics`/
`balance` only ever expose CURRENT aggregate values, never a real time
series -- "no persisted equity/balance HISTORY exists." That blocks an
equity curve, a P&L curve, underwater/drawdown, daily P&L, and (later)
rolling return/volatility/Sharpe/Sortino, monthly-return heatmaps, and
drawdown-duration analytics. `EquitySnapshotter` closes that gap the same
way `OrderReconciler`/`PriceMonitor`/`ProviderScout` close their own
periodic-loop gaps: a background loop, polling on an interval, writing a
durable row via `SignalStore.record_equity_snapshot` -- not a new
computation, just a persisted point-in-time capture of numbers this
codebase already computes correctly elsewhere.

## Honest labeling: `cumulative_pnl`, not `equity`

This codebase's `DestinationAccount`/`AccountRequest` (app/models.py,
app/main.py) have no `starting_balance`/`starting_capital`-type field --
checked before writing this module, not assumed. Without a real,
configured baseline, there is no genuine absolute "equity" figure to
report; inventing one (e.g. treating cumulative P&L as if it started from
some assumed capital) would be exactly the kind of fabricated number this
project's other modules (app/economics.py, app/execution_quality.py)
explicitly refuse to produce. So the persisted "how has this account done
over time" figure is named `cumulative_pnl` and is exactly `realized_pnl +
unrealized_pnl` at that snapshot's `captured_at` -- a real, honestly
labeled running P&L series, never a fabricated account balance.

## realized_pnl: never a second P&L calculation

`realized_pnl` is `app/economics.py`'s `compute_account_economics(store,
account_id).realized_pnl`, taken verbatim. This module must NEVER
recompute realized P&L a different way -- see this slice's own
load-bearing test (`tests/test_equity_history.py`), which breaks this
invariant on purpose and confirms it's caught.

## unrealized_pnl: reusing PU-A1's real last-known-price mechanism

For every symbol with a nonzero open quantity (per that same
`compute_account_economics` call's per-symbol cost basis), this module
looks for a real last-observed price on that account/symbol's OPEN
managed-lifecycle position -- `app/lifecycle/models.py`'s
`PositionLifecycle.last_observed_price`, itself fed only by a real entry
fill or a real `app/pricing.py` `PriceMonitor` tick (see that field's own
docstring). Where one exists: `(last_observed_price - average_cost) *
open_quantity` (this formula is sign-correct for both long and short,
since `open_quantity` and `average_cost` already encode direction/cost the
way `compute_account_economics` computes them). Where one doesn't exist --
a non-managed-lifecycle account, a broker with no live-price capability,
or a managed-lifecycle position that simply hasn't had a price observation
yet -- that symbol contributes 0.0 to `unrealized_pnl` and is listed by
name in `unpriced_open_symbols`, an explicit, honest disclosure that this
figure is incomplete for that symbol rather than a silent understatement.

No separate price source is invented here: this module never calls a
broker or a price feed itself, only reads what PU-A1's existing mechanism
already observed.

## Periodic tick only -- no immediate on-fill snapshot (documented choice)

An immediate snapshot on every real fill (in addition to the periodic
tick) was considered, per this slice's own brief. It was left out: wiring
it in would mean adding a snapshot call into
`PositionLifecycleManager.on_entry_fill`/`_apply_exit_fill`/`on_stop_filled`
(and the plain, non-managed-lifecycle fill path in app/engine.py) -- several
new call sites inside the core fill-handling flow other Phase A slices
already hardened, for a benefit (a snapshot arriving a few
`EQUITY_SNAPSHOT_INTERVAL_SECONDS` sooner after a fill) that a short
periodic interval already covers well enough for charting. The periodic
tick alone is a real, complete MVP: every account gets a snapshot at least
once per `config.EQUITY_SNAPSHOT_INTERVAL_SECONDS`, forever, for as long as
this process runs.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.db import SignalStore
from app.economics import compute_account_economics
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionLifecycle

logger = logging.getLogger(__name__)


class EquitySnapshotter:
    def __init__(
        self,
        store: SignalStore,
        lifecycle_manager: PositionLifecycleManager,
        *,
        interval_seconds: float = 300.0,
    ):
        self.store = store
        self.lifecycle_manager = lifecycle_manager
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        #: Same contract as OrderReconciler/PriceMonitor/ProviderScout's own
        #: identical field -- surfaced by app/main.py's /health.
        self.last_success_at: datetime | None = None

    async def start(self) -> None:
        # OPS-02: idempotent, same fix/reasoning as the other schedulers'
        # own start() methods (app/reconciliation.py, app/pricing.py,
        # app/provider_scout.py).
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                self.snapshot_once()
                self.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise  # see PriceMonitor._loop's identical comment
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during equity snapshot pass")

    def _open_lifecycles_by_account(self) -> dict[str, list[PositionLifecycle]]:
        by_account: dict[str, list[PositionLifecycle]] = {}
        for lifecycle in self.lifecycle_manager.list_open_lifecycles():
            by_account.setdefault(lifecycle.plan.account_id, []).append(lifecycle)
        return by_account

    def compute_snapshot(self, account_id: str, lifecycles: list[PositionLifecycle]) -> dict:
        """Compute (without persisting) exactly what `snapshot_once` would
        write for this one account right now -- exposed separately so a
        caller (or a test) can inspect the numbers before/without a write."""
        economics = compute_account_economics(self.store, account_id)
        last_price_by_symbol = {
            lifecycle.plan.symbol: lifecycle.last_observed_price
            for lifecycle in lifecycles
            if lifecycle.last_observed_price is not None
        }

        unrealized_pnl = 0.0
        unpriced_open_symbols: list[str] = []
        for symbol, se in economics.per_symbol.items():
            if se.open_quantity == 0:
                continue
            price = last_price_by_symbol.get(symbol)
            if price is None or se.average_cost is None:
                unpriced_open_symbols.append(symbol)
                continue
            unrealized_pnl += (price - se.average_cost) * se.open_quantity

        cumulative_pnl = economics.realized_pnl + unrealized_pnl
        return {
            "account_id": account_id,
            "captured_at": datetime.now(timezone.utc),
            "realized_pnl": economics.realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "cumulative_pnl": cumulative_pnl,
            "unpriced_open_symbols": unpriced_open_symbols,
        }

    def snapshot_once(self) -> int:
        """Persist one real equity/P&L snapshot for every configured
        account. Returns how many accounts were snapshotted."""
        lifecycles_by_account = self._open_lifecycles_by_account()

        count = 0
        for account in self.store.list_config_accounts():
            account_id = account["account_id"]
            snapshot = self.compute_snapshot(account_id, lifecycles_by_account.get(account_id, []))
            self.store.record_equity_snapshot(
                account_id,
                captured_at=snapshot["captured_at"],
                realized_pnl=snapshot["realized_pnl"],
                unrealized_pnl=snapshot["unrealized_pnl"],
                cumulative_pnl=snapshot["cumulative_pnl"],
                unpriced_open_symbols=snapshot["unpriced_open_symbols"],
            )
            count += 1
        return count
