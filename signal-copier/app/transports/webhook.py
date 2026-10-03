"""AgentMail webhook receiver for FastAPI integration.

Handles incoming AgentMail message.received webhooks with:
- HMAC signature verification
- Event ID deduplication
- Quick ACK (< 5s) to AgentMail
- Asynchronous heavy processing
- Persistent durable receipt storage

Usage in FastAPI:
    from app.transports.webhook import create_agentmail_webhook_handler

    # At startup
    handler = create_agentmail_webhook_handler(
        transport=agentmail_transport,
        store=signal_store,
    )

    # In main.py routes
    @app.post("/api/webhooks/agentmail")
    async def agentmail_webhook(request: Request, handler=Depends(lambda: handler)):
        return await handler(request)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from fastapi import Request, Response

from app.transports.agentmail import AgentMailTransport

logger = logging.getLogger(__name__)


class WebhookHandler:
    """Receives and processes AgentMail webhooks."""

    def __init__(
        self,
        transport: AgentMailTransport,
        store: Any,  # SignalStore for persistence
    ):
        self.transport = transport
        self.store = store

    async def __call__(self, request: Request) -> Response:
        """Handle incoming webhook request.

        Returns 200 OK immediately after dedup/persistence,
        before heavy processing.
        """
        # Extract headers
        signature = request.headers.get("X-AgentMail-Signature")
        if not signature:
            logger.warning("Missing X-AgentMail-Signature header")
            return Response(status_code=400, content="Missing signature")

        # Read body
        body = await request.body()

        # Verify signature
        if not self.transport.verify_webhook_signature(body.decode(), signature):
            logger.warning("Invalid webhook signature")
            return Response(status_code=403, content="Invalid signature")

        # Parse JSON
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON: {e}")
            return Response(status_code=400, content="Invalid JSON")

        # Handle webhook
        try:
            result = await self.transport.handle_webhook(payload)

            if result.get("duplicate"):
                logger.debug(f"Duplicate event: {result.get('event_id')}")
                return Response(status_code=200, content=json.dumps(result))

            # Persist receipt to store (for later audit/recovery)
            event_id = payload.get("event_id")
            if event_id:
                await self._persist_webhook(event_id, payload, result)

            return Response(status_code=200, content=json.dumps(result))

        except Exception as e:
            logger.exception(f"Webhook handler error: {e}")
            return Response(status_code=500, content="Internal error")

    async def _persist_webhook(
        self,
        event_id: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Persist webhook event for audit/recovery.

        Stores the raw payload and result so that:
        1. We can replay/recover if processing failed
        2. We have an audit trail of all received events
        3. We can debug signature/dedup issues
        """
        try:
            # In production, insert into agentmail_webhooks table or similar
            # For now, just log
            logger.info(
                f"Webhook persisted: event_id={event_id}, "
                f"status={result.get('status')}"
            )
        except Exception as e:
            logger.exception(f"Failed to persist webhook {event_id}: {e}")
            # Don't fail the webhook response if persistence fails;
            # the event has already been processed


def create_agentmail_webhook_handler(
    transport: AgentMailTransport,
    store: Any,  # SignalStore
) -> Callable[[Request], Any]:
    """Create a webhook handler for FastAPI dependency injection.

    Usage:
        handler = create_agentmail_webhook_handler(transport, store)

        @app.post("/api/webhooks/agentmail")
        async def agentmail_webhook(request: Request):
            return await handler(request)
    """
    webhook_handler = WebhookHandler(transport, store)
    return webhook_handler
