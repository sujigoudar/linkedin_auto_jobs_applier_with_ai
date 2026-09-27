"""SupportCase: CU-14 "Support and incident case" -- a customer's own,
tenant-scoped support case, per dashboard_spec/screens/CU-14.md's
F-SUPPORT form.

Attachments are plain id references, not real uploaded documents --
matching AD-02's own precedent (app/models/rights.py's evidence
references): a real malware-scan/upload pipeline doesn't exist in this
build, so nothing here pretends a scanned file was actually received.

`related_object_id` is validated against the caller's own tenant scope
at the service layer (app/services/support_case.py), never trusted as
given -- "No cross-tenant IDs" (CU-14's own field help text).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, Enum, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SupportCaseCategory(str, enum.Enum):
    BILLING = "billing"
    DELIVERY = "delivery"
    CONNECTION = "connection"
    PERFORMANCE = "performance"
    SAFETY = "safety"
    ACCESS = "access"


class SupportCaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class SupportCase(Base):
    __tablename__ = "support_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_support_case_membership",
        ),
    )

    case_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    category: Mapped[SupportCaseCategory] = mapped_column(Enum(SupportCaseCategory, native_enum=False), nullable=False)
    related_object_id: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    attachment_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    status: Mapped[SupportCaseStatus] = mapped_column(
        Enum(SupportCaseStatus, native_enum=False), nullable=False, default=SupportCaseStatus.OPEN
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
