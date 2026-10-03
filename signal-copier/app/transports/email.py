"""Abstract EmailTransport interface for pluggable email backends.

This module defines the contract every email transport must implement. Email
transports are neutral carriers for signal ingestion, operational observations,
and escalation notifications. They provide NO financial authority.

Design principles:
1. All email evidence flows through the same SourceReceipt → Parser →
   NormalizedSignal pipeline as Telegram, Discord, TradingView, webhooks, etc.
2. Email content is an OBSERVATION, never a financial decision. An email can
   never directly create, cancel, replace, resize, route, or close a broker order.
3. Webhook-based transports are preferred over polling (event-driven vs latency).
4. Deterministic provider identity mapping: inbox + sender + domain + config →
   ProviderIdentity. Unknown senders → NEEDS_REVIEW, never inherited parser.
5. Email is evidence, not system-of-record. Broker operational emails are
   reconciled against the authoritative broker API before canonical state changes.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class InboxRole(str, Enum):
    """Role a mailbox plays in the system."""
    SIGNALS = "signals"  # Provider trading alerts and signal ingestion
    OPERATIONS = "operations"  # Broker, infrastructure, security incidents
    REPORTS = "reports"  # Outbound summaries and notifications


@dataclass
class EmailAttachmentMetadata:
    """Metadata about an email attachment (no content stored here)."""
    filename: str
    mime_type: str
    size_bytes: int
    hash_sha256: str  # For deduplication and integrity
    quarantine_reason: Optional[str] = None  # If not approved for extraction


@dataclass
class SourceReceipt:
    """Raw email evidence, before provider identity resolution or parsing.

    Mirrors signal_copier/app/db.py's source_receipts table schema.
    This is the input to ProviderIdentityResolver.
    """
    message_id: str  # Email's native Message-ID header (RFC 5322 globally unique)
    inbox_id: str  # Transport + inbox role (e.g., "agentmail:signals" or "gmail:inbox")
    thread_id: str  # Conversation thread ID (email thread_id or agentmail thread_id)
    sender: str  # From address
    recipients: list[str]  # To addresses
    received_at: datetime
    subject: str
    body_hash: str  # SHA256(body) for dedup and integrity
    body_representation: str  # Plain text or safe HTML excerpt (sanitized, truncated)
    attachment_metadata: list[EmailAttachmentMetadata]

    # Transport-specific metadata
    transport_name: str  # "agentmail", "gmail", "microsoft_graph", etc.
    event_id: Optional[str] = None  # Webhook event ID (for idempotency)
    webhook_received_at: Optional[datetime] = None  # When webhook arrived

    # Parent/reply relationships (for multi-email corrections/retractions)
    in_reply_to: Optional[str] = None  # RFC 5322 In-Reply-To header
    references: list[str] = None  # RFC 5322 References header


@dataclass
class EmailMessageContent:
    """Processed email content ready for parser.

    Extracts safe, permitted content from raw email after attachment
    quarantine and MIME validation.
    """
    plain_text: Optional[str]  # Extracted plain text
    html_excerpt: Optional[str]  # Sanitized HTML (limited length)
    subject: str
    sender: str
    received_at: datetime
    attachments: list[EmailAttachmentMetadata]  # Metadata only, no content


class EmailTransport(abc.ABC):
    """Abstract base class for email transport implementations.

    Every subclass must implement webhook reception (event-driven) or polling
    (with latency disclosure), message retrieval, and attachment handling.
    """

    name: str  # "agentmail", "gmail", "microsoft_graph", etc.

    @abc.abstractmethod
    async def start(self) -> None:
        """Begin listening for incoming emails.

        For webhook-based transports, this starts the webhook receiver.
        For polling transports, this starts the background poll loop.
        """

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop listening and release resources."""

    @abc.abstractmethod
    async def get_receipt(
        self,
        inbox_id: str,
        message_id: str
    ) -> SourceReceipt:
        """Retrieve raw email evidence.

        Called when webhook payload is truncated and full message needed,
        or when historical import requires message content.
        """

    @abc.abstractmethod
    async def get_message_content(
        self,
        inbox_id: str,
        message_id: str
    ) -> EmailMessageContent:
        """Retrieve processed email content ready for parsing.

        Implements MIME parsing, attachment quarantine, and sanitization.
        """

    @abc.abstractmethod
    async def get_attachment(
        self,
        inbox_id: str,
        message_id: str,
        attachment_hash: str
    ) -> bytes:
        """Retrieve attachment content by hash.

        Only called if attachment passed security gates and extraction
        is approved. Raises ValueError if attachment not found or hash
        mismatch.
        """

    @abc.abstractmethod
    async def health_status(self) -> dict[str, Any]:
        """Return health/status information for monitoring.

        Must include:
        - connection_state: "connected" | "disconnected" | "unknown"
        - webhook_state: "active" | "inactive" | "unknown" (if applicable)
        - last_message_received_at: datetime or None
        - last_error: str or None
        - inboxes: {inbox_id: {messages_received, errors, latency_ms}}
        """

    @abc.abstractmethod
    async def send_notification(
        self,
        to_inbox: str,  # "operations" or "reports"
        subject: str,
        body: str,
        severity: str = "informational"  # "critical" | "warning" | "informational"
    ) -> bool:
        """Send outbound notification email.

        Used for incident escalation and daily reports. Never sends
        secrets, credentials, or unnecessarily sensitive account info.

        Returns True if accepted, False if failed.
        """
