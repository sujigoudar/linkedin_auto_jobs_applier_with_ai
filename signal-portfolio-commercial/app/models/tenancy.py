"""Tenant model core, per spec/docs/02_architecture_and_tenancy.md's
"Tenant model" section: `tenant`, `user_identity`, `membership`,
`customer_profile` -- the objects everything else in later phases
(products, portfolio versions, subscriptions, deliveries...) will hang
off of.

Isolation boundary: one `tenant` row per customer account. A
`user_identity` (a login) can hold a `membership` in more than one
tenant (e.g. the platform owner also has an operator membership; a
support agent has memberships in many tenants), each with its own role.
`customer_profile` is the tenant-scoped record for the paying customer
that tenant represents; it is deliberately compound-FK'd to
`membership` (not just to `tenant`) so a `customer_profile` can never
reference a `(tenant_id, user_id)` pair that isn't an actual membership
-- "all child rows have compound foreign keys ... preventing
cross-tenant parent references" (docs/02).

Operator roles (`owner`, `researcher`, `reviewer`, `publisher_operator`,
`billing_operator`, `support_readonly`) and the `customer` role are all
just `Membership.role` values -- role-specific authority is enforced in
`app/services/permissions.py`, never inferred from which table a row
lives in.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MembershipRole(str, enum.Enum):
    OWNER = "owner"
    RESEARCHER = "researcher"
    REVIEWER = "reviewer"
    PUBLISHER_OPERATOR = "publisher_operator"
    BILLING_OPERATOR = "billing_operator"
    SUPPORT_READONLY = "support_readonly"
    CUSTOMER = "customer"


class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    environment: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class UserIdentity(Base):
    __tablename__ = "user_identities"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Membership(Base):
    __tablename__ = "memberships"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_identities.user_id"), primary_key=True)
    role: Mapped[MembershipRole] = mapped_column(Enum(MembershipRole, native_enum=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class CustomerProfile(Base):
    """One per tenant that is a paying customer (not every tenant has one --
    a platform-operator-only tenant never gets a `CustomerProfile` row)."""

    __tablename__ = "customer_profiles"
    __table_args__ = (
        # The compound FK is the actual cross-tenant guard: this row can
        # only reference a (tenant_id, user_id) pair that is a real,
        # existing membership -- not merely an existing tenant_id and an
        # unrelated user_id from a different tenant.
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_customer_profile_membership",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    residence_jurisdiction: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
