"""Email-to-ProviderIdentity deterministic mapping.

Maps email metadata (inbox, sender, domain, configuration) to a provider
identifier deterministically. Unknown senders produce a NEEDS_REVIEW state
rather than being silently dropped or assigned to an arbitrary provider.

Key design decisions:

1. DETERMINISTIC: same (inbox, sender) always → same provider, no inference
2. CONFIGURATION-DRIVEN: the mapping is read from a config file or database,
   not learned or guessed from signal content
3. FAIL-CLOSED: unknown senders → NEEDS_REVIEW state that appears in the UI
   for manual routing, never inherited from a broader default
4. DOMAIN-BASED PRIMARY: most signal providers send from a consistent domain
   (TradingView always sends from tradingview.com, broker alerts from broker.com)
5. SENDER-SPECIFIC FALLBACK: specific email addresses can be mapped independently
   (e.g., alice@example.com → analyst_alice, bob@example.com → analyst_bob)
6. INBOX-SCOPED: inbox_role (signals/operations/reports) can further refine the
   mapping (e.g., trading alerts in signals@, operational emails in operations@)

Architecture:
    SourceReceipt (from email transport)
        ↓
    ProviderIdentityResolver.resolve(inbox, sender)
        ↓
    ProviderIdentity (provider + analyst, or NEEDS_REVIEW)
        ↓
    Signal is emitted with resolved provider/analyst
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderIdentity:
    """Result of email-to-provider mapping."""
    provider: Optional[str] = None  # The mapped provider ID, or None if NEEDS_REVIEW
    analyst: Optional[str] = None  # Specific analyst/sender within provider, or None
    needs_review: bool = False  # True if sender is unknown and requires manual routing
    reason: str = ""  # Human-readable explanation for NEEDS_REVIEW or mapping decision


@dataclass
class ProviderSenderMapping:
    """Configuration for mapping one sender to a provider."""
    sender_pattern: str  # Email address or domain (alice@example.com or @example.com)
    provider_id: str  # The provider this sender maps to
    analyst_id: Optional[str] = None  # Optional analyst override (defaults to sender name)
    enabled: bool = True


@dataclass
class InboxProviderConfig:
    """Configuration for one inbox role's provider mapping."""
    inbox_role: str  # "signals", "operations", or "reports"
    senders: dict[str, ProviderSenderMapping] = None  # sender_pattern -> mapping
    default_provider: Optional[str] = None  # Fallback when no sender matches
    require_known_sender: bool = True  # If True, unknown senders get NEEDS_REVIEW

    def __post_init__(self):
        if self.senders is None:
            self.senders = {}


class ProviderIdentityResolver:
    """Deterministic email-to-provider mapping."""

    def __init__(self, configs: Optional[list[InboxProviderConfig]] = None):
        """Initialize with optional configurations.

        Args:
            configs: List of InboxProviderConfig for each inbox role
        """
        self.configs: dict[str, InboxProviderConfig] = {}
        if configs:
            for config in configs:
                self.configs[config.inbox_role] = config

    def resolve(
        self,
        sender: str,
        inbox_role: str = "signals",
    ) -> ProviderIdentity:
        """Deterministically map (sender, inbox) to a provider.

        Args:
            sender: Email address of the sender (e.g., alerts@tradingview.com)
            inbox_role: Role of the receiving inbox ("signals", "operations", "reports")

        Returns:
            ProviderIdentity with provider and analyst, or NEEDS_REVIEW if unknown
        """
        config = self.configs.get(inbox_role)

        if not config:
            # No configuration for this inbox role
            return ProviderIdentity(
                needs_review=True,
                reason=f"No provider mapping configured for inbox role '{inbox_role}'"
            )

        # Try exact sender match first
        if sender in config.senders:
            mapping = config.senders[sender]
            if not mapping.enabled:
                return ProviderIdentity(
                    needs_review=True,
                    reason=f"Sender {sender} is disabled"
                )
            return ProviderIdentity(
                provider=mapping.provider_id,
                analyst=mapping.analyst_id or self._extract_analyst_from_sender(sender),
                reason=f"Matched sender {sender}"
            )

        # Try domain match (@example.com)
        if "@" in sender:
            domain = "@" + sender.split("@")[1]
            if domain in config.senders:
                mapping = config.senders[domain]
                if not mapping.enabled:
                    return ProviderIdentity(
                        needs_review=True,
                        reason=f"Domain {domain} is disabled"
                    )
                return ProviderIdentity(
                    provider=mapping.provider_id,
                    analyst=mapping.analyst_id or self._extract_analyst_from_sender(sender),
                    reason=f"Matched domain {domain}"
                )

        # No match found
        if config.require_known_sender or not config.default_provider:
            return ProviderIdentity(
                needs_review=True,
                reason=f"Unknown sender {sender} for inbox role {inbox_role}"
            )

        # Use default provider if configured
        return ProviderIdentity(
            provider=config.default_provider,
            analyst=self._extract_analyst_from_sender(sender),
            reason=f"Using default provider for unknown sender {sender}"
        )

    def add_mapping(
        self,
        inbox_role: str,
        sender_pattern: str,
        provider_id: str,
        analyst_id: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        """Add or update a sender-to-provider mapping.

        Args:
            inbox_role: The inbox role this mapping applies to
            sender_pattern: Email or domain pattern (alice@example.com or @example.com)
            provider_id: The provider this sender maps to
            analyst_id: Optional analyst override
            enabled: Whether this mapping is active
        """
        if inbox_role not in self.configs:
            self.configs[inbox_role] = InboxProviderConfig(inbox_role=inbox_role)

        mapping = ProviderSenderMapping(
            sender_pattern=sender_pattern,
            provider_id=provider_id,
            analyst_id=analyst_id,
            enabled=enabled,
        )
        self.configs[inbox_role].senders[sender_pattern] = mapping
        logger.info(f"Added {inbox_role} mapping: {sender_pattern} → {provider_id}")

    def _extract_analyst_from_sender(self, sender: str) -> Optional[str]:
        """Extract analyst name from email address.

        Examples:
            alice@example.com → alice
            alerts@tradingview.com → alerts

        Returns None if sender format is invalid.
        """
        if "@" not in sender:
            return None
        local_part = sender.split("@")[0]
        # Remove common prefixes
        for prefix in ("alerts", "signals", "bot", "noreply"):
            if local_part.startswith(prefix + "_"):
                local_part = local_part[len(prefix) + 1:]
                break
        return local_part if local_part else None
