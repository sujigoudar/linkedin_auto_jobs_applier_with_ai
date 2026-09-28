"""Builds a real `EventEnvelope`/`ExecutionAppliedPayload` from a
genuinely FILLED `OrderResult`, per Signal Platform Integration
Correction Pack's own INTEGRATION_DECISION.md S4.3/S6 -- the missing
piece slice 5's own commit message named: "app/engine.py does not yet
construct or pass a real export_envelope on any fill."

`build_execution_applied_envelope` returns `None` (never a
best-effort/partial envelope) whenever a FILLED result is missing a
field this payload actually requires (`broker_order_id`,
`filled_quantity`, `filled_price`) -- a caller that gets `None` back
skips exporting rather than fabricating a placeholder for a value the
broker adapter didn't actually report. This should be rare (every
broker adapter in this codebase that reports FILLED synchronously also
sets these), but it is a real, checked condition, not an assumption.

Known, documented limitations of this slice's own instrument-identity
construction (not solved here, not silently hidden either):

- `currency`: this codebase has no per-symbol contract-currency
  registry. A symbol containing "/" (ccxt/forex pair notation, e.g.
  "BTC/USDT", "EUR/USD") uses its quote side; every other symbol
  (equities, futures, options in this codebase's current adapters)
  defaults to "USD", matching every existing example account/routing
  config, which is US-broker-only today. A future non-USD equity/future
  account would need this resolved for real, not silently mislabeled.
- `multiplier`: always "1". This codebase has no per-symbol contract-
  multiplier registry either -- correct for the equities and crypto/
  forex pairs every current example config actually routes to, WRONG
  for a real futures contract with a multiplier other than 1 (e.g. ES
  futures). A real futures deployment must not rely on this default.
- `venue`: the destination account's own `broker` id (e.g. "paper",
  "alpaca"), not a canonical market-identifier-code venue -- this
  service has no venue registry independent of its broker adapters.
"""
from __future__ import annotations

from datetime import datetime, timezone

from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    ExecutionAppliedPayload,
    InstrumentIdentity,
    PrivateAccountIdentity,
    SourceIdentity,
    SourceReceiptPayload,
    build_subject,
    compute_payload_hash,
)

from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal

#: SourceIdentity.parser_version is required (S5), but this codebase has
#: no real per-source-adapter parser-version registry yet (same
#: disclosed-default pattern as `currency`/`multiplier`/`venue` above) --
#: every SOURCE_RECEIPT this slice exports carries this literal until a
#: real one exists.
_UNVERSIONED_PARSER = "unversioned"

_QUANTITY_CONVENTION_BY_ASSET_CLASS = {
    AssetClass.CRYPTO: "units",
    AssetClass.FOREX: "units",
    AssetClass.EQUITY: "shares",
    AssetClass.OPTION: "contracts",
    AssetClass.FUTURE: "contracts",
}


def _resolve_currency(symbol: str) -> str:
    if "/" in symbol:
        return symbol.split("/")[-1].upper()
    return "USD"


def build_execution_applied_envelope(
    result: OrderResult,
    *,
    account: DestinationAccount,
    symbol: str,
    side: Side,
    asset_class: AssetClass,
    source_stream: str,
    export_sequence: int,
    producer_id: str,
    evidence_class: EvidenceClass,
    environment: Environment,
    originating_source_event_id: str | None = None,
    originating_analyst_id: str | None = None,
) -> EventEnvelope | None:
    """Returns `None` (see this module's own docstring) unless `result`
    is a FILLED order with every field `ExecutionAppliedPayload` actually
    requires. `side` must already be the resolved BUY/SELL actually sent
    to the broker (never `Side.CLOSE` -- the engine always resolves a
    close to its opposing side before calling the broker at all, per
    app/engine.py's own module docstring)."""
    if result.status != OrderStatus.FILLED:
        return None
    if result.broker_order_id is None or result.filled_quantity is None or result.filled_price is None:
        return None
    if side == Side.CLOSE:
        raise ValueError("side must be the resolved BUY/SELL actually sent to the broker, not Side.CLOSE")

    instrument = InstrumentIdentity(
        instrument_id=symbol,
        venue=account.broker,
        market_type=asset_class.value,
        currency=_resolve_currency(symbol),
        multiplier="1",
        quantity_convention=_QUANTITY_CONVENTION_BY_ASSET_CLASS[asset_class],
    )
    account_identity = PrivateAccountIdentity(account_id=account.account_id)
    payload = ExecutionAppliedPayload(
        account=account_identity,
        instrument=instrument,
        side=side.value,
        filled_quantity=str(result.filled_quantity),
        filled_price=str(result.filled_price),
        fee=None,
        broker=account.broker,
        broker_order_id=result.broker_order_id,
        originating_source_event_id=originating_source_event_id,
        originating_analyst_id=originating_analyst_id,
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)

    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED,
        event_id=f"execution-applied:{account.account_id}:{result.broker_order_id}",
        producer_id=producer_id,
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(account=account_identity, instrument=instrument),
        event_time=result.executed_at,
        effective_time=result.executed_at,
        availability_time=now,
        receipt_time=now,
        environment=environment,
        evidence_class=evidence_class,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def build_source_receipt_envelope(
    signal: Signal,
    *,
    source_stream: str,
    export_sequence: int,
    producer_id: str,
    evidence_class: EvidenceClass,
    environment: Environment,
) -> EventEnvelope | None:
    """S12 step 5 "Portfolio Lab source feed": exports every source
    instruction as it's RECEIVED (S7: "SOURCE records what was
    recommended; it does not claim an execution"), independent of
    whether routing ever sends it to a broker. Returns `None` for a
    `Side.CLOSE` signal -- `SourceReceiptPayload.side` only accepts
    "buy"/"sell" (a close's real direction depends on whatever position
    is open at execution time, which isn't a property of the
    RECOMMENDATION itself), not a payload-shape limitation this
    function can work around by guessing.

    `instrument.venue`: unlike `build_execution_applied_envelope`
    (which has a real destination `DestinationAccount.broker` to use),
    a source recommendation is received before any routing decision --
    there is no broker/venue yet, genuinely, not merely unresolved.
    Uses the literal `"unspecified"` rather than fabricating one; a
    later EXECUTION_APPLIED for the same instrument (if this signal is
    ever routed and filled) carries the real venue.

    `source.parser_version`: see this module's own `_UNVERSIONED_PARSER`
    docstring -- no per-adapter parser-version registry exists yet.
    """
    if signal.side == Side.CLOSE:
        return None

    instrument = InstrumentIdentity(
        instrument_id=signal.symbol,
        venue="unspecified",
        market_type=signal.asset_class.value,
        currency=_resolve_currency(signal.symbol),
        multiplier="1",
        quantity_convention=_QUANTITY_CONVENTION_BY_ASSET_CLASS[signal.asset_class],
    )
    source_identity = SourceIdentity(
        source_provider_id=signal.source,
        analyst_id=signal.analyst,
        parser_version=_UNVERSIONED_PARSER,
        source_event_id=signal.id,
    )
    payload = SourceReceiptPayload(
        source=source_identity,
        instrument=instrument,
        side=signal.side.value,
        quantity=None if signal.quantity is None else str(signal.quantity),
        price=None if signal.price is None else str(signal.price),
        stop_loss=None if signal.stop_loss is None else str(signal.stop_loss),
        take_profit=None if signal.take_profit is None else str(signal.take_profit),
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)

    return EventEnvelope(
        event_type=EventType.SOURCE_RECEIPT,
        event_id=f"source-receipt:{signal.id}",
        producer_id=producer_id,
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(source=source_identity, instrument=instrument),
        event_time=signal.received_at,
        effective_time=signal.received_at,
        availability_time=now,
        receipt_time=now,
        environment=environment,
        evidence_class=evidence_class,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )
