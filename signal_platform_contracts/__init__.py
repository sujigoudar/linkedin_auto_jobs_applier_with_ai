"""signal_platform_contracts: the pure, versioned cross-service contract
between the private trading engine (`signal-copier/`) and the commercial
platform (`signal-portfolio-commercial/`).

Per Signal_Platform_Integration_Correction_Pack's own INTEGRATION_DECISION.md
S4.1: this package imports nothing from either application's own `app`
package, opens no database connection, and holds no broker/API secret --
it is data shapes and pure validation only, safe for both services (and
their tests) to depend on without coupling their runtimes together.

Slice 1 of the integration sequence (INTEGRATION_DECISION.md S12): the
event envelope (S6) and identity contract (S5). `EventType` enumerates
every event category S6 names; `SOURCE_RECEIPT` and `EXECUTION_APPLIED`
have an implemented payload model for S12's own "first complete proof"
(one simulated source instruction -> one paper execution -> one exported
event -> one staff view), `FEE` (slice 16, INT-012 "Late fee revises
net report") for the out-of-band fee confirmation that follows a fill,
`POSITION_SNAPSHOT` (slice 23, INT-008/INT-009 "Snapshot and delta
overlap"/"Interrupted bootstrap resumes") for a manifest-bound bootstrap
snapshot page, and `ROUTING_ADMISSION_OUTCOME` (INT-027 "All permitted
source outcomes reach research") for the real routing/admission/fill
outcome that follows a `SOURCE_RECEIPT`, correlated back to it by
`RoutingAdmissionOutcomePayload.originating_source_event_id`. Every
other `EventType` member is a real, intentional placeholder for a
later slice, not a promise this slice already carries that data.
"""
from __future__ import annotations

from signal_platform_contracts.envelope import (
    CONTRACT_SCHEMA_VERSION,
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    compute_payload_hash,
)
from signal_platform_contracts.identity import (
    CommercialIdentity,
    InstrumentIdentity,
    PortfolioIdentity,
    PrivateAccountIdentity,
    PublicationIdentity,
    SourceIdentity,
    build_subject,
)
from signal_platform_contracts.money import Money
from signal_platform_contracts.payloads import (
    CryptoDerivativeDetails,
    ExecutionAppliedPayload,
    FeePayload,
    FutureContractDetails,
    FxContractDetails,
    OptionContractDetails,
    PositionSnapshotEntry,
    PositionSnapshotPayload,
    ProfitTargetPayload,
    RoutingAdmissionOutcomePayload,
    SourceEventKind,
    SourceEventPayload,
    SourceReceiptPayload,
)

__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "CommercialIdentity",
    "CryptoDerivativeDetails",
    "Environment",
    "EventEnvelope",
    "EventType",
    "EvidenceClass",
    "ExecutionAppliedPayload",
    "FeePayload",
    "FutureContractDetails",
    "FxContractDetails",
    "InstrumentIdentity",
    "Money",
    "OptionContractDetails",
    "PortfolioIdentity",
    "PositionSnapshotEntry",
    "PositionSnapshotPayload",
    "PrivateAccountIdentity",
    "ProfitTargetPayload",
    "PublicationIdentity",
    "RoutingAdmissionOutcomePayload",
    "SourceEventKind",
    "SourceEventPayload",
    "SourceIdentity",
    "SourceReceiptPayload",
    "build_subject",
    "compute_payload_hash",
]
