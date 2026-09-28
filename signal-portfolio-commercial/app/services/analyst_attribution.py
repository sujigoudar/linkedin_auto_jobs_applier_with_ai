"""Per-analyst realized P&L attribution for `Book.PLATFORM`, replayed
from real `LedgerEntry` rows -- INTEGRATION_ACCEPTANCE_CASES.json
INT-026 "Analyst allocation survives shared symbol": "Two distinct
analysts trade the same instrument/account under separate allocations
... Per-analyst ownership retained; account totals reconcile to actual
aggregate," and never "Join only by account/symbol" or "Exit allocated
arbitrarily."

## Why `app/services/platform_performance.py`'s own algorithm can't
answer this

That module keeps ONE running volume-weighted average cost per
(tenant, book, instrument) -- correct for "what does this account
currently hold and at what basis," but a blended average erases which
analyst's entry contributed what once a second analyst's fill adds to
(or a close reduces) the SAME instrument: there is no way to recover,
after the fact, which analyst a blended average cost came from. Joining
a closing fill to an analyst "by account/symbol" (the case's own named
prohibited outcome) would arbitrarily credit whichever analyst happens
to be attached to the fill that reduces the position, which may not be
either analyst who actually opened the position being closed.

## FIFO-lot method (ported from signal-copier's own
`app/provider_value.py::compute_provider_value` -- the same algorithm,
same "replay every fill globally, per (account, symbol) keep an
ordered list of open lots each tagged with its own originator, and a
reducing fill consumes those lots FIFO, crediting P&L to each
CONSUMED LOT's own originator regardless of which analyst's signal (if
any) triggered the close" logic; reimplemented here, not imported, for
the same `Decimal`-not-`float` and no-asset_class-dimension reasons
`platform_performance.py`'s own docstring gives for reimplementing
signal-copier's `app/economics.py`)

Per (tenant, book, instrument): a signed `open_quantity` (matching
`platform_performance.py`'s own convention: positive = net long,
negative = net short) plus an ordered list of open "lots", one per
same-side entry fill, each tagged with `originating_analyst_id`
(`None` means "not attributed to a specific analyst" -- its own
bucket, never folded into another analyst's numbers). A reducing
(opposite-side) fill consumes those lots FIFO -- oldest entry first --
realizing P&L per lot against that lot's own entry price, credited to
THAT lot's own analyst, using the exact same single-formula trick
`platform_performance.py` uses for its own directional sign
(`direction = +1` if the position being closed is long, `-1` if
short -- correct for either side without a branch). A remainder left
over after every existing lot is consumed (a flip through flat) opens
a fresh lot attributed to THIS fill's own analyst. This is exactly how
"one analyst's exit" in an interleaved two-analyst scenario realizes
correctly against whichever analyst's lots are actually oldest, never
split arbitrarily between the two.

Fee corrections are folded in exactly like `platform_performance.py`
does (a correction row is never itself replayed as a second trade);
`account_total_realized_pnl` sums every analyst bucket's own realized
P&L, which by construction of the replay above always reconciles to
`platform_performance.compute_book_performance`'s own aggregate --
every unit of realized P&L is credited to exactly one lot's own
analyst, never duplicated or dropped.

Deliberately GROSS only and open-quantity-unaware in its own report
(same disclosed scope as signal-copier's own module): no per-analyst
open-position/average-cost reporting, no win-rate/profit-factor (a
later slice can port that half the same way if a real screen needs
it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import Book, LedgerEntry, Side


@dataclass
class _Lot:
    quantity: Decimal  # always positive; remaining
    entry_price: Decimal
    analyst: str | None


@dataclass
class AnalystInstrumentPerformance:
    instrument: str
    analyst: str | None
    realized_pnl: Decimal = Decimal(0)
    closing_fills: int = 0
    entries_opened: int = 0


@dataclass
class AnalystAttributionReport:
    #: Keyed (instrument, analyst) -- `analyst=None` is its own real
    #: bucket, never merged into another key.
    per_instrument_analyst: dict[tuple[str, str | None], AnalystInstrumentPerformance] = field(default_factory=dict)

    @property
    def account_total_realized_pnl(self) -> Decimal:
        """Sum of every (instrument, analyst) bucket's own realized P&L
        -- INT-026's own "account totals reconcile to actual aggregate":
        this must always equal `platform_performance.compute_book_
        performance(...).realized_pnl` for the same tenant/book, since
        the FIFO replay credits every unit of realized P&L to exactly
        one lot's own analyst bucket, never duplicating or dropping
        any."""
        return sum((ip.realized_pnl for ip in self.per_instrument_analyst.values()), Decimal(0))


def compute_analyst_attribution(
    session: Session, *, tenant_id: str, book: Book = Book.PLATFORM
) -> AnalystAttributionReport:
    all_entries = list(
        session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.tenant_id == tenant_id, LedgerEntry.book == book)
            .order_by(LedgerEntry.event_time, LedgerEntry.created_at)
        ).all()
    )
    # Same correction-folding as platform_performance.py: a fee
    # correction never changes quantity/price/side/analyst, so it is
    # never itself replayed as a second, separate fill.
    entries = [entry for entry in all_entries if entry.correction_of is None]

    report = AnalystAttributionReport()

    def _bucket(instrument: str, analyst: str | None) -> AnalystInstrumentPerformance:
        key = (instrument, analyst)
        bucket = report.per_instrument_analyst.get(key)
        if bucket is None:
            bucket = AnalystInstrumentPerformance(instrument=instrument, analyst=analyst)
            report.per_instrument_analyst[key] = bucket
        return bucket

    open_quantity_by_instrument: dict[str, Decimal] = {}
    open_lots_by_instrument: dict[str, list[_Lot]] = {}

    for entry in entries:
        instrument = entry.instrument
        analyst = entry.originating_analyst_id
        signed_qty = entry.quantity if entry.side == Side.BUY else -entry.quantity
        open_quantity = open_quantity_by_instrument.get(instrument, Decimal(0))
        lots = open_lots_by_instrument.setdefault(instrument, [])

        if open_quantity == 0 or (open_quantity > 0) == (signed_qty > 0):
            # Opening or adding to the position on the same side: a
            # brand-new lot, attributed to THIS fill's own analyst.
            lots.append(_Lot(quantity=entry.quantity, entry_price=entry.price, analyst=analyst))
            open_quantity_by_instrument[instrument] = open_quantity + signed_qty
            _bucket(instrument, analyst).entries_opened += 1
            continue

        # Opposite side: reduce (and possibly flip through) the existing
        # lots, FIFO -- oldest entry first, crediting each consumed
        # lot's own analyst, never the analyst on THIS closing fill.
        remaining_to_close = min(abs(signed_qty), abs(open_quantity))
        direction = Decimal(1) if open_quantity > 0 else Decimal(-1)
        while remaining_to_close > 0 and lots:
            lot = lots[0]
            consumed = min(lot.quantity, remaining_to_close)
            realized = (entry.price - lot.entry_price) * direction * consumed * entry.multiplier
            bucket = _bucket(instrument, lot.analyst)
            bucket.realized_pnl += realized
            bucket.closing_fills += 1
            lot.quantity -= consumed
            remaining_to_close -= consumed
            if lot.quantity <= 0:
                lots.pop(0)

        closed_quantity = min(abs(signed_qty), abs(open_quantity))
        remainder = abs(signed_qty) - closed_quantity
        open_quantity_by_instrument[instrument] = open_quantity + signed_qty
        if remainder > 0:
            # Flipped through flat -- the remainder opens a fresh
            # position in the NEW direction, attributed to THIS fill's
            # own analyst.
            lots.append(_Lot(quantity=remainder, entry_price=entry.price, analyst=analyst))
            _bucket(instrument, analyst).entries_opened += 1

    return report
