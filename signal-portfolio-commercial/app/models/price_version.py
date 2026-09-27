"""PriceVersion: AD-13 "Pricing, entitlements and billing operations" --
a tenant's own test-mode price/entitlement draft. See
dashboard_spec/screens/AD-13.md's F-PRICE form for the full contract
this implements a bounded slice of.

"Existing subscriptions bind original price/version" (AD-13's own
acceptance text) means a PriceVersion is never edited once saved -- a
changed price is a NEW version, matching Product/PortfolioVersion's own
established pattern in this build. Nothing in the billing pipeline
reads this table yet, so saving one has no live effect.

`sku` is globally unique and immutable -- "immutable unique product
plan key" (AD-13's own field help text), matching Product.slug's own
precedent.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BillingInterval(str, enum.Enum):
    MONTH = "month"
    YEAR = "year"


class PriceMode(str, enum.Enum):
    TEST = "test"
    LIVE = "live"


class PriceVersion(Base):
    __tablename__ = "price_versions"

    price_version_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    sku: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="usd")
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    interval: Mapped[BillingInterval] = mapped_column(Enum(BillingInterval, native_enum=False), nullable=False)
    #: True means explicit, reviewed unlimited policy; `portfolio_limit`
    #: is then ignored -- "No unexplained null unlimited" (AD-13's own
    #: field help text): unlimited is never inferred from an absent value.
    is_unlimited_portfolios: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    portfolio_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    mode: Mapped[PriceMode] = mapped_column(Enum(PriceMode, native_enum=False), nullable=False, default=PriceMode.TEST)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
