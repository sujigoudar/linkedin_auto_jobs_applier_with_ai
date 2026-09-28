"""Typed payloads -- one model per `EventType` that this slice actually
implements (see envelope.py's own `IMPLEMENTED_EVENT_TYPES`). A payload
model's `.model_dump(mode="json")` is exactly what goes in an
`EventEnvelope.payload`, and `signal_platform_contracts.envelope.
compute_payload_hash` is computed over that same dump -- never a
different serialization of the same data, or the hash would mean
nothing.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from signal_platform_contracts.identity import (
    InstrumentIdentity,
    PrivateAccountIdentity,
    SourceIdentity,
)
from signal_platform_contracts.money import Money

_KNOWN_SIDES = ("buy", "sell")


def _known_side(value: str) -> str:
    if value not in _KNOWN_SIDES:
        raise ValueError(f"{value!r} is not a known side (expected one of {_KNOWN_SIDES})")
    return value


class SourceReceiptPayload(BaseModel):
    """`EventType.SOURCE_RECEIPT` -- a source instruction as originally
    received, before any private engine acted on it. This is a
    recommendation, never a claim of execution (INTEGRATION_DECISION.md
    S7: "SOURCE records what was recommended; it does not claim an
    execution")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceIdentity
    instrument: InstrumentIdentity
    side: str
    quantity: Money | None = None
    price: Money | None = None
    stop_loss: Money | None = None
    take_profit: Money | None = None
    #: S8: "Exclude ... private source text ... unless the precise
    #: workflow needs and authorizes them." Defaults to excluded; a later
    #: slice's relay must refuse to export the raw source text unless this
    #: is explicitly False AND the export grant covers it.
    raw_text_excluded: bool = True

    _validate_side = field_validator("side")(_known_side)


class FeePayload(BaseModel):
    """`EventType.FEE` -- a confirmed fee for a specific already-applied
    execution, arriving separately from the fill itself
    (INTEGRATION_DECISION.md S7: fees are frequently not known at fill
    time). Correlates to that execution by the SAME (account, instrument,
    broker, broker_order_id) identity its own `ExecutionAppliedPayload`
    carried -- there is no other shared key between the two payloads.
    `fee` is required here (never `None`): this event exists specifically
    to move a fee from "not yet known" to "confirmed", so an unset value
    would defeat its own purpose; a genuinely zero fee is expressed as
    `Money("0")`, not by omitting the event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: PrivateAccountIdentity
    instrument: InstrumentIdentity
    broker: str
    broker_order_id: str
    fee: Money


class ExecutionAppliedPayload(BaseModel):
    """`EventType.EXECUTION_APPLIED` -- a fill the private engine's own
    broker adapter actually confirmed. `fee: None` means "not yet known",
    never "zero" (INTEGRATION_DECISION.md S7: "Importing a zero default is
    not proof of a verified zero fee")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: PrivateAccountIdentity
    instrument: InstrumentIdentity
    side: str
    filled_quantity: Money
    filled_price: Money
    fee: Money | None = None
    broker: str
    broker_order_id: str
    #: Links back to the SourceReceiptPayload's own source_event_id that
    #: caused this fill, when that provenance is known.
    originating_source_event_id: str | None = None

    _validate_side = field_validator("side")(_known_side)
