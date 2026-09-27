"""EligibilityAssessment: ID-04 "Service eligibility onboarding" -- a
customer's own residence/service facts and consent versions, per
dashboard_spec/screens/ID-04.md's F-ELIGIBILITY form. "Customer
supplies facts, not approval flags" (ID-04's own acceptance text) -- no
column here stores an eligibility VERDICT; the decision is always
computed live (app/services/eligibility.py's `evaluate_eligibility`)
from these facts plus real published-product state, so it can never go
stale relative to what is actually published.

One row per (tenant_id, user_id) -- a mutable draft like `Product`, not
append-only: "Residence change invalidates affected service eligibility
and triggers review" is naturally true here because the decision is
recomputed from the CURRENT row, never a separately stored, possibly
stale verdict.

The compound FK to `memberships` matches `CustomerProfile`'s own
precedent (app/models/tenancy.py): this row can only reference a real
existing membership, never an arbitrary user_id under someone else's
tenant.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, Boolean, DateTime, Enum, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CustomerType(str, enum.Enum):
    INDIVIDUAL = "individual"
    ENTITY = "entity"


class EligibilityAssessment(Base):
    __tablename__ = "eligibility_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_eligibility_assessment_membership",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)

    residence_country: Mapped[str] = mapped_column(String, nullable=False)
    tax_residence: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    customer_type: Mapped[CustomerType] = mapped_column(
        Enum(CustomerType, native_enum=False), nullable=False, default=CustomerType.INDIVIDUAL
    )
    requested_service_modes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    document_versions: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    facts_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
