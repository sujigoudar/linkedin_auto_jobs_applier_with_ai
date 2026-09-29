"""Signal-provider value analysis: realized P&L, win rate, and profit
factor attributed to each (source, analyst, asset_class) that has sent a
BUY/SELL signal, replayed from this service's own confirmed execution
journal (`SignalStore`'s `orders` table) -- never a self-reported or
invented number. Also combines this with `provider_subscriptions` cost
data into a "still worth paying for" recommendation. See
app/provider_scout.py for the analogous "is this FREE provider worth
promoting" scan.

## Attribution method: FIFO lots, not this project's account-level
volume-weighted average

app/economics.py's account-level economics blends every entry into one
running average cost per (account, symbol) -- the right model for "what
does this account currently hold and at what basis," but it can't answer
"was PROVIDER X's advice profitable" once a SECOND provider's signal adds
to (or a resolved close from either reduces) the SAME account/symbol
position: a blended average erases which provider's entry contributed
what.

This module instead replays every FILLED order GLOBALLY (across every
account) in chronological order and, per (account_id, symbol), keeps an
ordered list of open "lots" -- one per same-side entry fill, each tagged
with the provider (source, analyst) and asset_class of the SIGNAL that
originated it. A reducing (opposite-side) fill consumes those lots
FIFO -- oldest entry first -- realizing P&L per lot against that lot's
own entry price, credited to THAT lot's provider, regardless of which
provider's signal (if any) triggered the close itself. A remainder left
over after every existing lot is consumed (a flip through flat) opens a
fresh lot in the new direction, attributed to THIS fill's own signal --
it's a genuinely new entry, not a continuation of the old one. This
correctly separates two providers sharing one account/symbol; this
project's account-level economics.py cannot.

## What this does NOT cover (materially important -- read before acting
on a "cancel this provider" recommendation)

- **A managed-lifecycle position's stop-loss, profit-target, or trailing
  exit fill is NOT in this replay.** Those are applied directly to
  `SignalStore`'s `positions`/`lifecycle_state` (see
  `PositionLifecycleManager.on_stop_filled`/`resolve_pending_exit`) and
  never create an `orders` table row at all -- the exact same limitation
  app/economics.py's account-level P&L already has, just newly material
  here: a provider whose entries are mostly protected by a stop (rather
  than an explicit provider CLOSE signal) will have most of its LOSSES
  invisible to this calculation, while its wins (an explicit CLOSE, or a
  dashboard manual exit, which DO create an order row) still count. A
  provider that looks "surprisingly good" here may just be one whose bad
  trades are being caught by stops this replay can't see -- corroborate
  against `GET /positions`' `managed_lifecycles` detail before cancelling
  or promoting anything on this alone.
- Gross of fees/subscription-adjacent costs beyond the tracked
  subscription itself (`orders` has no fee column -- same disclosed gap
  as app/economics.py).
- No live unrealized P&L -- only a CLOSED (reducing) fill realizes
  anything counted here.
- A signal with no `analyst` is grouped as `analyst=None` -- "the whole
  provider," never folded into some other analyst's numbers.
- The verdict thresholds (`PROVIDER_VALUE_MIN_SAMPLE_SIZE`/
  `_WIN_RATE_THRESHOLD`/`_PROFIT_FACTOR_THRESHOLD`) are a disclosed
  heuristic, not a claim of statistical significance -- a small sample
  passing them is "not yet proven bad," not "proven good."
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app import config
from app.db import SignalStore


@dataclass
class _Lot:
    quantity: float  # always positive; remaining
    entry_price: float
    source: str
    analyst: str | None


ProviderKey = tuple[str, str | None, str]  # (source, analyst, asset_class)


@dataclass
class ProviderValue:
    source: str
    analyst: str | None
    asset_class: str
    realized_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0  # stored as a positive number
    closing_fills: int = 0
    winning_closing_fills: int = 0
    entries_opened: int = 0
    last_activity_at: str | None = None

    @property
    def win_rate(self) -> float | None:
        if self.closing_fills == 0:
            return None
        return self.winning_closing_fills / self.closing_fills

    @property
    def profit_factor(self) -> float | None:
        """gross_profit / gross_loss -- None (not an invented infinity)
        when there's no losing trade to divide by yet."""
        if self.gross_loss == 0:
            return None
        return self.gross_profit / self.gross_loss

    def to_dict(self) -> dict:
        losing_closing_fills = self.closing_fills - self.winning_closing_fills
        return {
            "source": self.source,
            "analyst": self.analyst,
            "asset_class": self.asset_class,
            "realized_pnl": self.realized_pnl,
            # TR-09 (provider scorecard "average win/loss"): these two were
            # already tracked on this dataclass (accumulated in
            # `compute_provider_value` above) but never projected out of
            # `to_dict` -- exposing them lets a caller compute a real
            # avg_win/avg_loss (gross_profit / winning_closing_fills,
            # gross_loss / losing_closing_fills) without back-solving them
            # imprecisely from win_rate/profit_factor/realized_pnl alone.
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "closing_fills": self.closing_fills,
            "winning_closing_fills": self.winning_closing_fills,
            "losing_closing_fills": losing_closing_fills,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "entries_opened": self.entries_opened,
            "last_activity_at": self.last_activity_at,
        }


def compute_provider_value(store: SignalStore) -> dict[ProviderKey, ProviderValue]:
    """Replay every confirmed fill across every account and return
    per-(source, analyst, asset_class) attribution. See module docstring
    for the FIFO methodology and its disclosed scope limits."""
    # One FIFO lot queue per (account_id, symbol); a position's current
    # direction ('buy' meaning net long, 'sell' meaning net short) is
    # implicit in which side originally opened its still-open lots.
    positions: dict[tuple[str, str], tuple[str | None, list[_Lot]]] = {}
    results: dict[ProviderKey, ProviderValue] = {}

    def _value_for(source: str, analyst: str | None, asset_class: str) -> ProviderValue:
        key = (source, analyst, asset_class)
        pv = results.get(key)
        if pv is None:
            pv = ProviderValue(source=source, analyst=analyst, asset_class=asset_class)
            results[key] = pv
        return pv

    for order in store.list_filled_orders_with_signal_chronological():
        symbol = order["symbol"]
        side = order["side"]
        quantity = order["filled_quantity"]
        price = order["filled_price"]
        source = order["source"]
        analyst = order["analyst"]
        asset_class = order["asset_class"]
        executed_at = order["executed_at"]

        if (
            symbol is None
            or side not in ("buy", "sell")
            or quantity is None
            or price is None
            or not math.isfinite(quantity)
            or not math.isfinite(price)
            or quantity <= 0
        ):
            continue  # unresolved/invalid fill -- same "incomplete stays incomplete" rule as app/economics.py

        pos_key = (order["account_id"], symbol)
        direction, lots = positions.get(pos_key, (None, []))

        if direction is None or direction == side:
            # Opening or adding to a position on the same side: a brand
            # new lot, attributed to this fill's own signal.
            lots.append(_Lot(quantity=quantity, entry_price=price, source=source, analyst=analyst))
            positions[pos_key] = (side, lots)
            pv = _value_for(source, analyst, asset_class)
            pv.entries_opened += 1
            pv.last_activity_at = max(pv.last_activity_at or "", executed_at)
            continue

        # Opposite side: reduce (and possibly flip through) the existing
        # lots, FIFO -- oldest entry first.
        remaining_to_close = quantity
        while remaining_to_close > 1e-9 and lots:
            lot = lots[0]
            consumed = min(lot.quantity, remaining_to_close)
            realized = (price - lot.entry_price) * consumed if direction == "buy" else (lot.entry_price - price) * consumed
            pv = _value_for(lot.source, lot.analyst, asset_class)
            pv.realized_pnl += realized
            pv.closing_fills += 1
            pv.last_activity_at = max(pv.last_activity_at or "", executed_at)
            if realized > 0:
                pv.winning_closing_fills += 1
                pv.gross_profit += realized
            elif realized < 0:
                pv.gross_loss += -realized
            lot.quantity -= consumed
            remaining_to_close -= consumed
            if lot.quantity <= 1e-9:
                lots.pop(0)

        if remaining_to_close > 1e-9:
            # Flipped through flat -- the remainder is a fresh position in
            # the NEW direction, attributed to THIS fill's own signal.
            lots.append(_Lot(quantity=remaining_to_close, entry_price=price, source=source, analyst=analyst))
            positions[pos_key] = (side, lots)
            pv = _value_for(source, analyst, asset_class)
            pv.entries_opened += 1
            pv.last_activity_at = max(pv.last_activity_at or "", executed_at)
        elif not lots:
            positions[pos_key] = (None, lots)
        else:
            positions[pos_key] = (direction, lots)

    return results


#: A verdict is always exactly one of these, in priority order (the first
#: one that applies wins). See ProviderValueReport's docstring for what
#: each means.
VERDICT_INSUFFICIENT_DATA = "insufficient_data"
VERDICT_CANCEL_CANDIDATE = "cancel_candidate"
VERDICT_UNDERPERFORMING_FREE = "underperforming_free"
VERDICT_KEEP = "keep"


def _cycles_elapsed(subscribed_since: str, billing_cycle: str, *, now: date | None = None) -> int:
    """A deliberately coarse "roughly how many billing periods have
    elapsed since tracking started" estimate -- NOT proration-exact (it
    doesn't account for the specific day-of-month/year your actual
    billing anchors on), good enough for a "is this worth it" signal, not
    for accounting or invoicing. At least 1 once `subscribed_since` has
    arrived, so a brand-new subscription's first cycle is never reported
    as costing nothing yet."""
    since = date.fromisoformat(subscribed_since)
    today = now or datetime.now(timezone.utc).date()
    if today < since:
        return 0
    if billing_cycle == "monthly":
        months = (today.year - since.year) * 12 + (today.month - since.month)
        return max(1, months + 1)
    if billing_cycle == "annual":
        years = today.year - since.year
        return max(1, years + 1)
    if billing_cycle == "one_time":
        return 1
    if billing_cycle == "free":
        return 0
    return 1


def _verdict(
    pv_totals: ProviderValue,
    subscription: dict | None,
    *,
    min_sample_size: int,
    win_rate_threshold: float,
    profit_factor_threshold: float,
) -> tuple[str, dict]:
    detail: dict = {}
    if pv_totals.closing_fills < min_sample_size:
        return VERDICT_INSUFFICIENT_DATA, detail

    win_rate = pv_totals.win_rate or 0.0
    profit_factor = pv_totals.profit_factor
    underperforming = win_rate < win_rate_threshold and (profit_factor is not None and profit_factor < profit_factor_threshold)

    cost_amount = (subscription or {}).get("cost_amount") or 0.0
    if subscription is not None and cost_amount > 0:
        cycles = _cycles_elapsed(subscription["subscribed_since"], subscription["billing_cycle"])
        cost_to_date = cost_amount * cycles
        net_value = pv_totals.realized_pnl - cost_to_date
        detail = {"cost_to_date": cost_to_date, "billing_cycles_elapsed": cycles, "net_value": net_value}
        if underperforming and net_value < 0:
            return VERDICT_CANCEL_CANDIDATE, detail
        return VERDICT_KEEP, detail

    # Free (no subscription row, or one with cost_amount <= 0): there's no
    # $ to "cancel," but the performance signal is still worth surfacing.
    if underperforming:
        return VERDICT_UNDERPERFORMING_FREE, detail
    return VERDICT_KEEP, detail


def compute_provider_value_report(
    store: SignalStore,
    *,
    source: str | None = None,
    analyst: str | None = None,
    asset_class: str | None = None,
) -> list[dict]:
    """The full, filterable, dashboard-facing report: every (source,
    analyst, asset_class) group's `ProviderValue`, plus (when a
    `provider_subscriptions` row exists for that `source`) its cost data
    and a verdict. Filters are an exact-match AND across whichever of
    `source`/`analyst`/`asset_class` are given -- `analyst=""` explicitly
    means "no analyst on the signal" (matches `analyst=None` groups), not
    "any analyst"."""
    values = compute_provider_value(store)
    subscriptions = {row["provider_id"]: row for row in store.list_provider_subscriptions()}

    # Verdicts are computed once per SOURCE (a subscription's cost applies
    # to the whole provider, not one asset_class slice of it) from that
    # source's totals across every analyst/asset_class, then attached to
    # every one of that source's rows so the breakdown table and the
    # cost/verdict summary can share one response.
    totals_by_source: dict[str, ProviderValue] = {}
    for pv in values.values():
        totals = totals_by_source.setdefault(pv.source, ProviderValue(source=pv.source, analyst=None, asset_class=""))
        totals.realized_pnl += pv.realized_pnl
        totals.gross_profit += pv.gross_profit
        totals.gross_loss += pv.gross_loss
        totals.closing_fills += pv.closing_fills
        totals.winning_closing_fills += pv.winning_closing_fills
        totals.entries_opened += pv.entries_opened
        totals.last_activity_at = max(totals.last_activity_at or "", pv.last_activity_at or "")

    verdicts: dict[str, tuple[str, dict]] = {
        src: _verdict(
            totals,
            subscriptions.get(src),
            min_sample_size=config.PROVIDER_VALUE_MIN_SAMPLE_SIZE,
            win_rate_threshold=config.PROVIDER_VALUE_WIN_RATE_THRESHOLD,
            profit_factor_threshold=config.PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD,
        )
        for src, totals in totals_by_source.items()
    }

    report = []
    for pv in values.values():
        if source is not None and pv.source != source:
            continue
        if analyst is not None and (pv.analyst or "") != analyst:
            continue
        if asset_class is not None and pv.asset_class != asset_class:
            continue
        verdict, verdict_detail = verdicts.get(pv.source, (VERDICT_INSUFFICIENT_DATA, {}))
        entry = pv.to_dict()
        entry["subscription"] = subscriptions.get(pv.source)
        entry["provider_verdict"] = verdict
        entry["provider_verdict_detail"] = verdict_detail
        report.append(entry)
    return report
