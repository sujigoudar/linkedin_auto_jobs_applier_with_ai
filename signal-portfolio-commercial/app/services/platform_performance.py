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

Deliberately GROSS only, same as signal-copier's own module: `fee` is
frequently `None` on an ingested entry (S7: "Importing a zero default
is not proof of a verified fee"), so a NET figure would either silently
treat unknown fees as zero or need per-entry fee tracking this replay
doesn't attempt. `unknown_fee_entry_count` reports how many entries in
each instrument's own history had no fee recorded, so a caller can
render "Net: unavailable (N fills with unknown fee)" honestly instead
of a net number quietly computed from a gross assumption.

Does NOT compute win rates or episode counts (signal-copier's own
`SymbolEconomics` does) -- out of scope for this bounded slice; a
later one can port that half of the same algorithm the same way if a
real screen needs it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import Book, LedgerEntry, Side


@dataclass
class InstrumentPerformance:
    instrument: str
    realized_pnl: Decimal = Decimal(0)
    open_quantity: Decimal = Decimal(0)  # signed: positive = net long, negative = net short
    average_cost: Decimal | None = None
    last_fill_price: Decimal | None = None
    closing_fills: int = 0
    #: How many entries contributing to THIS instrument's history (opening
    #: or closing) had `fee is None` -- 0 means every entry's fee was
    #: genuinely known (even if some were verified-zero), so
    #: `realized_pnl` here is also, incidentally, the net figure; > 0
    #: means a net figure cannot be honestly derived from this replay.
    unknown_fee_entry_count: int = 0


@dataclass
class PlatformPerformanceReport:
    realized_pnl: Decimal = Decimal(0)
    per_instrument: dict[str, InstrumentPerformance] = field(default_factory=dict)


def compute_platform_performance(session: Session, *, tenant_id: str) -> PlatformPerformanceReport:
    """Backward-compatible alias for `compute_book_performance(...,
    book=Book.PLATFORM)` -- this module's original, still most common,
    entry point (app/api/dashboard_routes.py's own
    GET /api/v1/ops/platform-performance)."""
    return compute_book_performance(session, tenant_id=tenant_id, book=Book.PLATFORM)


def compute_book_performance(session: Session, *, tenant_id: str, book: Book) -> PlatformPerformanceReport:
    entries = list(
        session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.tenant_id == tenant_id, LedgerEntry.book == book)
            .order_by(LedgerEntry.event_time, LedgerEntry.created_at)
        ).all()
    )

    report = PlatformPerformanceReport()
    per_instrument = report.per_instrument

    for entry in entries:
        signed_qty = entry.quantity if entry.side == Side.BUY else -entry.quantity
        ip = per_instrument.setdefault(entry.instrument, InstrumentPerformance(instrument=entry.instrument))
        if entry.fee is None:
            ip.unknown_fee_entry_count += 1
        ip.last_fill_price = entry.price

        if ip.open_quantity == 0 or (ip.open_quantity > 0) == (signed_qty > 0):
            # Opening or adding to a position on the same side: only the
            # volume-weighted average cost moves, nothing is realized yet.
            new_quantity = ip.open_quantity + signed_qty
            existing_cost = (ip.average_cost or Decimal(0)) * abs(ip.open_quantity)
            ip.average_cost = (existing_cost + entry.price * abs(signed_qty)) / abs(new_quantity)
            ip.open_quantity = new_quantity
            continue

        # Opposite side: this entry reduces (and possibly flips) the
        # existing position. Realize P&L on whatever it closes, using the
        # average cost at the moment of this entry, scaled by the
        # contract multiplier.
        assert ip.average_cost is not None  # guaranteed: open_quantity != 0 always implies a set average_cost
        closing_quantity = min(abs(signed_qty), abs(ip.open_quantity))
        direction = Decimal(1) if ip.open_quantity > 0 else Decimal(-1)
        realized = (entry.price - ip.average_cost) * direction * closing_quantity * entry.multiplier
        ip.realized_pnl += realized
        report.realized_pnl += realized
        ip.closing_fills += 1

        remainder = abs(signed_qty) - closing_quantity
        ip.open_quantity += signed_qty
        if remainder > 0:
            # Flipped through flat: what's left opens a FRESH position in
            # the new direction, priced at this same entry.
            ip.average_cost = entry.price
            ip.open_quantity = remainder if signed_qty > 0 else -remainder
        elif ip.open_quantity == 0:
            ip.average_cost = None

    return report
