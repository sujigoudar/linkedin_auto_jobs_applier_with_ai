"""Incident: AD-21 "Commercial incidents and obligations" -- a real,
tenant-scoped record coordinating an incident across rights, payment,
publication or customer exposure. See dashboard_spec/screens/AD-21.md
for the full screen contract this implements a bounded slice of.

Bounded scope: no automated monitoring/alerting pipeline exists in this
build, so nothing here fabricates a stream of incoming incidents --
rows are only ever created directly against real affected objects this
tenant already owns (a Subscription, a Product, a PortfolioVersion...),
the same "plain id reference, not a real uploaded document" precedent
`SupportCase.attachment_ids` already sets for `evidence_ids` below.

A plain, mutable record (like `Product`/`SupportCase`, not append-only
like `PortfolioVersion`/`AuditEvent`) -- its own state (acknowledged,
assigned, resolved) is exactly what F-INCIDENT's steps update in place;
every state-changing action additionally appends an `AuditEvent` (see
app/services/incident.py), which is what actually gives AD-21-P03
"Timeline" its immutable, never-reordered history.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentService(str, enum.Enum):
    RIGHTS = "rights"
    PAYMENT = "payment"
    PUBLICATION = "publication"
    CUSTOMER_EXPOSURE = "customer_exposure"


class IncidentSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentState(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ASSIGNED = "ASSIGNED"
    RESOLVED = "RESOLVED"


class Incident(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    service: Mapped[IncidentService] = mapped_column(Enum(IncidentService, native_enum=False), nullable=False)
    severity: Mapped[IncidentSeverity] = mapped_column(Enum(IncidentSeverity, native_enum=False), nullable=False)
    state: Mapped[IncidentState] = mapped_column(
        Enum(IncidentState, native_enum=False), nullable=False, default=IncidentState.OPEN
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    #: The one real affected object this incident is about (e.g.
    #: "subscription"/a Subscription id, "product"/a Product id) --
    #: validated against the caller's own tenant at the service layer,
    #: same "no cross-tenant IDs" discipline as SupportCase.related_object_id.
    affected_object_type: Mapped[str | None] = mapped_column(String, nullable=True)
    affected_object_id: Mapped[str | None] = mapped_column(String, nullable=True)

    assignee_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Plain id references only -- see this module's own docstring.
    evidence_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
