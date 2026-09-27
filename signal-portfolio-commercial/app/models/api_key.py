"""ApiKey: CU-16 "API delivery, keys and exports" -- a customer's own,
tenant-scoped, scoped read/delivery API credential, per
dashboard_spec/screens/CU-16.md's F-API-ACCESS form.

Only `key_hash` (a SHA-256 digest) is ever persisted -- the raw secret
exists only in the single response returned at creation time
(app/services/api_key.py's `generate_scoped_key`), never stored,
logged, or retrievable again afterward. "Generated key shown once only"
(CU-16's own acceptance text) is a property of what this table CANNOT
do (reverse a hash), not just an application convention.

`scopes` is restricted at the service layer to a fixed, safe allowlist
(alerts_read, reports_read, delivery_receive) -- "No trading/admin
scope" (CU-16's own field help text) means this column can never hold
a scope that grants any financial or administrative authority, by
construction.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_api_key_membership",
        ),
    )

    key_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    label: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
