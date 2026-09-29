"""Typed payloads -- one model per `EventType` that this slice actually
implements (see envelope.py's own `IMPLEMENTED_EVENT_TYPES`). A payload
model's `.model_dump(mode="json")` is exactly what goes in an
`EventEnvelope.payload`, and `signal_platform_contracts.envelope.
compute_payload_hash` is computed over that same dump -- never a
different serialization of the same data, or the hash would mean
nothing.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

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


#: The real, distinct routing/admission/fill outcome states signal-copier's
#: own `app/engine.py` actually produces for one destination account (or,
#: for the first two, for a signal with no destination account reached at
#: all) -- see that module's own `_export_source_outcome` docstring for
#: exactly which code path produces each one. This is NOT
#: INT-027's own requested taxonomy verbatim (admitted/rejected/unfilled/
#: canceled/loss/commentary) -- "canceled" has no real code path in this
#: engine (no cancellation logic exists anywhere in app/engine.py or its
#: broker adapters), and "loss"/"commentary" are not routing outcomes at
#: all (a loss is a P&L fact computed later from closed positions, not a
#: state this engine's routing/admission step ever reports) -- inventing
#: either here would be exactly the fabrication this contract package's
#: own tests (INT-035) and this integration's own standing rules forbid.
_KNOWN_ROUTING_OUTCOMES = (
    "not_routed",  # no destination account configured for this source/symbol at all
    "disabled_by_settings",  # a destination account/provider/analyst override skipped this entry
    "admitted_filled",  # admitted (INT-027's own "admitted") and the broker confirmed a fill
    "admitted_unfilled",  # admitted but only PENDING so far (INT-027's own "unfilled")
    "rejected",  # a real, checked refusal: routing/asset-class/risk/capital/broker rejection
    "error",  # an operational failure (no broker adapter, broker call raised/errored)
)


def _known_routing_outcome(value: str) -> str:
    if value not in _KNOWN_ROUTING_OUTCOMES:
        raise ValueError(f"{value!r} is not a known routing outcome (expected one of {_KNOWN_ROUTING_OUTCOMES})")
    return value


class RoutingAdmissionOutcomePayload(BaseModel):
    """`EventType.ROUTING_ADMISSION_OUTCOME` -- the real routing/admission/
    fill outcome for one `SOURCE_RECEIPT`, exported as a separate,
    correlated follow-up event once that outcome is actually known
    (INTEGRATION_ACCEPTANCE_CASES.json INT-027 "All permitted source
    outcomes reach research": a `SOURCE_RECEIPT` alone never encoded
    WHICH routing outcome produced it -- this payload is exactly that
    missing distinction). Mirrors `FeePayload`'s own late-arriving-
    correlated-update idiom, but correlates by `originating_source_event_id`
    (the originating `SOURCE_RECEIPT`'s own `EventEnvelope.event_id`)
    rather than by an execution's `(broker, broker_order_id)` identity --
    a routing outcome exists before any broker order necessarily does
    (e.g. `rejected`/`error`/`not_routed` never reach a broker at all).

    `account` is `None` exactly when no destination account was ever
    reached for this signal (`not_routed`) -- never fabricated. `outcome`
    is one of `_KNOWN_ROUTING_OUTCOMES` above; see that tuple's own
    comment for the two INT-027-requested categories this engine
    genuinely cannot produce."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    originating_source_event_id: str
    outcome: str
    account: PrivateAccountIdentity | None = None
    broker: str | None = None
    #: The real, underlying `OrderStatus` value (signal-copier's own
    #: `app/models.py`) when an order was actually attempted -- `None`
    #: for `not_routed`/`disabled_by_settings`, where no order ever was.
    order_status: str | None = None
    #: The engine's own real rejection/error message, carried verbatim
    #: (never re-worded or summarized) when one exists.
    message: str | None = None

    _validate_outcome = field_validator("outcome")(_known_routing_outcome)


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


class PositionSnapshotEntry(BaseModel):
    """One instrument's own net position AS OF a `PositionSnapshotPayload`'s
    own `cutoff_sequence` -- the accumulated result of every fill up to
    and including that sequence, not a fill itself. `side` is the
    position's own direction (never a transaction side): `quantity` is
    always positive, `average_cost` is the position's own volume-
    weighted entry price at the cutoff."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: InstrumentIdentity
    side: str
    quantity: Money
    average_cost: Money

    _validate_side = field_validator("side")(_known_side)


class PositionSnapshotPayload(BaseModel):
    """`EventType.POSITION_SNAPSHOT` -- S6's own "Snapshot plus deltas":
    one PAGE of a coherent bootstrap snapshot, S9/S6's own "Use snapshot
    cutoff, immutable manifest and delta deduplication"
    (INTEGRATION_ACCEPTANCE_CASES.json INT-008 "Snapshot and delta
    overlap", INT-009 "Interrupted bootstrap resumes").

    `manifest_id` is the SAME across every page of one snapshot attempt
    -- a receiver groups pages by it, never by anything else (never
    inferred from arrival order or timing). `cutoff_sequence` and
    `page_count` MUST also be identical across every page sharing a
    `manifest_id` -- a page claiming a different value for either is a
    corrupted/conflicting manifest, never silently reconciled by
    trusting the latest one. `page_index` is 0-based and every value in
    `range(page_count)` must appear exactly once before the manifest is
    complete -- a receiver never marks a manifest complete from a
    partial set (INT-009's own "Partial snapshot labeled complete",
    prohibited).

    Each page is still its own real `EventEnvelope` with its own
    `export_sequence` (sequential, within the SAME `producer_generation`
    the deltas immediately following the snapshot also use) and its own
    `event_id` -- so page redelivery is exactly as idempotent as every
    other event type here, no separate dedup mechanism needed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: str
    cutoff_sequence: int
    page_index: int
    page_count: int
    positions: list[PositionSnapshotEntry]

    @field_validator("cutoff_sequence", "page_index", "page_count")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must not be negative")
        return value

    @model_validator(mode="after")
    def _page_index_within_page_count(self) -> PositionSnapshotPayload:
        if self.page_count < 1:
            raise ValueError("page_count must be at least 1")
        if self.page_index >= self.page_count:
            raise ValueError(f"page_index {self.page_index} is out of range for page_count {self.page_count}")
        return self


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
    #: The `SourceIdentity.analyst_id` that originated this fill, when
    #: known -- carried directly on the fill itself (not re-derived by
    #: joining `originating_source_event_id` back to a separate
    #: SOURCE_RECEIPT event, which may not even be exported on the same
    #: stream or may arrive out of order) so a receiver can attribute
    #: P&L per analyst without depending on that join succeeding.
    #: `None` means "not attributed to a specific analyst" -- grouped as
    #: its own bucket by a receiver (INTEGRATION_ACCEPTANCE_CASES.json
    #: INT-026 "Analyst allocation survives shared symbol"), never
    #: folded into some other analyst's numbers.
    originating_analyst_id: str | None = None

    _validate_side = field_validator("side")(_known_side)
