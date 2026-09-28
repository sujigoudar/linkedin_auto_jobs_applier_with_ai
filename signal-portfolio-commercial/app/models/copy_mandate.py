"""CopyMandate: CU-09 "Copy setup and mandate wizard" -- a customer's
own explicit, versioned mandate draft, per
dashboard_spec/screens/CU-09.md's F-MANDATE form ("Create an explicit
versioned mandate; activation is separate from draft and payment").
See app/services/copy_mandate.py for the full screen contract this
implements a bounded slice of.

This is the real "no copy-mandate model exists at all" gap AD-11's own
slice already documented, closed for real -- but only as far as "Save
mandate draft without effect" (CU-09's own Save contract) goes.
`state` therefore has no ACTIVE value at all: activation needs a real
scoped publisher/execution pipeline this build does not have, so a
mandate can only ever be DRAFT or CANCELLED here, never activated.

`selection_id` and `connection_id` reference a customer's own
PortfolioSelection (CU-02) and PlatformConnection (CU-07/CU-08) --
enforced at the service layer, not by a DB-level FK, since ownership
also requires the row's OWN state to be eligible (an ACTIVE selection,
a DECLARED connection), which a bare foreign key can't express.

Tenant-scoped, and additionally compound-FK'd to `memberships` like
`ApiKey`/`PlatformConnection`'s own precedent -- a customer can draft
more than one mandate.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKeyConstraint, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CopyMandateStartMode(str, enum.Enum):
    NEW_ENTRIES_ONLY = "new_entries_only"
    SYNC_EXISTING = "sync_existing"


class CopyMandateState(str, enum.Enum):
    DRAFT = "draft"
    CANCELLED = "cancelled"


class CopyMandate(Base):
    __tablename__ = "copy_mandates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_copy_mandate_membership",
        ),
    )

    mandate_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    selection_id: Mapped[str] = mapped_column(String, nullable=False)
    connection_id: Mapped[str] = mapped_column(String, nullable=False)

    allocation_amount: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    allocation_currency: Mapped[str] = mapped_column(String, nullable=False)
    max_trade_risk: Mapped[Decimal | None] = mapped_column(Numeric(28, 10), nullable=True)
    max_loss: Mapped[Decimal | None] = mapped_column(Numeric(28, 10), nullable=True)
    start_mode: Mapped[CopyMandateStartMode] = mapped_column(
        Enum(CopyMandateStartMode, native_enum=False), nullable=False, default=CopyMandateStartMode.NEW_ENTRIES_ONLY
    )
    policy_version_id: Mapped[str] = mapped_column(String, nullable=False)
    consent_version: Mapped[str] = mapped_column(String, nullable=False)

    state: Mapped[CopyMandateState] = mapped_column(
        Enum(CopyMandateState, native_enum=False), nullable=False, default=CopyMandateState.DRAFT
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
