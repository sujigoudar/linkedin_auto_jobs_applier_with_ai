"""Scheduled discovery of free signal providers worth promoting.

A background loop (started in app/main.py's `lifespan`, same pattern as
`OrderReconciler`/`PriceMonitor`) that periodically re-evaluates every
(source, analyst, asset_class) `app/provider_value.py` has real fill data
for and that is NOT currently a tracked `provider_subscriptions` row --
i.e. every "free" signal source this service happens to already be
executing trades from, whether or not the owner ever deliberately added
it as a paid or tracked provider. Nothing is auto-adopted or auto-routed:
this only writes a `provider_candidates` snapshot (recommendation +
evaluated_at) for `GET /providers/candidates` to show; promoting one into
a real `provider_subscriptions` row is a separate, explicit owner action
(`POST /providers/candidates/promote`, app/main.py).

Recommendation is the same three-way heuristic app/provider_value.py's
verdict logic uses, minus the cost/subscription half of it (there's no
subscription yet to weigh against):

- `insufficient_data` -- fewer than `PROVIDER_VALUE_MIN_SAMPLE_SIZE`
  DECIDED episodes so far (see `ProviderEpisodeValue.win_rate`'s own
  docstring for exactly what "decided" excludes). Not "bad," just not
  evaluated yet.
- `promote` -- win rate and profit factor both clear their configured
  thresholds.
- `not_promising` -- enough data, but the numbers don't clear the bar.

TR-EPISODE-01: this scan reads `app/provider_value.py`'s EPISODE-based
scoring (`compute_provider_value_from_episodes`), not the deprecated
closing-fill replay -- see that module's own docstring for the exact bias
this closes (a stop/target/time_exit exit is now a first-class,
episode-closing execution, not invisible to this scan; a position reduced
across several partial-exit fills is one episode observation, not
several).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.db import SignalStore
from app.provider_value import compute_provider_value_from_episodes

logger = logging.getLogger(__name__)


class ProviderScout:
    def __init__(
        self,
        store: SignalStore,
        *,
        interval_seconds: float = 86400.0,
        min_sample_size: int = 10,
        win_rate_threshold: float = 0.4,
        profit_factor_threshold: float = 1.0,
    ):
        self.store = store
        self.interval_seconds = interval_seconds
        self.min_sample_size = min_sample_size
        self.win_rate_threshold = win_rate_threshold
        self.profit_factor_threshold = profit_factor_threshold
        self._task: asyncio.Task | None = None
        #: Same contract as OrderReconciler.last_success_at/PriceMonitor's
        #: identical field -- surfaced by app/main.py's /health.
        self.last_success_at: datetime | None = None

    async def start(self) -> None:
        # OPS-02: idempotent, same fix/reasoning as OrderReconciler.start()
        # and PriceMonitor.start().
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
                self.scan_once()
                self.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise  # see PriceMonitor._loop's identical comment
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during provider scout pass")

    def scan_once(self) -> int:
        """Re-evaluate every non-subscribed (source, analyst, asset_class)
        once. Returns how many were newly recommended for promotion this
        pass (not the total number of candidates recorded)."""
        subscribed_sources = {row["provider_id"] for row in self.store.list_provider_subscriptions()}
        values = compute_provider_value_from_episodes(self.store)

        promoted = 0
        for pv in values.values():
            if pv.source in subscribed_sources:
                continue  # already a tracked provider -- not a "candidate" to scout

            decided_episodes = pv.winning_episodes + pv.losing_episodes
            if decided_episodes < self.min_sample_size:
                recommendation = "insufficient_data"
            elif (pv.win_rate or 0.0) >= self.win_rate_threshold and (
                pv.profit_factor is None or pv.profit_factor >= self.profit_factor_threshold
            ):
                recommendation = "promote"
            else:
                recommendation = "not_promising"

            # TR-EPISODE-01: `closing_fills`/`winning_closing_fills` are
            # this store method's pre-existing column names (see
            # app/db.py's `provider_candidates` schema) -- reusing them
            # for episode COUNTS (not fill counts) is an honest relabeling
            # at the call site, not a schema change; a future pass may
            # rename the columns themselves, but that's a migration this
            # slice doesn't need to make to close the scoring bias.
            self.store.upsert_provider_candidate(
                source=pv.source,
                analyst=pv.analyst,
                asset_class=pv.asset_class,
                closing_fills=decided_episodes + pv.breakeven_episodes,
                winning_closing_fills=pv.winning_episodes,
                realized_pnl=pv.realized_pnl,
                win_rate=pv.win_rate,
                profit_factor=pv.profit_factor,
                recommendation=recommendation,
            )
            if recommendation == "promote":
                promoted += 1

        return promoted
