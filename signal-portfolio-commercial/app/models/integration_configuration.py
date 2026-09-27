"""IntegrationConfiguration: AD-17 "Integrations, data rights and
quotas" -- a tenant's own declared, inactive third-party integration
configuration. See dashboard_spec/screens/AD-17.md's F-INTEGRATION form
for the full contract this implements a bounded slice of.

Saving a configuration never calls anything -- nothing in this build
reads this table to make a real outbound request. "Save inactive
config" (AD-17's own action label) is literally true.

`credential_ref` is an opaque reference, never a real secret -- "No
secret echo" (AD-17's own field help text). No real credential is ever
stored, accepted, or transmitted by this module.
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


class IntegrationPurpose(str, enum.Enum):
    RESEARCH = "research"
    QUOTES = "quotes"
    REFERENCE = "reference"
    PUBLICATION = "publication"
    BILLING = "billing"
    MONITORING = "monitoring"


class IntegrationEnvironment(str, enum.Enum):
    TEST = "test"
    DEMO = "demo"
    LIVE = "live"


class IntegrationConfiguration(Base):
    __tablename__ = "integration_configurations"

    integration_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    provider_registry_id: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[IntegrationPurpose] = mapped_column(Enum(IntegrationPurpose, native_enum=False), nullable=False)
    environment: Mapped[IntegrationEnvironment] = mapped_column(
        Enum(IntegrationEnvironment, native_enum=False), nullable=False, default=IntegrationEnvironment.TEST
    )
    credential_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    entitlement_evidence_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quota_profile_id: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
