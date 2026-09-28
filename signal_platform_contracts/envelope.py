"""The event envelope every cross-service export uses -- one shape for
every event type, carrying the fields INTEGRATION_DECISION.md S6's
"Event envelope" section requires: schema version, event type, stable
event ID, producer identity, source stream, producer generation,
monotonically ordered export sequence, immutable subject binding,
event/effective/availability/receipt times, environment, evidence class,
correlation/causation IDs, and a payload hash.

`event_id` is the idempotency key: a producer must assign the SAME
`event_id` to every redelivery attempt of the same real event, so a
receiver's unique-identity constraint (S6 "Commit and delivery") can
recognize a resend without depending on the transport layer to dedupe
for it.
"""
from __future__ import annotations

import enum
import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_SCHEMA_VERSION = "1.0.0"


class Environment(str, enum.Enum):
    """Matches signal-portfolio-commercial's own `app/config.py` ENVIRONMENT
    values -- the same five environments, since an event crossing this
    contract boundary must never claim a more-live environment than the
    producer's own real one."""

    LOCAL_SIM = "LOCAL_SIM"
    INTEGRATION_ISOLATED = "INTEGRATION_ISOLATED"
    PLATFORM_DEMO = "PLATFORM_DEMO"
    PRIVATE_SHADOW = "PRIVATE_SHADOW"
    COMMERCIAL_LIVE = "COMMERCIAL_LIVE"


class EvidenceClass(str, enum.Enum):
    """INTEGRATION_DECISION.md S7: every event and every four-book ledger
    entry this bridge ever produces must carry exactly one of these --
    never left implicit, never "upgraded" by a later reader."""

    SYNTHETIC_FIXTURE = "synthetic_fixture"
    INTERNAL_PAPER = "internal_paper"
    EXTERNAL_DEMO = "external_demo"
    HYPOTHETICAL_BACKTEST = "hypothetical_backtest"
    OBSERVED_OWNER_LIVE = "observed_owner_live"
    PLATFORM_REPORTED_MODEL = "platform_reported_model"
    OBSERVED_FOLLOWER_LIVE = "observed_follower_live"


class EventType(str, enum.Enum):
    """INTEGRATION_DECISION.md S6's own named event categories. See this
    package's own docstring: only `SOURCE_RECEIPT` and `EXECUTION_APPLIED`
    have an implemented payload model in this slice -- every other member
    here is a real placeholder for a named later slice, not yet backed by
    a payload type or a producer."""

    SOURCE_RECEIPT = "source_receipt"
    SOURCE_REVISION = "source_revision"
    SOURCE_CLASSIFICATION = "source_classification"
    ROUTING_ADMISSION_OUTCOME = "routing_admission_outcome"
    EXECUTION_OBSERVED = "execution_observed"
    EXECUTION_APPLIED = "execution_applied"
    EXECUTION_CORRECTION = "execution_correction"
    ORDER_FAMILY_STATE = "order_family_state"
    PENDING_COMMITMENT_STATE = "pending_commitment_state"
    POSITION_SNAPSHOT = "position_snapshot"
    RECONCILIATION_STATUS = "reconciliation_status"
    PROTECTION_DESIRED = "protection_desired"
    PROTECTION_SUBMITTED = "protection_submitted"
    PROTECTION_CONFIRMED = "protection_confirmed"
    PROTECTION_DEFICIENT = "protection_deficient"
    PROTECTION_REPAIRED = "protection_repaired"
    FEE = "fee"
    CASH_MOVEMENT = "cash_movement"
    FINANCING = "financing"
    DIVIDEND = "dividend"
    CORPORATE_ACTION = "corporate_action"
    MARKET_MARK = "market_mark"
    QUALIFIED_METRIC_ARTIFACT = "qualified_metric_artifact"
    INCIDENT = "incident"
    WORKER_PROGRESS = "worker_progress"
    ROUTE_QUALIFICATION = "route_qualification"


#: Event types with a real payload model (signal_platform_contracts.payloads)
#: and an intended producer in this slice. Anything else in EventType is a
#: named placeholder only -- see this module's own docstring.
IMPLEMENTED_EVENT_TYPES = frozenset(
    {EventType.SOURCE_RECEIPT, EventType.EXECUTION_APPLIED, EventType.FEE, EventType.POSITION_SNAPSHOT}
)


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """A stable hash of a payload's canonical JSON form -- sorted keys, no
    whitespace -- so the SAME event_id arriving with a DIFFERENT hash is
    detectable as the integrity incident S6 describes ("Commit and
    delivery": "The same identity with a different hash is an integrity
    incident, not last-write-wins"), not silently overwritten."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = CONTRACT_SCHEMA_VERSION
    event_type: EventType
    #: The idempotency key -- see this module's own docstring. Stable
    #: across every redelivery of the same real event.
    event_id: str
    #: Which producer instance emitted this (e.g. a specific signal-copier
    #: deployment's own stable id) -- distinct from `source_stream` below.
    producer_id: str
    #: The logical stream this event belongs to (e.g. "signal-copier:acct1"
    #: or "signal-copier:provider:momentum_mike") -- export_sequence is
    #: only monotonic WITHIN (producer_id, source_stream, producer_generation).
    source_stream: str
    #: Bumped when a stream is re-bootstrapped from a fresh snapshot (S6
    #: "Snapshot plus deltas") -- lets a receiver detect and isolate a new
    #: generation rather than interleaving it with the old one's sequence.
    producer_generation: int = Field(ge=1, default=1)
    export_sequence: int = Field(ge=0)
    #: The immutable identity binding this event is about -- built via
    #: `signal_platform_contracts.identity.build_subject`, never a bare
    #: dict assembled ad hoc by a caller.
    subject: dict[str, str]
    event_time: datetime
    effective_time: datetime
    availability_time: datetime
    receipt_time: datetime
    environment: Environment
    evidence_class: EvidenceClass
    correlation_id: str | None = None
    causation_id: str | None = None
    payload_hash: str
    #: The typed payload's own `model_dump(mode="json")` -- see
    #: signal_platform_contracts.payloads for the models that produce this.
    payload: dict[str, Any]

    @field_validator("event_id", "producer_id", "source_stream", "payload_hash")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("event_time", "effective_time", "availability_time", "receipt_time")
    @classmethod
    def _tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("must be timezone-aware")
        return value

    @field_validator("subject")
    @classmethod
    def _non_empty_subject(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("subject must not be empty -- every event is about something")
        return value
