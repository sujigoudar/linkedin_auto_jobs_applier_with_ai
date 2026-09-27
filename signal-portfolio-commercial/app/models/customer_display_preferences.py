"""CustomerDisplayPreferences: CU-13 "Profile, security and display
preferences" -- a customer's own profile/display fields, per
dashboard_spec/screens/CU-13.md's F-PREFERENCES form ("Profile ->
Display -> Sessions -> Privacy" -- this model covers only the
Profile/Display steps). See app/services/customer_display_preferences.py
for the full screen contract this implements a bounded slice of.

One row per (tenant_id, user_id) -- a mutable draft like
`NotificationPreferences`/`EligibilityAssessment`'s own precedent.
"No arbitrary JSON settings blob" (CU-13's own acceptance text) holds
by construction: every field here is named and typed, never a
freeform blob column.

"Layout preferences cannot hide mandatory risk/origin/safety fields"
holds by construction too -- there is no panel-visibility field on this
model at all (unlike AD-20's own tenant-wide workspace settings, which
has one and therefore needs a runtime `MANDATORY_PANEL_IDS` guard).

The compound FK to `memberships` matches `EligibilityAssessment`/
`NotificationPreferences`'s own precedent.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DisplayTheme(str, enum.Enum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class DisplayDensity(str, enum.Enum):
    COMFORTABLE = "comfortable"
    COMPACT = "compact"


class ReduceMotion(str, enum.Enum):
    SYSTEM = "system"
    ON = "on"


class CustomerDisplayPreferences(Base):
    __tablename__ = "customer_display_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_customer_display_preferences_membership",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)

    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone_name: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    theme: Mapped[DisplayTheme] = mapped_column(Enum(DisplayTheme, native_enum=False), nullable=False, default=DisplayTheme.SYSTEM)
    density: Mapped[DisplayDensity] = mapped_column(
        Enum(DisplayDensity, native_enum=False), nullable=False, default=DisplayDensity.COMFORTABLE
    )
    number_locale: Mapped[str] = mapped_column(String, nullable=False, default="en-US")
    #: Display only -- "no implicit conversion without data; No account
    #: currency change" (CU-13's own field help text). Nothing in this
    #: build reads this field to perform a conversion.
    view_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    reduce_motion: Mapped[ReduceMotion] = mapped_column(
        Enum(ReduceMotion, native_enum=False), nullable=False, default=ReduceMotion.SYSTEM
    )

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
