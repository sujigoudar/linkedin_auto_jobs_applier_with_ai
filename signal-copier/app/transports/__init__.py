"""Email transport adapters for Signal Copier.

This package provides pluggable email transports that feed into the canonical
signal processing pipeline: SourceReceipt → ProviderIdentity → Parser →
NormalizedSignal → Admission → PortfolioAllocator → Execution.

Supported transports:
- AgentMail: event-driven API-controlled agent inboxes (primary)
- Gmail/IMAP: legacy IMAP polling (fallback; see app.sources.email_source)
- Microsoft Graph: tenant-isolated multi-inbox support
"""
from app.transports.broker_operations_classifier import (
    BrokerOperationEvent,
    BrokerOperationsClassifier,
    EventSeverity,
    OperationEventType,
)
from app.transports.broker_operations_escalator import (
    BrokerOperationsIncidentEscalator,
    EscalationAction,
    IncidentEscalation,
)
from app.transports.provider_identity import (
    InboxProviderConfig,
    ProviderIdentity,
    ProviderIdentityResolver,
    ProviderSenderMapping,
)

__all__ = [
    "BrokerOperationEvent",
    "BrokerOperationsClassifier",
    "BrokerOperationsIncidentEscalator",
    "EscalationAction",
    "EventSeverity",
    "IncidentEscalation",
    "OperationEventType",
    "ProviderIdentity",
    "ProviderIdentityResolver",
    "InboxProviderConfig",
    "ProviderSenderMapping",
]
