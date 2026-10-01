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
open-position/average-cost reporting.

## Completed episodes / win rate

A "completed episode" is exactly one `_Lot` (above) from the moment it
opens (or is freshly opened by a flip through flat) to the moment its
own `quantity` is fully consumed by FIFO closes -- the same real
lot-tracking this module already does for per-analyst P&L, never a
second, separate open/close concept. `report.episodes` records one
`CompletedEpisode` per such fully-closed lot, with `pnl` the SUM of
every real closing fill's own realized P&L against THAT lot (a lot
closed across several partial fills is still exactly one episode, not
several) -- a real win is `pnl > 0`, ported the same way signal-copier's
own `SymbolEconomics` derives a win rate from its own completed round
trips.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.ledger import Book, Side
from app.services.platform_performance import effective_fill, load_ordered_root_entries


@dataclass
class _Lot:
    quantity: Decimal  # always positive; remaining
    entry_price: Decimal
    analyst: str | None
    #: The real `event_time` of the fill that opened (or, on a flip
    #: through flat, re-opened) this lot -- `CompletedEpisode.opened_at`
    #: once the lot is fully consumed.
    opened_at: datetime
    #: Running sum of every real closing fill's own realized P&L against
    #: THIS lot so far -- becomes `CompletedEpisode.pnl` once the lot's
    #: own `quantity` reaches zero.
    episode_pnl: Decimal = Decimal(0)


@dataclass
class CompletedEpisode:
    """One real FIFO lot, opened then fully closed -- CU-06's own
    "episode/trade-completion concept" (M-CU-06-03), never a guessed or
    time-boxed grouping."""

    instrument: str
    analyst: str | None
    opened_at: datetime
    closed_at: datetime
    pnl: Decimal

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


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
    #: Every real FIFO lot that reached full closure during this replay,
    #: in the order it closed -- see module docstring. A lot still open
    #: at the end of the replay (the book's own current position) is
    #: never included -- only a REAL completion counts as an episode.
    episodes: list[CompletedEpisode] = field(default_factory=list)

    @property
    def completed_episode_count(self) -> int:
        return len(self.episodes)

    @property
    def winning_episode_count(self) -> int:
        return sum(1 for e in self.episodes if e.is_win)

    @property
    def win_rate(self) -> Decimal | None:
        """`winning_episode_count / completed_episode_count` -- `None`
        (never a fabricated 0%) when zero episodes have completed yet."""
        if not self.episodes:
            return None
        return Decimal(self.winning_episode_count) / Decimal(self.completed_episode_count)

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
    session: Session,
    *,
    tenant_id: str,
    book: Book = Book.PLATFORM,
    follower_connection_ids: frozenset[str] | None = None,
) -> AnalystAttributionReport:
    """`follower_connection_ids`: same contract as
    `platform_performance.load_ordered_root_entries`'s own -- only
    meaningful (and only ever passed) for `book == Book.FOLLOWER`, to
    scope this replay to one customer's own connections rather than a
    tenant's whole FOLLOWER book across every customer.

    Track 41: replays `platform_performance.load_ordered_root_entries`'s
    own real query (never a second, separately-maintained one) and
    folds each root entry's latest correction through the SAME
    `platform_performance.effective_fill` every other reader uses -- a
    correction may now revise `instrument`/`side`/`quantity`/`price`
    (a genuine `SOURCE_RECEIPT` revision/EDIT), not only `fee`, and this
    replay must see exactly the same corrected values
    `compute_book_performance` does, or `account_total_realized_pnl`
    would stop reconciling to it. `originating_analyst_id` is the ONE
    field `effective_fill` deliberately never touches (it isn't one of
    `EffectiveFill`'s fields at all) -- an edit never reassigns whose
    recommendation this was, so it is always read straight off the
    ROOT entry, correction or not."""
    entries, latest_correction_by_original = load_ordered_root_entries(
        session, tenant_id=tenant_id, book=book, follower_connection_ids=follower_connection_ids,
    )

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
        correction = latest_correction_by_original.get(entry.entry_id)
        fill = effective_fill(entry, correction)
        instrument = fill.instrument
        analyst = entry.originating_analyst_id  # never revised by a correction -- see docstring above
        signed_qty = fill.quantity if fill.side == Side.BUY else -fill.quantity
        open_quantity = open_quantity_by_instrument.get(instrument, Decimal(0))
        lots = open_lots_by_instrument.setdefault(instrument, [])

        if open_quantity == 0 or (open_quantity > 0) == (signed_qty > 0):
            # Opening or adding to the position on the same side: a
            # brand-new lot, attributed to THIS fill's own analyst.
            lots.append(_Lot(quantity=fill.quantity, entry_price=fill.price, analyst=analyst, opened_at=fill.event_time))
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
            realized = (fill.price - lot.entry_price) * direction * consumed * fill.multiplier
            bucket = _bucket(instrument, lot.analyst)
            bucket.realized_pnl += realized
            bucket.closing_fills += 1
            lot.quantity -= consumed
            lot.episode_pnl += realized
            remaining_to_close -= consumed
            if lot.quantity <= 0:
                lots.pop(0)
                # This real lot has now gone from open to fully closed --
                # a real completed episode, never a guessed boundary.
                report.episodes.append(
                    CompletedEpisode(
                        instrument=instrument,
                        analyst=lot.analyst,
                        opened_at=lot.opened_at,
                        closed_at=fill.event_time,
                        pnl=lot.episode_pnl,
                    )
                )

        closed_quantity = min(abs(signed_qty), abs(open_quantity))
        remainder = abs(signed_qty) - closed_quantity
        open_quantity_by_instrument[instrument] = open_quantity + signed_qty
        if remainder > 0:
            # Flipped through flat -- the remainder opens a fresh
            # position in the NEW direction, attributed to THIS fill's
            # own analyst.
            lots.append(_Lot(quantity=remainder, entry_price=fill.price, analyst=analyst, opened_at=fill.event_time))
            _bucket(instrument, analyst).entries_opened += 1

    return report
