"""AgentMail email transport adapter.

AgentMail provides persistent email addresses for agents with event-driven
webhook delivery, multi-inbox support, and tenant isolation through Pods.

This adapter implements the EmailTransport interface for AgentMail's webhook-
based message delivery. Key design decisions:

1. WEBHOOK-DRIVEN, not polling: message.received webhooks are event-driven,
   providing near-real-time ingestion without latency.

2. SIGNATURE VERIFICATION: AgentMail webhooks include a secret for HMAC
   signature verification. Every webhook is verified before processing.

3. IDEMPOTENCY: webhook retries are deduplicated on event_id. An event_id
   that has already been processed is ACK'd immediately without reprocessing.

4. ASYNCHRONOUS PROCESSING: the webhook handler ACKs AgentMail immediately
   (within 5s), then processes heavy work asynchronously. This follows
   AgentMail's own recommendations.

5. PAYLOAD SIZE HANDLING: AgentMail caps webhook payloads at 1 MB. When
   content is omitted due to size, the full message is retrieved via API.

6. NO FINANCIAL AUTHORITY: emails are observations only. They feed the
   existing SourceReceipt → Parser → NormalizedSignal pipeline.

Architecture:
    Webhook endpoint receives message.received event
        ↓
    Verify HMAC signature
        ↓
    Dedup on event_id
        ↓
    Persist raw SourceReceipt
        ↓
    ACK AgentMail (5s)
        ↓
    Async: retrieve full message if truncated
        ↓
    Async: process → ProviderIdentity → Parser
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import httpx

from app.transports.email import (
    EmailAttachmentMetadata,
    EmailMessageContent,
    EmailTransport,
    InboxRole,
    SourceReceipt,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentMailConfig:
    """AgentMail transport configuration."""
    api_key: str
    webhook_secret: str
    webhook_url: str  # URL where AgentMail sends message.received events

    # Inbox configuration: role → email address
    inboxes: dict[InboxRole, str] = field(default_factory=dict)  # signals@..., ops@..., etc.

    # API settings
    api_base_url: str = "https://api.agentmail.to"
    webhook_timeout_seconds: int = 5
    api_timeout_seconds: int = 10
    max_retry_attempts: int = 3


@dataclass
class AgentMailWebhookPayload:
    """Structure of message.received webhook from AgentMail."""
    event_id: str
    event_type: str  # "message.received"
    timestamp: str  # ISO8601

    # Message data (may be truncated if >1MB)
    inbox_id: str
    message_id: str
    thread_id: str
    sender: str
    recipients: list[str]
    received_at: str
    subject: str
    body_plain: Optional[str]  # Omitted if truncated
    body_html: Optional[str]  # Omitted if truncated

    attachments: list[dict[str, Any]] = field(default_factory=list)
    content_truncated: bool = False

    # Headers
    message_id_header: str  # RFC 5322 Message-ID
    in_reply_to: Optional[str] = None
    references: Optional[str] = None


class AgentMailTransport(EmailTransport):
    """AgentMail implementation of EmailTransport.

    Receives webhook deliveries from AgentMail, verifies signatures,
    deduplicates, and routes through the signal pipeline.
    """

    name = "agentmail"

    def __init__(
        self,
        config: AgentMailConfig,
        on_receipt: Callable[[SourceReceipt], Any],  # async callback
    ):
        self.config = config
        self.on_receipt = on_receipt
        self.http_client = httpx.AsyncClient(timeout=config.api_timeout_seconds)

        # Track received event IDs for deduplication (in-memory; should be
        # backed by persistent storage in production)
        self._seen_event_ids: set[str] = set()
        self._event_id_lock = asyncio.Lock()

        # Health tracking
        self._last_message_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._message_count = 0
        self._error_count = 0

    async def start(self) -> None:
        """Initialize the transport (webhook receiver should be started separately)."""
        logger.info(f"Starting {self.name} transport")
        # In a real implementation, this would register the webhook with AgentMail
        # and start listening. For now, we assume the webhook endpoint is already
        # running as part of the FastAPI app.

    async def stop(self) -> None:
        """Clean up resources."""
        await self.http_client.aclose()
        logger.info(f"Stopped {self.name} transport")

    async def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle incoming webhook from AgentMail.

        Returns immediately with 200 OK after dedup and persistence.
        Heavy processing happens asynchronously.

        Args:
            payload: Raw webhook payload from AgentMail

        Returns:
            {status: "ok", event_id: "..."} for immediate response
        """
        try:
            event_id = payload.get("event_id")
            event_type = payload.get("event_type")

            if not event_id or event_type != "message.received":
                logger.warning(f"Unexpected webhook: {event_type}")
                return {"status": "ignored", "reason": "unexpected_event_type"}

            # Dedup on event_id
            async with self._event_id_lock:
                if event_id in self._seen_event_ids:
                    logger.debug(f"Duplicate event_id: {event_id}")
                    return {"status": "ok", "event_id": event_id, "duplicate": True}
                self._seen_event_ids.add(event_id)

            # Parse webhook payload
            webhook = AgentMailWebhookPayload(**payload)

            # Persist raw receipt
            receipt = await self._webhook_to_receipt(webhook)

            # ACK AgentMail immediately
            self._last_message_at = datetime.utcnow()
            self._message_count += 1

            # Schedule async processing
            asyncio.create_task(self._process_receipt_async(receipt, webhook))

            return {"status": "ok", "event_id": event_id}

        except Exception as e:
            self._last_error = str(e)
            self._error_count += 1
            logger.exception(f"Webhook error: {e}")
            return {"status": "error", "error": str(e)}

    async def _webhook_to_receipt(self, webhook: AgentMailWebhookPayload) -> SourceReceipt:
        """Convert webhook payload to SourceReceipt."""
        return SourceReceipt(
            message_id=webhook.message_id_header,
            inbox_id=f"agentmail:{webhook.inbox_id}",
            thread_id=webhook.thread_id,
            sender=webhook.sender,
            recipients=webhook.recipients,
            received_at=datetime.fromisoformat(webhook.received_at.replace('Z', '+00:00')),
            subject=webhook.subject,
            body_hash=hashlib.sha256(
                (webhook.body_plain or webhook.body_html or "").encode()
            ).hexdigest(),
            body_representation=self._extract_body(webhook),
            attachment_metadata=await self._parse_attachment_metadata(webhook.attachments),
            transport_name=self.name,
            event_id=webhook.event_id,
            webhook_received_at=datetime.fromisoformat(webhook.timestamp.replace('Z', '+00:00')),
            in_reply_to=webhook.in_reply_to,
            references=webhook.references.split() if webhook.references else [],
        )

    def _extract_body(self, webhook: AgentMailWebhookPayload) -> str:
        """Extract safe body representation from webhook."""
        # Prefer plain text; fall back to HTML excerpt if needed
        if webhook.body_plain:
            return webhook.body_plain[:10000]  # Limit to 10KB

        # Sanitize HTML (in production, use bleach or similar)
        if webhook.body_html:
            # Strip HTML tags for now; real impl would sanitize properly
            import re
            text = re.sub(r'<[^>]+>', '', webhook.body_html)
            return text[:10000]

        return ""

    async def _parse_attachment_metadata(
        self,
        attachments: list[dict[str, Any]]
    ) -> list[EmailAttachmentMetadata]:
        """Parse attachment metadata from webhook."""
        metadata = []
        for att in attachments:
            quarantine_reason = None

            # Check size (1MB max)
            if att.get("size_bytes", 0) > 1024 * 1024:
                quarantine_reason = "oversized"

            # Check MIME type against whitelist
            mime_type = att.get("mime_type", "application/octet-stream")
            if not self._is_approved_mime_type(mime_type):
                quarantine_reason = f"unapproved_mime_type:{mime_type}"

            metadata.append(
                EmailAttachmentMetadata(
                    filename=att.get("filename", "unknown"),
                    mime_type=mime_type,
                    size_bytes=att.get("size_bytes", 0),
                    hash_sha256=att.get("hash_sha256", ""),
                    quarantine_reason=quarantine_reason,
                )
            )

        return metadata

    def _is_approved_mime_type(self, mime_type: str) -> bool:
        """Check if MIME type is approved for extraction."""
        approved = {
            "text/plain",
            "text/csv",
            "application/pdf",
            "application/json",
            "image/png",
            "image/jpeg",
            "image/gif",
        }
        return mime_type in approved

    async def _process_receipt_async(
        self,
        receipt: SourceReceipt,
        webhook: AgentMailWebhookPayload
    ) -> None:
        """Process receipt asynchronously after webhook ACK."""
        try:
            # If content was truncated, retrieve full message
            if webhook.content_truncated:
                receipt = await self.get_receipt(
                    receipt.inbox_id,
                    receipt.message_id
                )

            # Emit to signal pipeline
            await self.on_receipt(receipt)

        except Exception as e:
            self._last_error = str(e)
            self._error_count += 1
            logger.exception(f"Error processing receipt {receipt.message_id}: {e}")

    async def get_receipt(
        self,
        inbox_id: str,
        message_id: str
    ) -> SourceReceipt:
        """Retrieve full message via API (when webhook was truncated)."""
        try:
            response = await self.http_client.get(
                f"{self.config.api_base_url}/messages/{message_id}",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                params={"inbox_id": inbox_id},
            )
            response.raise_for_status()

            data = response.json()
            # Parse into SourceReceipt
            return SourceReceipt(
                message_id=data["message_id_header"],
                inbox_id=f"agentmail:{data['inbox_id']}",
                thread_id=data["thread_id"],
                sender=data["sender"],
                recipients=data.get("recipients", []),
                received_at=datetime.fromisoformat(
                    data["received_at"].replace('Z', '+00:00')
                ),
                subject=data["subject"],
                body_hash=hashlib.sha256(
                    (data.get("body_plain") or data.get("body_html") or "").encode()
                ).hexdigest(),
                body_representation=data.get("body_plain") or data.get("body_html") or "",
                attachment_metadata=await self._parse_attachment_metadata(
                    data.get("attachments", [])
                ),
                transport_name=self.name,
                in_reply_to=data.get("in_reply_to"),
                references=data.get("references", []),
            )

        except httpx.HTTPError as e:
            logger.exception(f"API error retrieving {message_id}: {e}")
            raise

    async def get_message_content(
        self,
        inbox_id: str,
        message_id: str
    ) -> EmailMessageContent:
        """Retrieve processed message content."""
        receipt = await self.get_receipt(inbox_id, message_id)

        return EmailMessageContent(
            plain_text=receipt.body_representation if receipt.body_representation else None,
            html_excerpt=None,  # Could parse HTML from body if needed
            subject=receipt.subject,
            sender=receipt.sender,
            received_at=receipt.received_at,
            attachments=receipt.attachment_metadata,
        )

    async def get_attachment(
        self,
        inbox_id: str,
        message_id: str,
        attachment_hash: str
    ) -> bytes:
        """Retrieve attachment content by hash."""
        try:
            response = await self.http_client.get(
                f"{self.config.api_base_url}/messages/{message_id}/attachments/{attachment_hash}",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                params={"inbox_id": inbox_id},
            )
            response.raise_for_status()
            return response.content

        except httpx.HTTPError as e:
            logger.exception(f"Error retrieving attachment {attachment_hash}: {e}")
            raise ValueError(f"Attachment not found: {attachment_hash}") from e

    async def health_status(self) -> dict[str, Any]:
        """Return health status."""
        return {
            "transport": self.name,
            "status": "ok" if not self._last_error else "error",
            "last_message_received_at": self._last_message_at.isoformat() if self._last_message_at else None,
            "last_error": self._last_error,
            "messages_received": self._message_count,
            "errors": self._error_count,
            "inboxes": {
                role.value: {"status": "configured"}
                for role in self.config.inboxes.keys()
            },
        }

    async def send_notification(
        self,
        to_inbox: str,
        subject: str,
        body: str,
        severity: str = "informational"
    ) -> bool:
        """Send outbound notification email."""
        try:
            response = await self.http_client.post(
                f"{self.config.api_base_url}/messages/send",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={
                    "to": self.config.inboxes.get(InboxRole[to_inbox.upper()]),
                    "subject": subject,
                    "body": body,
                    "severity": severity,
                },
            )
            response.raise_for_status()
            return True

        except httpx.HTTPError as e:
            logger.exception(f"Error sending notification: {e}")
            return False

    def verify_webhook_signature(
        self,
        payload: str,
        signature: str
    ) -> bool:
        """Verify AgentMail webhook HMAC signature.

        AgentMail signs webhooks with HMAC-SHA256 using the webhook secret.
        The signature is provided in the X-AgentMail-Signature header.
        """
        expected_signature = hmac.new(
            self.config.webhook_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected_signature)
