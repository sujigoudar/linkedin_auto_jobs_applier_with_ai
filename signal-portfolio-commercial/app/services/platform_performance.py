"""AD-XX "Owner trading performance" (not yet a numbered screen -- see
this module's own docstring on scope) -- real, per-instrument realized
P&L for any ONE book at a time (`Book.PLATFORM`, `Book.MODEL` or
`Book.FOLLOWER`), per INTEGRATION_DECISION.md S12 step 4: "Complete
scoped financial/provider metrics, reports, correction propagation and
the actual owner navigation."

`compute_book_performance` is the real, book-agnostic function;
`compute_platform_performance` is a thin, backward-compatible wrapper
fixed to `Book.PLATFORM` (this module's original, still most common,
caller). INTEGRATION_ACCEPTANCE_CASES.json INT-015 "Different model
and follower executions": model and follower net P&L for the same
instrument must never be summed or overwritten into each other --
holds by construction here since each call is scoped to exactly one
book's own rows (`LedgerEntry.book == book`), and the caller decides
which book's report it's asking for; nothing in this module ever
mixes two books' entries into one running total.

Ports signal-copier's own `app/economics.py` volume-weighted-average-
cost realized-P&L replay (the same algorithm, same "opening/adding to
a position only moves average cost; the opposite side realizes P&L on
whatever it closes, using the average cost at that moment, and opens a
fresh position on a flip through flat" logic) to this app's own
`LedgerEntry` rows -- reimplemented here (not imported) because the two
ledgers are shaped differently: `Decimal` money (never `float`, per
this app's own "Use Decimal or integer... do not calculate exact money
in floating point"), a `multiplier` column signal-copier's own ledger
has no equivalent of, and no `Side.CLOSE` (every commercial LedgerEntry
is already a resolved BUY/SELL).

Realized P&L itself is GROSS, same as signal-copier's own module: fee
data arrives per entry and, per S7 ("Importing a zero default is not
proof of a verified fee"), `fee: None` means "not yet known", never
zero -- summing an unknown fee into a running total would silently
treat it as zero. `unknown_fee_entry_count` reports how many entries in
each instrument's own history still have no fee recorded; `net_pnl` is
computed (`realized_pnl` minus every CLOSING entry's own effective fee)
only when that count is 0 for the instrument, and stays `None`
otherwise -- INTEGRATION_ACCEPTANCE_CASES.json INT-012 "Late fee
revises net report": "Gross P&L100; net initially unavailable... After
confirmed fees, net98", never a net figure quietly computed from a
gross assumption, and never "Net100" before every fee is confirmed.

Fee corrections (INT-012, `app/services/ledger.py`'s own
`append_correction`, applied by `app/services/integration_inbox.py`'s
own `EventType.FEE` handling): a correction row is never itself
replayed as a second, separate trade -- this module first builds a
`correction_of -> latest correction` map, replays only the root entries
(`correction_of IS NULL`), and uses each root's OWN effective fee (its
correction's fee, if one exists, else its own) for the fee math above.
A root entry's `quantity`/`price`/`side` never change via a fee
correction (`append_correction`'s own contract), so only which `fee`
value counts changes -- the realized-P&L replay itself (quantity,
average cost, closing/opening logic) is entirely unaffected by whether
a fee correction exists. Re-ingesting the identical `FEE` event twice
is a no-op at the inbox layer (the same `event_id`/`payload_hash`
redelivery-dedup every event type gets), so a duplicate fee correction
never appends a second correction row and never lowers `net_pnl` twice.

Does NOT compute win rates or episode counts itself (see
`app/services/analyst_attribution.py`'s own FIFO-lot replay, which
does, ported the same way from signal-copier's own `SymbolEconomics`)
-- out of scope for this module's own per-instrument average-cost
replay.

`load_ordered_root_entries` and `apply_entry` below are this module's
own replay steps, factored out so a caller needing something this
module's own aggregate return value doesn't expose (e.g.
`app/services/customer_performance_report.py`'s own chronological
cumulative-realized-P&L series for a real max-drawdown walk) can
REPLAY the exact same real query + correction-folding + volume-
weighted-average-cost position math this module's own
`compute_book_performance` runs, rather than a second, potentially-
diverging implementation of the same logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import Book, LedgerEntry, Side


@dataclass(frozen=True)
class EffectiveFill:
    """The REAL economic fields one root entry's replay should use --
    Track 41: a correction is no longer only ever a `fee` revision
    (`EventType.FEE`'s own original, still most common, use); it can now
    also be a genuine `SOURCE_RECEIPT` revision/EDIT (`app/services/
    integration_inbox.py`'s own `EventType.SOURCE_RECEIPT` handling)
    that revises `instrument`/`side`/`quantity`/`price`/`multiplier` too.
    `effective_fill` below is the one place that decides, for any one
    root entry, which values (its own, or its latest correction's) the
    replay actually uses -- every reader of `LedgerEntry` history
    (`compute_book_performance`, `compute_analyst_attribution`,
    `customer_performance_report.compute_customer_equity_series`) calls
    it instead of reading `entry.instrument`/`.side`/`.quantity`/
    `.price`/`.multiplier`/`.fee` directly, so none of them can silently
    diverge on which one wins.

    `event_time` is always the ROOT's own, never the correction's --
    a correction never moves WHEN, in the replay's chronological
    order, this fact is recognized (that would reorder it relative to
    every OTHER entry already replayed), only WHAT its true values
    are."""

    instrument: str
    side: Side
    quantity: Decimal
    price: Decimal
    multiplier: Decimal
    fee: Decimal | None
    event_time: datetime


def effective_fill(entry: LedgerEntry, correction: LedgerEntry | None) -> EffectiveFill:
    """`correction` is `latest_correction_by_original.get(entry.entry_id)`
    (see `load_ordered_root_entries`) -- `None` means this root entry has
    never been corrected, in which case its own fields are the real,
    effective ones. When a correction exists, EVERY one of `instrument`/
    `side`/`quantity`/`price`/`multiplier`/`fee` comes from the
    correction, not the root -- `append_correction`'s own contract
    (`app/services/ledger.py`) is that a correction row IS the fully
    resolved new state of every one of these fields (inheriting the
    root's own value for any field its caller didn't explicitly
    override), never a partial patch a reader has to merge field-by-
    field itself."""
    if correction is None:
        return EffectiveFill(
            instrument=entry.instrument, side=entry.side, quantity=entry.quantity, price=entry.price,
            multiplier=entry.multiplier, fee=entry.fee, event_time=entry.event_time,
        )
    return EffectiveFill(
        instrument=correction.instrument, side=correction.side, quantity=correction.quantity,
        price=correction.price, multiplier=correction.multiplier, fee=correction.fee, event_time=entry.event_time,
    )


@dataclass
class InstrumentPerformance:
    instrument: str
    realized_pnl: Decimal = Decimal(0)
    open_quantity: Decimal = Decimal(0)  # signed: positive = net long, negative = net short
    average_cost: Decimal | None = None
    last_fill_price: Decimal | None = None
    closing_fills: int = 0
    #: How many entries contributing to THIS instrument's history (opening
    #: or closing) had no CONFIRMED fee (no fee correction and the root
    #: entry's own `fee is None`) -- 0 means every entry's fee is
    #: genuinely known (even if some were verified-zero), which is
    #: exactly the condition under which `net_pnl` below is populated.
    unknown_fee_entry_count: int = 0
    #: Sum of every contributing entry's own effective (corrected, if a
    #: correction exists) fee -- accumulated regardless of
    #: `unknown_fee_entry_count`, but only meaningful (used to compute
    #: `net_pnl`) once that count reaches 0.
    total_fees: Decimal = Decimal(0)
    #: `realized_pnl - total_fees`, but ONLY once every contributing
    #: entry's fee is confirmed (`unknown_fee_entry_count == 0`) --
    #: `None` otherwise. INT-012: never a net figure silently computed
    #: from an unconfirmed-zero assumption.
    net_pnl: Decimal | None = None


@dataclass
class PlatformPerformanceReport:
    realized_pnl: Decimal = Decimal(0)
    per_instrument: dict[str, InstrumentPerformance] = field(default_factory=dict)

    @property
    def net_pnl(self) -> Decimal | None:
        """Sum of every instrument's own `net_pnl` -- `None` (not a
        partial sum) if ANY instrument's own net is still unavailable,
        for the same reason a per-instrument net is `None` until every
        contributing fee is confirmed."""
        total = Decimal(0)
        for ip in self.per_instrument.values():
            if ip.net_pnl is None:
                return None
            total += ip.net_pnl
        return total


def compute_platform_performance(session: Session, *, tenant_id: str) -> PlatformPerformanceReport:
    """Backward-compatible alias for `compute_book_performance(...,
    book=Book.PLATFORM)` -- this module's original, still most common,
    entry point (app/api/dashboard_routes.py's own
    GET /api/v1/ops/platform-performance)."""
    return compute_book_performance(session, tenant_id=tenant_id, book=Book.PLATFORM)


def load_ordered_root_entries(
    session: Session,
    *,
    tenant_id: str,
    book: Book,
    follower_connection_ids: frozenset[str] | None = None,
) -> tuple[list[LedgerEntry], dict[str, LedgerEntry]]:
    """The real, timestamp-ordered, correction-folded query this
    module's own `compute_book_performance` replays -- factored out so
    another caller needing the SAME real entries in the SAME order (not
    a second, potentially-diverging query) can replay them for its own
    purpose (e.g. a chronological cumulative-P&L series).

    Returns `(root_entries, latest_correction_by_original)`:
    `root_entries` excludes correction rows themselves (`correction_of
    IS NULL`), in real `(event_time, created_at)` order; the map is
    keyed by the corrected entry's own `entry_id` and gives, for any
    root entry that has one, its most-recently-created correction row
    (never summed -- see this module's own docstring on why).

    `follower_connection_ids`, when given, additionally restricts the
    query to `LedgerEntry.follower_connection_id` in that set -- only
    meaningful (and only ever passed) for `book == Book.FOLLOWER`: a
    tenant's own `Book.FOLLOWER` rows span every one of its customers'
    own connections, so a caller reporting ONE customer's own reconciled
    result (CU-06 "Performance and costs") must narrow to that
    customer's own connection ids first, or it would silently mix
    another customer's executions into this one's report. `None` (the
    default) keeps this function's original tenant-wide behavior for
    every existing caller."""
    filters = [LedgerEntry.tenant_id == tenant_id, LedgerEntry.book == book]
    if follower_connection_ids is not None:
        filters.append(LedgerEntry.follower_connection_id.in_(follower_connection_ids))
    all_entries = list(
        session.scalars(select(LedgerEntry).where(*filters).order_by(LedgerEntry.event_time, LedgerEntry.created_at)).all()
    )

    # Fold fee corrections into their root entry rather than replaying
    # them as a second, separate trade: a correction never changes
    # quantity/price/side (`append_correction`'s own contract), so all
    # it can honestly contribute here is a more-confirmed `fee`. If more
    # than one correction ever exists for the same root (not produced by
    # any caller in this build today), the most recently created one
    # wins -- never summed, which would double-count a fee amount that
    # was itself being corrected.
    latest_correction_by_original: dict[str, LedgerEntry] = {}
    for entry in all_entries:
        if entry.correction_of is None:
            continue
        existing = latest_correction_by_original.get(entry.correction_of)
        if existing is None or entry.created_at > existing.created_at:
            latest_correction_by_original[entry.correction_of] = entry
    entries = [entry for entry in all_entries if entry.correction_of is None]

    return entries, latest_correction_by_original


def apply_entry(ip: InstrumentPerformance, fill: EffectiveFill) -> Decimal:
    """The real volume-weighted-average-cost position math for exactly
    ONE entry's effective fill against ONE instrument's own running
    `InstrumentPerformance` state -- this module's single, real replay
    step, called by `compute_book_performance` below and by any other
    caller (e.g. a chronological equity-series builder) that needs the
    same real position math applied entry-by-entry rather than
    reimplemented. Takes an `EffectiveFill` (Track 41: not a raw
    `LedgerEntry` -- see that type's own docstring for why: a caller
    must resolve which values, root or correction, are REAL first) so
    this function itself never has to re-decide that. Returns the
    realized P&L delta THIS fill contributed (`Decimal(0)` for an
    opening/adding entry that realizes nothing yet). Mutates `ip` in
    place; does not touch fee bookkeeping or `report.realized_pnl` --
    a caller does that itself, same as `compute_book_performance` does
    below."""
    signed_qty = fill.quantity if fill.side == Side.BUY else -fill.quantity

    if ip.open_quantity == 0 or (ip.open_quantity > 0) == (signed_qty > 0):
        # Opening or adding to a position on the same side: only the
        # volume-weighted average cost moves, nothing is realized yet.
        new_quantity = ip.open_quantity + signed_qty
        existing_cost = (ip.average_cost or Decimal(0)) * abs(ip.open_quantity)
        ip.average_cost = (existing_cost + fill.price * abs(signed_qty)) / abs(new_quantity)
        ip.open_quantity = new_quantity
        return Decimal(0)

    # Opposite side: this entry reduces (and possibly flips) the
    # existing position. Realize P&L on whatever it closes, using the
    # average cost at the moment of this entry, scaled by the
    # contract multiplier.
    assert ip.average_cost is not None  # guaranteed: open_quantity != 0 always implies a set average_cost
    closing_quantity = min(abs(signed_qty), abs(ip.open_quantity))
    direction = Decimal(1) if ip.open_quantity > 0 else Decimal(-1)
    realized = (fill.price - ip.average_cost) * direction * closing_quantity * fill.multiplier
    ip.realized_pnl += realized
    ip.closing_fills += 1

    remainder = abs(signed_qty) - closing_quantity
    ip.open_quantity += signed_qty
    if remainder > 0:
        # Flipped through flat: what's left opens a FRESH position in
        # the new direction, priced at this same entry.
        ip.average_cost = fill.price
        ip.open_quantity = remainder if signed_qty > 0 else -remainder
    elif ip.open_quantity == 0:
        ip.average_cost = None

    return realized


def compute_book_performance(
    session: Session,
    *,
    tenant_id: str,
    book: Book,
    follower_connection_ids: frozenset[str] | None = None,
) -> PlatformPerformanceReport:
    """`follower_connection_ids`: see `load_ordered_root_entries`'s own
    docstring -- passed straight through."""
    entries, latest_correction_by_original = load_ordered_root_entries(
        session, tenant_id=tenant_id, book=book, follower_connection_ids=follower_connection_ids
    )

    report = PlatformPerformanceReport()
    per_instrument = report.per_instrument

    for entry in entries:
        correction = latest_correction_by_original.get(entry.entry_id)
        fill = effective_fill(entry, correction)

        # Grouped by the FILL's own (possibly corrected) instrument --
        # Track 41: a revision that renames the instrument must be
        # replayed under its real, latest instrument, never the root's
        # stale one.
        ip = per_instrument.setdefault(fill.instrument, InstrumentPerformance(instrument=fill.instrument))
        if fill.fee is None:
            ip.unknown_fee_entry_count += 1
        else:
            ip.total_fees += fill.fee
        ip.last_fill_price = fill.price

        report.realized_pnl += apply_entry(ip, fill)

    for ip in per_instrument.values():
        if ip.unknown_fee_entry_count == 0:
            ip.net_pnl = ip.realized_pnl - ip.total_fees

    return report
