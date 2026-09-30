"""TR-EPISODE-01 (P&L completeness): the extended, "full economic account
view" the private performance screen's own review flagged as missing --
`GET /accounts/{id}/economics` (app/economics.py) is "essentially realized/
gross" today. This module ADDS the fields the review asked for, wired from
real data already tracked elsewhere in this codebase (never a fabricated
number where a genuine source doesn't exist yet) -- it deliberately does
NOT modify app/economics.py itself (that module is under this repo's own
`.agent/autonomy.yaml` ask-first gate; this file follows the exact same
"build alongside, never recompute a second way" pattern app/equity_history.py
already established for the same reason).

## Field-by-field: what's real, and what's honestly "unavailable"

- **Realized**: `gross_realized` is `app/economics.py`'s own
  `realized_pnl`, taken verbatim (never recomputed a second way -- same
  invariant app/equity_history.py's own load-bearing test enforces).
  `fees`/`commissions`/`financing`/`borrow`/`funding`/`taxes` are all the
  literal string `"unknown"` -- `orders` has no fee column and this
  codebase has no financing/borrow/funding/tax data source at all (see
  app/economics.py's own "gross of fees" disclosure) -- never `0.0`,
  which would silently claim a verified zero. `net_realized` is
  correspondingly `None` (not `gross_realized` restated), since a "net"
  figure that ignores unknown costs would misstate itself as complete.
- **Unrealized**: reuses `app/equity_history.py`'s existing, real
  last-observed-price mechanism (`PositionLifecycle.last_observed_price`,
  itself fed only by a genuine entry fill or `PriceMonitor` tick) --
  never invents a mark. A symbol with no real price observation
  contributes `None` (not `0.0`) to `unrealized_gross`/`unrealized_net`
  and is named in `unavailable_marks`; `mark_age_seconds` is the real age
  of the most STALE contributing observation, `None` if every open symbol
  is unpriced. `unrealized_net` is `None` whenever ANY unknown-fee state
  would apply to it too (fees are unknown for unrealized costs exactly as
  for realized ones), so it is always `None` here (gross, only, is
  reported) until a real per-position cost basis for unrealized fees
  exists.
- **Account**: `nav`/`equity`/`cash`/`buying_power`/`margin_used` come
  from a caller-supplied, freshly-fetched `AccountBalance` (see
  `app/brokers/base.py`) when one is given -- a REAL broker read, never
  derived from this replay's own P&L (this module does no broker I/O
  itself, matching app/economics.py's own "reads what's given, never
  calls a broker" scope). `None` for any field the broker didn't report,
  or when no `AccountBalance` was supplied at all. `deposits_withdrawals`
  is always `"unknown"` -- this schema has no cash-transfer ledger.
- **Returns**: `twr` (time-weighted return) is always `None` --
  `account_equity_snapshots` (app/equity_history.py) persists
  `cumulative_pnl`, not a real starting-capital-anchored equity value (see
  that module's own "Honest labeling" section for why), so a genuine
  cashflow-adjusted TWR cannot be computed from what this schema actually
  stores. Reported as an explicit field so a caller sees the metric
  exists and is honestly unavailable, not silently missing.
- **Execution**: `slippage`/`implementation_shortfall` are computed from
  every filled order whose originating signal carried a real reference
  `price` (see `SignalStore.list_filled_orders_with_signal_reference_
  price`) -- `filled_price` vs that reference, signed so a POSITIVE value
  always means "worse than the provider's own reference price" for both
  sides. `sample_count` is how many fills actually had a reference price
  to compare against; a signal with no `price` at all contributes nothing
  (never treated as zero slippage). `spread_cost`/`latency` are `None`/
  reused from `app/execution_quality.py` respectively -- this schema has
  no bid/ask spread data at all (no quote book is stored), so spread_cost
  stays `None`; latency is that module's own real `SymbolLatency`
  aggregate, not recomputed here.
- **Attribution**: provider/analyst/asset_class come straight from
  `app/provider_value.py`'s existing per-(source, analyst, asset_class)
  breakdown (not duplicated here -- see `attribution_available`);
  strategy/regime are `None` (not tracked anywhere in this codebase --
  see app/trade_episode.py's identical disclosure for `strategy`).
- **Customer**: `model_vs_platform_vs_follower` is always the literal
  string `"not_applicable_in_signal_copier"` -- the SOURCE/MODEL/PLATFORM/
  FOLLOWER four-book distinction is signal-portfolio-commercial's own
  concept (see that package's ADR-0004); this service has no follower-
  account concept of its own to report against.
- **Commercial**: `subscription_revenue`/`business_costs` are always
  `"not_applicable_in_signal_copier"` -- this is the private, live-trading
  engine, not the commercial multi-tenant platform; billing lives entirely
  in signal-portfolio-commercial.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db import SignalStore
from app.economics import compute_account_economics
from app.execution_quality import compute_execution_quality
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AccountBalance


@dataclass
class SlippageStats:
    sample_count: int
    mean: float
    median: float
    #: Positive == worse than the provider's reference price, for both
    #: sides (see module docstring's signing convention).
    worst: float

    def to_dict(self) -> dict:
        return {"sample_count": self.sample_count, "mean": self.mean, "median": self.median, "worst": self.worst}


def _compute_slippage(store: SignalStore, account_id: str) -> SlippageStats | None:
    samples: list[float] = []
    for row in store.list_filled_orders_with_signal_reference_price(account_id):
        reference = row["signal_price"]
        filled_price = row["filled_price"]
        quantity = row["filled_quantity"]
        if reference is None or filled_price is None or quantity is None:
            continue  # no real reference to compare against -- excluded, never treated as zero
        if row["side"] == "buy":
            samples.append(filled_price - reference)
        elif row["side"] == "sell":
            samples.append(reference - filled_price)
        # Side.CLOSE (or anything unresolved) has no consistent sign convention -- excluded.
    if not samples:
        return None
    return SlippageStats(
        sample_count=len(samples), mean=statistics.mean(samples), median=statistics.median(samples), worst=max(samples)
    )


def _compute_unrealized(
    store: SignalStore, account_id: str, lifecycle_manager: PositionLifecycleManager | None
) -> tuple[float | None, list[str], float | None]:
    """Returns (unrealized_gross, unavailable_marks, mark_age_seconds) --
    reuses app/equity_history.py's own real last-observed-price mechanism,
    never a second, independent unrealized computation. `None` (not 0.0)
    when there is nothing open, or nothing open has a real mark."""
    economics = compute_account_economics(store, account_id)
    open_symbols = {symbol: se for symbol, se in economics.per_symbol.items() if se.open_quantity != 0}
    if not open_symbols:
        return None, [], None

    last_price_by_symbol: dict[str, tuple[float, datetime | None]] = {}
    if lifecycle_manager is not None:
        for lifecycle in lifecycle_manager.list_open_lifecycles():
            if lifecycle.last_observed_price is not None:
                last_price_by_symbol[lifecycle.plan.symbol] = (
                    lifecycle.last_observed_price,
                    lifecycle.last_observed_price_at,
                )

    unrealized_gross = 0.0
    unavailable_marks: list[str] = []
    oldest_observation: datetime | None = None
    any_priced = False
    for symbol, se in open_symbols.items():
        priced = last_price_by_symbol.get(symbol)
        if priced is None or se.average_cost is None:
            unavailable_marks.append(symbol)
            continue
        price, observed_at = priced
        unrealized_gross += (price - se.average_cost) * se.open_quantity
        any_priced = True
        if observed_at is not None and (oldest_observation is None or observed_at < oldest_observation):
            oldest_observation = observed_at

    if not any_priced:
        return None, unavailable_marks, None

    mark_age_seconds = None
    if oldest_observation is not None:
        mark_age_seconds = (datetime.now(timezone.utc) - oldest_observation).total_seconds()
    return unrealized_gross, unavailable_marks, mark_age_seconds


@dataclass
class ExtendedAccountEconomics:
    account_id: str

    # --- Realized ---
    gross_realized: float = 0.0
    fees: str = "unknown"
    commissions: str = "unknown"
    financing: str = "unknown"
    borrow: str = "unknown"
    funding: str = "unknown"
    taxes: str = "unknown"
    net_realized: None = None  # unknown costs above make a real net figure unstatable

    # --- Unrealized ---
    unrealized_gross: float | None = None
    unrealized_net: None = None  # see module docstring: always None until unrealized fees are tracked
    unavailable_marks: list[str] = field(default_factory=list)
    mark_age_seconds: float | None = None

    # --- Account ---
    nav: float | None = None
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    margin_used: float | None = None
    deposits_withdrawals: str = "unknown"
    account_data_source: str = "unavailable"  # "broker_reported" once a real AccountBalance was supplied

    # --- Returns ---
    twr: None = None
    twr_unavailable_reason: str = (
        "no persisted real equity time series with a known starting balance exists in this "
        "schema (account_equity_snapshots stores cumulative_pnl, not a cashflow-adjusted equity "
        "curve) -- see app/equity_history.py's own 'Honest labeling' section"
    )

    # --- Execution ---
    slippage: SlippageStats | None = None
    implementation_shortfall: SlippageStats | None = None  # identical basis to slippage in this schema; see docstring
    spread_cost: None = None
    latency_seconds_mean: float | None = None
    latency_sample_count: int = 0

    # --- Customer / Commercial ---
    model_vs_platform_vs_follower: str = "not_applicable_in_signal_copier"
    subscription_revenue: str = "not_applicable_in_signal_copier"
    business_costs: str = "not_applicable_in_signal_copier"

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "realized": {
                "gross_realized": self.gross_realized,
                "fees": self.fees,
                "commissions": self.commissions,
                "financing": self.financing,
                "borrow": self.borrow,
                "funding": self.funding,
                "taxes": self.taxes,
                "net_realized": self.net_realized,
            },
            "unrealized": {
                "unrealized_gross": self.unrealized_gross,
                "unrealized_net": self.unrealized_net,
                "unavailable_marks": self.unavailable_marks,
                "mark_age_seconds": self.mark_age_seconds,
            },
            "account": {
                "nav": self.nav,
                "equity": self.equity,
                "cash": self.cash,
                "buying_power": self.buying_power,
                "margin_used": self.margin_used,
                "deposits_withdrawals": self.deposits_withdrawals,
                "data_source": self.account_data_source,
            },
            "returns": {"twr": self.twr, "twr_unavailable_reason": self.twr_unavailable_reason},
            "execution": {
                "slippage": self.slippage.to_dict() if self.slippage else None,
                "implementation_shortfall": (
                    self.implementation_shortfall.to_dict() if self.implementation_shortfall else None
                ),
                "spread_cost": self.spread_cost,
                "latency_seconds_mean": self.latency_seconds_mean,
                "latency_sample_count": self.latency_sample_count,
            },
            "customer": {"model_vs_platform_vs_follower": self.model_vs_platform_vs_follower},
            "commercial": {
                "subscription_revenue": self.subscription_revenue,
                "business_costs": self.business_costs,
            },
            "note": (
                "Extension of GET /accounts/{id}/economics (app/economics.py), added alongside it "
                "-- never a second, independent realized-P&L computation. Every 'unknown'/None "
                "field is a genuine, disclosed gap in this schema's own tracked data, not a "
                "fabricated zero -- see app/account_economics_v2.py's module docstring."
            ),
        }


def compute_extended_account_economics(
    store: SignalStore,
    account_id: str,
    *,
    lifecycle_manager: PositionLifecycleManager | None = None,
    broker_balance: AccountBalance | None = None,
) -> ExtendedAccountEconomics:
    """Build the extended P&L view for one account. `lifecycle_manager`
    (optional) is used only to read real last-observed prices for
    unrealized P&L, exactly as app/equity_history.py's own
    `EquitySnapshotter` does -- omit it and `unrealized_gross` is honestly
    `None` for every open symbol. `broker_balance` (optional) is a
    FRESHLY fetched, real `AccountBalance` from the account's own broker
    adapter (this module never calls a broker itself) -- omit it and every
    Account-family field stays `None`/`"unavailable"`."""
    economics = compute_account_economics(store, account_id)
    result = ExtendedAccountEconomics(account_id=account_id, gross_realized=economics.realized_pnl)

    unrealized_gross, unavailable_marks, mark_age_seconds = _compute_unrealized(store, account_id, lifecycle_manager)
    result.unrealized_gross = unrealized_gross
    result.unavailable_marks = unavailable_marks
    result.mark_age_seconds = mark_age_seconds

    if broker_balance is not None:
        result.nav = broker_balance.equity
        result.equity = broker_balance.equity
        result.cash = broker_balance.cash
        result.buying_power = broker_balance.buying_power
        result.margin_used = broker_balance.maintenance_margin
        result.account_data_source = "broker_reported"

    slippage = _compute_slippage(store, account_id)
    result.slippage = slippage
    result.implementation_shortfall = slippage

    # E05/PU-A2: reuse app/execution_quality.py's own real signal-to-fill
    # latency aggregate -- never recomputed a second way. Averaged across
    # symbols (unweighted by sample count) into one account-level figure;
    # per-symbol detail is already available from that module directly.
    quality = compute_execution_quality(store, account_id)
    if quality.per_symbol:
        per_symbol_means = [s.mean_seconds for s in quality.per_symbol.values()]
        result.latency_seconds_mean = statistics.mean(per_symbol_means)
        result.latency_sample_count = sum(s.sample_count for s in quality.per_symbol.values())

    return result
