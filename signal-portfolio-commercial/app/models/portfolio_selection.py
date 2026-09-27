"""PortfolioSelection: CU-02 "My portfolios" -- a customer's own record
of choosing to copy a published Product, per
dashboard_spec/screens/CU-02.md ("Manage selections and versions
without creating unintended orders"). See app/services/
portfolio_selection.py for the full screen contract this implements a
bounded slice of.

A selection is deliberately NOT an order, a mandate, or a live copying
instruction -- it records only "this customer intends to copy this
product," nothing that touches broker connectivity, quantity, or
execution. "Manage... without creating unintended orders" holds by
construction: there is no field here that could route to a broker.

The compound FK to `memberships` matches `CustomerProfile`/
`EligibilityAssessment`'s own precedent: this row can only reference a
real existing membership, never an arbitrary user_id under someone
else's tenant. Unlike those two (one row per tenant/user), a customer
can select more than one product, so `selection_id` is its own primary
key. A partial unique index on (tenant_id, user_id, product_id) WHERE
state = 'active' allows at most one ACTIVE selection per customer per
product at a time -- not duplicated by resubmitting the same selection
form -- while still letting a customer cancel and later re-select the
same product (a new row, since the old CANCELLED one is never edited
or deleted).

Tenant-scoped and RLS-protected like the other tenant tables.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, ForeignKeyConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioSelectionState(str, enum.Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class PortfolioSelection(Base):
    __tablename__ = "portfolio_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_portfolio_selection_membership",
        ),
        Index(
            "uq_portfolio_selection_active_customer_product",
            "tenant_id",
            "user_id",
            "product_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )

    selection_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    product_id: Mapped[str] = mapped_column(String, ForeignKey("products.product_id"), nullable=False)

    state: Mapped[PortfolioSelectionState] = mapped_column(
        Enum(PortfolioSelectionState, native_enum=False), nullable=False, default=PortfolioSelectionState.ACTIVE
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
