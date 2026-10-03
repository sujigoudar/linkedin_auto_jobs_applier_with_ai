"""Email transport adapters for Signal Copier.

This package provides pluggable email transports that feed into the canonical
signal processing pipeline: SourceReceipt → ProviderIdentity → Parser →
NormalizedSignal → Admission → PortfolioAllocator → Execution.

Supported transports:
- AgentMail: event-driven API-controlled agent inboxes (primary)
- Gmail/IMAP: legacy IMAP polling (fallback; see app.sources.email_source)
- Microsoft Graph: tenant-isolated multi-inbox support
"""
