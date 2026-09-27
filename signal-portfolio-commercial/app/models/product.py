"""Product: a draftable, mutable, revision-tracked commercial offering,
per dashboard_spec/screens/AD-07.md ("Products and portfolio versions")
and dashboard_spec/docs/12_BUILDABLE_DATA_AND_REPOSITORIES.md's "First
actual vertical slice": "A user must be able to create an unreleased
product, refresh and read the persisted draft, and see exactly why it
cannot be published."

Deliberately mutable, unlike `PortfolioVersion` (Phase 04's immutable,
append-only pinned allocation): a Product draft is exactly the kind of
in-progress record that legitimately gets edited before release --
"Version/revision fields are required for updates" (docs/12). Only once
a Product is PUBLISHED does its *content* (which portfolio_version_id,
weights, etc.) stop mattering for new customers; this table itself is
never append-only.

Tenant-scoped and RLS-protected like the other tenant tables. A slug is
globally unique (used for the public catalog URL) but only meaningful
once PUBLISHED -- "Draft slug not public" (AD-07's own field help text).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProductLifecycleState(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"


class ServiceMode(str, enum.Enum):
    ALERTS = "alerts"
    COPYING = "copying"
    MANAGED_PROGRAM = "managed_program"


class Product(Base):
    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    product_name: Mapped[str] = mapped_column(String, nullable=False)
    #: Unique globally (it becomes part of a public URL once published),
    #: not just per-tenant -- "unique lower-case URL slug" (AD-07).
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    #: The currently-selected PortfolioVersion for this product, if any
    #: -- optional on a fresh draft ("editable draft only" -- AD-07).
    portfolio_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolio_versions.portfolio_version_id"), nullable=True
    )
    cash_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=10_000)
    service_modes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    audience_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    research_report_id: Mapped[str | None] = mapped_column(String, nullable=True)
    methodology_document_id: Mapped[str | None] = mapped_column(String, nullable=True)

    lifecycle_state: Mapped[ProductLifecycleState] = mapped_column(
        Enum(ProductLifecycleState, native_enum=False), nullable=False, default=ProductLifecycleState.DRAFT
    )
    #: Bumped on every update -- "Version/revision fields are required
    #: for updates" (docs/12). Lets a client detect it edited a stale
    #: copy (CP-level "conflict" state) instead of silently overwriting.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
