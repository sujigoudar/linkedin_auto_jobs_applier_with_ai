"""WorkspaceSettings: AD-20 "Workspace customization and configuration"
-- a tenant's own shared display/notification-routing draft. See
dashboard_spec/screens/AD-20.md's F-WORKSPACE form for the full
contract this implements a bounded slice of.

One row per tenant (like CustomerProfile's own precedent) -- this is
the shared "admin draft" workspace default, not a per-user personal
view (AD-20's own "Save personal view" is a separate, not-yet-built
scope).

"Cosmetic configuration never changes policy, financial permissions or
required disclosure visibility" (AD-20's own acceptance text) is true
by construction: there is no field here that touches a permission,
policy, or financial value at all -- only display/notification-routing
references.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkspaceTheme(str, enum.Enum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class WorkspaceDensity(str, enum.Enum):
    COMFORTABLE = "comfortable"
    COMPACT = "compact"


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)

    workspace_name: Mapped[str] = mapped_column(String, nullable=False)
    theme: Mapped[WorkspaceTheme] = mapped_column(Enum(WorkspaceTheme, native_enum=False), nullable=False, default=WorkspaceTheme.SYSTEM)
    density: Mapped[WorkspaceDensity] = mapped_column(
        Enum(WorkspaceDensity, native_enum=False), nullable=False, default=WorkspaceDensity.COMFORTABLE
    )
    visible_panel_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    column_order: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    notification_route_id: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
