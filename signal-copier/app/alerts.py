"""Alert notification system for critical events.

Provides AlertSink for recording alerts (protection deficit, halt, unknown
submission, skipped allocation, venue>tracked adoption). Each alert:
- Persists to the `alerts` table with kind, account_id, message, payload
- Optionally POSTs to config.ALERT_WEBHOOK_URL (failures logged, never raised)
- Is visible via GET /alerts?unacknowledged=1 and acknowledged via POST /alerts/{id}/ack
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db import SignalStore

logger = logging.getLogger(__name__)


class AlertSink:
    """Records alerts for critical operational events.

    Thread-safe: uses the SignalStore's own serialization (_immediate context
    manager) to ensure atomicity across threads/processes.
    """

    def __init__(self, store: SignalStore, http_client: Any | None = None):
        """Initialize AlertSink.

        Args:
            store: SignalStore instance for persistence
            http_client: Optional HTTP client for webhook delivery (e.g., httpx.AsyncClient).
                        If provided and config.ALERT_WEBHOOK_URL is set, POSTs each
                        alert. Failures are logged, never raised.
        """
        self.store = store
        self.http_client = http_client

    def record(
        self,
        kind: str,
        account_id: str | None,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Record an alert and optionally POST to webhook.

        Args:
            kind: Alert type (e.g., "protection_deficit", "loss_halt", "unknown_submission",
                             "skipped_allocation", "venue_adoption")
            account_id: Account UUID, or None for system-level alerts
            message: Human-readable description
            payload: Structured data (dict), JSON-serialized

        Returns:
            Alert ID (UUID string) for later acknowledgment

        Raises: Never — failures are logged and suppressed.
        """
        try:
            alert_id = self.store.persist_alert(
                kind=kind,
                account_id=account_id,
                message=message,
                payload=payload,
            )

            # Deliver to webhook (if configured)
            self._maybe_post_webhook(alert_id, kind, account_id, message, payload)

            return alert_id
        except Exception:
            logger.exception(f"Failed to record alert (kind={kind}, account_id={account_id})")
            # Return a synthetic ID; the caller may continue
            return f"alert-failed-{datetime.now(timezone.utc).isoformat()}"

    def _maybe_post_webhook(
        self, alert_id: str, kind: str, account_id: str | None, message: str, payload: dict[str, Any] | None
    ) -> None:
        """POST the alert to config.ALERT_WEBHOOK_URL if configured and http_client is available.

        Failures are logged, never raised.
        """
        if not self.http_client:
            return

        from app.config import config

        if not config.ALERT_WEBHOOK_URL:
            return

        try:
            # Build JSON payload
            alert_json = {
                "id": alert_id,
                "kind": kind,
                "account_id": account_id,
                "message": message,
                "payload": payload or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # POST synchronously (blocking). In an async context, consider
            # spawning a background task instead.
            # Note: actual delivery depends on self.http_client being passed
            # (not done in __init__ by default for decoupling).
            if hasattr(self.http_client, "post"):
                # httpx.Client or httpx.AsyncClient interface
                self.http_client.post(config.ALERT_WEBHOOK_URL, json=alert_json)
        except Exception as e:
            logger.warning(f"Failed to POST alert to webhook {config.ALERT_WEBHOOK_URL}: {e}")
