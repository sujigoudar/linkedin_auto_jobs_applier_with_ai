"""NotificationPreferences: CU-12 "Alert delivery preferences" -- a
customer's own delivery destinations, categories, timezone and quiet
hours, per dashboard_spec/screens/CU-12.md's F-DELIVERY form. See
app/services/notification_preferences.py for the full screen contract
this implements a bounded slice of.

One row per (tenant_id, user_id) -- a mutable draft like
`EligibilityAssessment`, not append-only: saving preferences replaces
the current row rather than accumulating a history no panel here reads.

`webhook_endpoint_id` is deliberately an opaque id reference, never a
raw URL -- "SSRF-safe registry; Raw URL uses separate secure
validation" (CU-12's own field help text) holds by construction: there
is no raw-URL field here for an SSRF check to secure.

The compound FK to `memberships` matches `EligibilityAssessment`/
`CustomerProfile`'s own precedent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationPreferences(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_notification_preferences_membership",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)

    email: Mapped[str | None] = mapped_column(String, nullable=True)
    webhook_endpoint_id: Mapped[str | None] = mapped_column(String, nullable=True)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    timezone_name: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    quiet_start: Mapped[str | None] = mapped_column(String, nullable=True)
    quiet_end: Mapped[str | None] = mapped_column(String, nullable=True)
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
