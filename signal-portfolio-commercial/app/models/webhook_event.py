"""Persisted webhook event IDs. docs/08 requires persisting event IDs
before asynchronous processing, and handling duplicate/reordered events
by reconciling current authoritative state rather than comparing
arrival order alone. This table is the durable record that a given
processor event_id has been seen at all; it is deliberately separate
from whatever authoritative state (Subscription) processing that event
goes on to update.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProcessedWebhookEvent(Base):
    __tablename__ = "processed_webhook_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
