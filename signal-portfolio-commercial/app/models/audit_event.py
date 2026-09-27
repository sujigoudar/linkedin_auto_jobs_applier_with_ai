"""AuditEvent: AD-18 "Audit log and release evidence" -- an immutable,
append-only record of a real state-changing action. See
dashboard_spec/screens/AD-18.md for the full screen contract this
implements a bounded slice of.

Append-only like ledger_entries/portfolio_versions
(app/db.py's `enforce_append_only`) -- "Audit cannot be edited through
UI" (AD-18's own acceptance text) is a database-level guarantee here,
not just an application convention a future caller could route around.

This is the FIRST real writer: app/services/staff_access.py's
invite_staff_member/revoke_staff_member now append one of these on
every real membership change, resolving the "no audit-log store
exists" gap AD-16/AD-11 both documented. Other services do not write
here yet -- this is a real, working append-only store with one real
caller, not a schema built ahead of any actual writer.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditOutcome(str, enum.Enum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    ERROR = "ERROR"


class AuditEvent(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    actor_user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[AuditOutcome] = mapped_column(Enum(AuditOutcome, native_enum=False), nullable=False)

    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
