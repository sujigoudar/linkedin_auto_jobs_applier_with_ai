"""TR-EPISODE-01: one authoritative trade-episode ledger, replayed from
this service's own confirmed execution journal (`SignalStore`'s `orders`
table, joined to `signals`) -- never a self-reported or invented number.

## Why this module exists

A release review found a material accounting gap: `app/provider_value.py`
(and, through it, `app/provider_scout.py`'s free-provider promotion scan)
computed provider performance from "closing fills in the ordinary order
history," but a managed-lifecycle position's stop-loss, profit-target, or
trailing exit did NOT reliably create a row in that same order history --
so a provider's winning conventional closes could be counted while losses
exited through lifecycle protection went missing from its scorecard. See
`app/lifecycle/manager.py`'s `PositionLifecycleManager._apply_exit_fill`/
`_persist_self_initiated_exit` (TR-EPISODE-01) for the companion fix that
makes those exits real `orders` rows in the first place -- this module is
what replays them, together with every other execution touching the same
position, into ONE record per position episode.

## What one `TradeEpisode` is

One independently-completed (or still-open) position lifecycle for one
`(account_id, symbol)`, from its first entry fill to (if it has one yet)
the fill that returned it to flat -- exactly `app/db.py`'s `orders.
family_id` grouping (see that column's own schema comment): every entry,
add-on, reduction, stop exit, target exit, time exit, and manual/provider
exit that shares one `family_id` is ONE episode, never several. This is
also what fixes the "misleading win rate" the review called out: a
position reduced across three partial-exit fills is one episode with one
net economic result, not three independent "trades."

## Scope this module is honest about

- **Grouping requires a real `family_id`.** Any filled order with
  `family_id IS NULL` (a pre-existing row saved before DB-0X's family
  tracking existed, or a plain, non-managed_lifecycle account's close --
  see `orders.family_id`'s own schema comment for exactly which closes
  have no real family to report) cannot be attributed to an episode and
  is excluded from every episode entirely -- listed by count in
  `SKIPPED_NO_FAMILY_ID` at the call site, never silently absorbed into
  some other episode.
- **A leg with no known fill price stays economically unknown.** A
  `stop_exit`/`target_exit`/`time_exit` row's `filled_price` can
  genuinely be `NULL` (see `_apply_exit_fill`'s own docstring for when --
  in particular, `resolve_pending_exit`'s partial/terminal reconciliation
  path has no real fill price available at that layer today). When ANY
  execution in an episode has an unknown price, `final_economic_result.
  realized_pnl` is `None` (never a partial sum that silently treats the
  unknown leg as contributing zero) and `has_unknown_price_execution` is
  `True` -- this episode's economic outcome is DISCLOSED as incomplete,
  not guessed at.
- **Fees, financing, and corporate actions are not tracked anywhere in
  this schema today** (`orders` has no fee/financing column; there is no
  corporate-actions table). Each is reported as the literal string
  `"unknown"` on every episode -- never `0`/`0.0`, which would silently
  claim a verified zero (see `app/economics.py`'s identical "gross of
  fees" disclosure for the same reasoning applied to account-level P&L).
- **Strategy and signal-revision are not tracked anywhere in this schema
  today** (there is no `Strategy` entity, and `Signal` is immutable --
  no revision concept exists to report). Both fields are always `None` on
  every episode, not a fabricated identifier.
- **Allocation** is the entry's own real requested/planned quantity --
  the SUM of every entry/add-on fill's `filled_quantity` for this family
  (this schema has no separate "planned size" persisted independently of
  what was actually filled for a plain account; a managed_lifecycle
  account's real plan size lives only in in-memory/lifecycle-state JSON,
  not in a queryable column here -- see this module's own module-level
  note on that gap).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.db import SignalStore

#: Recognized `orders.purpose` values this module classifies into a named
#: execution bucket on `TradeEpisode` -- see each field's own docstring.
#: Anything else (`None`, or a value this module doesn't yet recognize) is
#: counted in `TradeEpisode.unclassified_executions` instead of being
#: silently dropped or silently guessed into the wrong bucket.
_ENTRY_PURPOSE = "entry"
_MANUAL_OR_PROVIDER_CLOSE_PURPOSE = "close"
_STOP_EXIT_PURPOSE = "stop_exit"
_TARGET_EXIT_PURPOSE = "target_exit"
_TIME_EXIT_PURPOSE = "time_exit"


@dataclass
class EpisodeExecution:
    """One real, confirmed fill belonging to this episode."""

    order_id: int
    side: str
    quantity: float
    price: float | None  # None only when genuinely unknown -- see module docstring
    executed_at: str


@dataclass
class TradeEpisode:
    family_id: str
    account_id: str
    symbol: str
    asset_class: str

    # --- Attribution (review's required episode fields) ---
    provider: str  # `signals.source` of the entry signal that opened this episode
    analyst: str | None
    #: Not tracked anywhere in this codebase today -- see module docstring.
    #: Always `None`, never a fabricated identifier.
    strategy: None = None
    #: Not tracked anywhere in this codebase today (`Signal` is immutable,
    #: no revision concept exists) -- see module docstring. Always `None`.
    signal_revision: None = None
    #: The entry's own real total filled quantity (sum of every entry/
    #: add-on fill) -- see module docstring for why this, not a separately
    #: persisted "planned size," is what's reported.
    allocation: float = 0.0

    # --- Executions, grouped by economic role (the review's explicit ask:
    # stop/target/trailing exits must be first-class alongside entry/
    # add-on/reduction/manual, not invisible) ---
    entry_executions: list[EpisodeExecution] = field(default_factory=list)
    add_on_executions: list[EpisodeExecution] = field(default_factory=list)
    #: A manual (dashboard "Exit now"/"Flatten") or provider-driven CLOSE
    #: signal exit/reduction -- `orders.purpose == 'close'`. This bucket
    #: covers BOTH a full manual close and a partial provider-driven
    #: reduction; `app/db.py`'s schema has no further distinction between
    #: them for a managed_lifecycle account (see `orders.purpose`'s own
    #: schema comment).
    reduction_executions: list[EpisodeExecution] = field(default_factory=list)
    stop_executions: list[EpisodeExecution] = field(default_factory=list)
    target_executions: list[EpisodeExecution] = field(default_factory=list)
    #: A subset of `stop_executions` whose originating signal was tagged
    #: `reason == "trailing_stop"` (see `_apply_exit_fill`'s own
    #: `was_trailing` check) -- reported separately, additively, since a
    #: trailing exit IS a stop fill in this codebase's real execution
    #: model (a trailing policy only ever ratchets the SAME resting stop
    #: order tighter; it never submits its own distinct exit order type).
    #: Fabricating a separate "trailing order" that never existed would
    #: misrepresent this schema's real event stream.
    trailing_stop_executions: list[EpisodeExecution] = field(default_factory=list)
    time_exit_executions: list[EpisodeExecution] = field(default_factory=list)
    #: Any filled order for this family whose `purpose` this module
    #: doesn't recognize (e.g. a legacy/manually-edited row) -- counted,
    #: never mis-sorted into another bucket.
    unclassified_executions: list[EpisodeExecution] = field(default_factory=list)

    # --- Not tracked anywhere in this codebase today -- see module
    # docstring. Always the literal string "unknown", never 0/0.0. ---
    fees: str = "unknown"
    financing: str = "unknown"
    corporate_actions: str = "unknown"

    # --- Final economic result ---
    net_open_quantity: float = 0.0  # signed: 0 means this episode is closed (flat)
    realized_pnl: float | None = 0.0
    has_unknown_price_execution: bool = False
    #: Every reducing (opposite-side) execution across every bucket above,
    #: whether it fully or only partially closed this episode -- what the
    #: review's own "misleading win rate" complaint is about: this is
    #: exactly ONE number for however many separate reducing FILLS this
    #: episode actually had.
    reducing_execution_count: int = 0

    @property
    def is_closed(self) -> bool:
        return abs(self.net_open_quantity) <= 1e-9

    @property
    def is_winning_episode(self) -> bool:
        """True only for a CLOSED episode with a known, strictly positive
        net result -- None (not True/False) is the honest answer for a
        still-open episode or one with an unknown-price leg; see
        `outcome` for that trinary read."""
        return self.is_closed and self.realized_pnl is not None and self.realized_pnl > 0

    @property
    def outcome(self) -> str:
        """`"open"` (not yet flat), `"unknown"` (closed, but at least one
        leg's price was never known -- this episode's true result cannot
        be stated), `"win"`, `"loss"`, or `"breakeven"`. Never conflates
        "unknown" with "breakeven" or with "loss" -- see the review's own
        "unknown fees must remain unknown, not $0" principle applied here
        to P&L instead of fees."""
        if not self.is_closed:
            return "open"
        if self.realized_pnl is None:
            return "unknown"
        if self.realized_pnl > 0:
            return "win"
        if self.realized_pnl < 0:
            return "loss"
        return "breakeven"

    def to_dict(self) -> dict:
        def _execs(execs: list[EpisodeExecution]) -> list[dict]:
            return [
                {
                    "order_id": e.order_id,
                    "side": e.side,
                    "quantity": e.quantity,
                    "price": e.price,
                    "executed_at": e.executed_at,
                }
                for e in execs
            ]

        return {
            "family_id": self.family_id,
            "account_id": self.account_id,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "provider": self.provider,
            "analyst": self.analyst,
            "strategy": self.strategy,
            "signal_revision": self.signal_revision,
            "allocation": self.allocation,
            "entry_executions": _execs(self.entry_executions),
            "add_on_executions": _execs(self.add_on_executions),
            "reduction_executions": _execs(self.reduction_executions),
            "stop_executions": _execs(self.stop_executions),
            "target_executions": _execs(self.target_executions),
            "trailing_stop_executions": _execs(self.trailing_stop_executions),
            "time_exit_executions": _execs(self.time_exit_executions),
            "unclassified_executions": _execs(self.unclassified_executions),
            "fees": self.fees,
            "financing": self.financing,
            "corporate_actions": self.corporate_actions,
            "net_open_quantity": self.net_open_quantity,
            "is_closed": self.is_closed,
            "realized_pnl": self.realized_pnl,
            "has_unknown_price_execution": self.has_unknown_price_execution,
            "reducing_execution_count": self.reducing_execution_count,
            "outcome": self.outcome,
            "note": (
                "fees/financing/corporate_actions are 'unknown' (not tracked in this schema), "
                "never a fabricated 0. realized_pnl is None (not a partial sum) whenever any "
                "execution's price is genuinely unknown -- see has_unknown_price_execution."
            ),
        }


def _order_side_matches_entry(order_side: str, entry_side: str) -> bool:
    return order_side == entry_side


def compute_trade_episodes(store: SignalStore) -> tuple[dict[str, TradeEpisode], int]:
    """Replay every FILLED order across every account, chronologically,
    grouped by `orders.family_id` into one `TradeEpisode` per group.
    Returns `(episodes_by_family_id, skipped_no_family_id_count)` -- the
    second value is how many otherwise-real filled orders had no
    `family_id` to group by (see module docstring) and were excluded
    entirely, never guessed into an episode."""
    episodes: dict[str, TradeEpisode] = {}
    skipped_no_family_id = 0

    for order in store.list_filled_orders_with_signal_chronological():
        family_id = order.get("family_id")
        if not family_id:
            skipped_no_family_id += 1
            continue

        quantity = order["filled_quantity"]
        price = order["filled_price"]
        side = order["side"]
        if quantity is None or side not in ("buy", "sell") or not math.isfinite(quantity) or quantity <= 0:
            # Same "incomplete stays incomplete" rule as app/economics.py /
            # app/provider_value.py -- an order this replay can't use at
            # all is excluded, not treated as a zero-quantity execution.
            continue
        if price is not None and not math.isfinite(price):
            price = None

        episode = episodes.get(family_id)
        if episode is None:
            episode = TradeEpisode(
                family_id=family_id,
                account_id=order["account_id"],
                symbol=order["symbol"],
                asset_class=order["asset_class"],
                provider=order["source"],
                analyst=order["analyst"],
            )
            episodes[family_id] = episode

        execution = EpisodeExecution(
            order_id=order["order_id"], side=side, quantity=quantity, price=price, executed_at=order["executed_at"]
        )
        purpose = order.get("purpose")
        entry_side = episode.entry_executions[0].side if episode.entry_executions else None

        if purpose == _ENTRY_PURPOSE:
            if entry_side is None or _order_side_matches_entry(side, entry_side):
                if entry_side is None:
                    episode.entry_executions.append(execution)
                else:
                    episode.add_on_executions.append(execution)
                episode.allocation += quantity
            else:
                # A same-family 'entry'-purpose order on the OPPOSITE side
                # of the real entry shouldn't happen (an entry never
                # reverses direction under the same family_id in this
                # codebase's own contract) -- if it ever does, don't
                # mis-sort it as an entry; count it honestly instead.
                episode.unclassified_executions.append(execution)
        elif purpose == _MANUAL_OR_PROVIDER_CLOSE_PURPOSE:
            episode.reduction_executions.append(execution)
        elif purpose == _STOP_EXIT_PURPOSE:
            raw = _load_signal_raw(store, order.get("signal_id"))
            if raw.get("reason") == "trailing_stop":
                episode.trailing_stop_executions.append(execution)
            else:
                episode.stop_executions.append(execution)
        elif purpose == _TARGET_EXIT_PURPOSE:
            episode.target_executions.append(execution)
        elif purpose == _TIME_EXIT_PURPOSE:
            episode.time_exit_executions.append(execution)
        else:
            episode.unclassified_executions.append(execution)

    for episode in episodes.values():
        _finalize_episode(episode)

    return episodes, skipped_no_family_id


#: Small per-call cache so a family with several stop_exit legs doesn't
#: re-parse the same signal's `raw` JSON repeatedly within one replay.
def _load_signal_raw(store: SignalStore, signal_id: str | None) -> dict:
    if not signal_id:
        return {}
    cache = getattr(_load_signal_raw, "_cache", None)
    if cache is None:
        cache = {}
        _load_signal_raw._cache = cache  # type: ignore[attr-defined]
    if signal_id in cache:
        return cache[signal_id]
    raw = store.get_signal_raw(signal_id)
    cache[signal_id] = raw
    return raw


def _finalize_episode(episode: TradeEpisode) -> None:
    """Single volume-weighted-average-cost replay of this ONE family's own
    executions, in chronological order across every bucket -- the
    per-episode counterpart to app/economics.py's per-(account,symbol)
    replay, scoped to exactly the executions that belong to this one
    position lifecycle rather than every fill ever made in that
    account/symbol."""
    all_executions: list[tuple[str, EpisodeExecution]] = []
    for kind, execs in (
        ("entry", episode.entry_executions),
        ("add_on", episode.add_on_executions),
        ("reduction", episode.reduction_executions),
        ("stop", episode.stop_executions),
        ("target", episode.target_executions),
        ("trailing_stop", episode.trailing_stop_executions),
        ("time_exit", episode.time_exit_executions),
        ("unclassified", episode.unclassified_executions),
    ):
        for e in execs:
            all_executions.append((kind, e))
    all_executions.sort(key=lambda pair: (pair[1].executed_at, pair[1].order_id))

    if not episode.entry_executions and not episode.add_on_executions:
        # No real opening fill in this family at all (shouldn't happen for
        # a family this module itself created from an 'entry'-purpose
        # order, but stay honest rather than divide by an assumed side).
        episode.has_unknown_price_execution = True
        episode.realized_pnl = None
        return

    entry_side = episode.entry_executions[0].side if episode.entry_executions else episode.add_on_executions[0].side
    signed_open = 0.0
    average_cost: float | None = None
    realized = 0.0
    unknown_price_seen = False
    reducing_count = 0

    for _kind, execution in all_executions:
        is_same_side = execution.side == entry_side
        signed_qty = execution.quantity if is_same_side else -execution.quantity

        if is_same_side:
            if execution.price is None:
                unknown_price_seen = True
            else:
                new_open = signed_open + signed_qty
                existing_cost = (average_cost or 0.0) * abs(signed_open)
                average_cost = (existing_cost + execution.price * execution.quantity) / abs(new_open) if new_open else None
            signed_open += signed_qty
            continue

        # Reducing fill.
        reducing_count += 1
        closing_quantity = min(execution.quantity, abs(signed_open))
        if execution.price is None or average_cost is None:
            unknown_price_seen = True
        else:
            direction = 1 if signed_open > 0 else -1
            realized += (execution.price - average_cost) * direction * closing_quantity
        signed_open += signed_qty
        if abs(signed_open) <= 1e-9:
            signed_open = 0.0

    episode.net_open_quantity = signed_open
    episode.reducing_execution_count = reducing_count
    episode.has_unknown_price_execution = unknown_price_seen
    episode.realized_pnl = None if unknown_price_seen else realized
