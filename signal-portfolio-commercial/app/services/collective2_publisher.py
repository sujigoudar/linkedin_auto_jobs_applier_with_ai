"""Collective2 API4 publisher: maps a QUEUED PublicationIntent onto the
API4 Order envelope, per spec/docs/06_platform_adapters.md's "Collective2
first" section. Builds and validates the outbound request; does NOT
transmit it -- there is no C2 sandbox, no credentials exist in this
environment, and "No live financial publisher/broker/payment authority
during build" holds regardless. Submission (an authenticated HTTP POST
to the real API4 endpoint) is future work gated on real, owner-supplied
credentials and CARD-3 (platform agreements).

TIF resolution attempted and BLOCKED, not guessed: this session tried to
fetch Collective2's own current API4 documentation
(https://collective2.com/apidoc/v4) to resolve the spec's own flagged
conflict -- "It documents TIF0 day/1 GTC, but one conditional example
uses 2. This is a documentation conflict, not permission to infer 2;
capture current authoritative schema/vendor confirmation before using
unsupported values." -- and the request was refused by this
environment's egress policy (collective2.com is not on the allowed
domain list), not by a transient network failure. Per this build's own
rule that missing evidence keeps a gate BLOCKED rather than assumed
passing, `Tif` below defines ONLY the two values the documentation
agrees on (DAY=0, GTC=1) and `build_order` raises rather than accepting
or silently normalizing a TIF value of 2 or anything else -- vendor
confirmation must be captured before this constant set is ever widened.
"""
from __future__ import annotations

import enum
from decimal import Decimal, InvalidOperation

from app.models.publication import PublicationAction, PublicationIntent, PublicationSide, QuantityBasis


class Tif(enum.IntEnum):
    DAY = 0
    GTC = 1
    #: Deliberately no GTC_EXT/other value -- see this module's docstring.
    #: A caller wanting to use "2" must first obtain and record real
    #: vendor confirmation, then extend this enum, not work around it.


class Side(enum.IntEnum):
    BUY = 1
    SELL = 2


class OrderType(enum.IntEnum):
    MARKET = 1
    LIMIT = 2
    STOP = 3


_QUANTITY_ONLY_ACTIONS = frozenset({PublicationAction.OPEN, PublicationAction.ADD, PublicationAction.REDUCE})

#: docs/06: "The guide documents price-only modifications, not general
#: direction/duration/quantity replacement." A quantity change on an
#: already-QUEUED/SENT order is therefore never modeled as an amend here
#: -- it must be its own new OPEN/ADD/REDUCE intent, never a same-order
#: field edit.
_PRICE_ONLY_MODIFICATION_ACTIONS = frozenset({PublicationAction.STOP_UPDATE, PublicationAction.TARGET_UPSERT})


class UnsupportedTifError(Exception):
    pass


class UnresolvedInstrumentError(Exception):
    pass


class UnsupportedQuantityBasisError(Exception):
    pass


def _resolve_tif(intent: PublicationIntent) -> Tif:
    """`PublicationIntent` doesn't carry a TIF field of its own (see
    spec/contracts/PublicationIntent.schema.json) -- every intent this
    build issues is DAY, the more conservative of the two undisputed
    values, until a real per-portfolio TIF policy exists (Phase 04/08
    work, not yet built) to justify GTC for a specific published
    strategy. This function exists as the single place that decision is
    made, so a future TIF policy replaces one function, not every call
    site."""
    return Tif.DAY


def build_order(intent: PublicationIntent, *, c2_symbol: str) -> dict:
    """Build (never send) the API4 Order envelope for `intent`. Raises
    rather than emitting a request C2 would reject or silently
    mis-execute: an unresolved instrument, a quantity basis this adapter
    doesn't yet implement, or (defensively, since `_resolve_tif` cannot
    currently produce one) an unsupported TIF value."""
    if not c2_symbol:
        raise UnresolvedInstrumentError(
            f"no C2Symbol resolved for instrument {intent.instrument_id!r} -- exact exchange/maturity/option "
            "contract/FX units must be resolved before submission, never guessed"
        )

    tif = _resolve_tif(intent)
    if tif not in (Tif.DAY, Tif.GTC):
        raise UnsupportedTifError(f"TIF {tif!r} is not one of the vendor-confirmed values (DAY, GTC)")

    if intent.action in _PRICE_ONLY_MODIFICATION_ACTIONS:
        return _build_price_only_modification(intent, c2_symbol=c2_symbol, tif=tif)

    if intent.action in _QUANTITY_ONLY_ACTIONS:
        return _build_quantity_order(intent, c2_symbol=c2_symbol, tif=tif)

    raise UnsupportedQuantityBasisError(
        f"action {intent.action.value!r} has no API4 order-envelope mapping in this adapter yet"
    )


def _build_quantity_order(intent: PublicationIntent, *, c2_symbol: str, tif: Tif) -> dict:
    if intent.quantity_basis != QuantityBasis.UNITS:
        raise UnsupportedQuantityBasisError(
            f"quantity_basis {intent.quantity_basis.value!r} has no API4 OrderQuantity mapping -- API4 takes an "
            "integer share/contract count, not a fraction or target-position instruction"
        )
    if intent.quantity is None:
        raise UnsupportedQuantityBasisError(f"{intent.action.value} requires a quantity, got None")

    try:
        quantity = Decimal(intent.quantity)
    except InvalidOperation as exc:
        raise UnsupportedQuantityBasisError(f"quantity {intent.quantity!r} is not a valid decimal string") from exc
    if quantity != quantity.to_integral_value():
        raise UnsupportedQuantityBasisError(
            f"quantity {intent.quantity!r} is not a whole number -- API4 OrderQuantity is an integer"
        )

    side = Side.SELL if intent.side is PublicationSide.SELL else Side.BUY

    return {
        "StrategyId": intent.external_strategy_id,
        "C2Symbol": c2_symbol,
        "OrderType": int(OrderType.MARKET),
        "Side1": int(side),
        "OrderQuantity": int(quantity),
        "TIF": int(tif),
    }


def _build_price_only_modification(intent: PublicationIntent, *, c2_symbol: str, tif: Tif) -> dict:
    return {
        "StrategyId": intent.external_strategy_id,
        "C2Symbol": c2_symbol,
        "TIF": int(tif),
        "PriceOnlyModification": True,
    }
