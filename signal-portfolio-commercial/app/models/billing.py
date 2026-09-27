"""Subscription/entitlement state, per spec/docs/08_products_billing_and_entitlements.md.

Tenant-scoped and RLS-protected like the other customer-facing tables
(app/db.py's `_TENANT_SCOPED_TABLES`). `ProductTier` and its four
proposed test-mode prices are exactly the spec's own "founder-review
pricing hypotheses and test fixtures, not user-approved prices" --
`Subscription.price_cents`/`currency` are still stored per-row (never a
hardcoded browser amount), so a real, owner-approved price can replace
these fixtures without a schema change.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProductTier(str, enum.Enum):
    FREE_RESEARCH = "FREE_RESEARCH"
    ALERTS_ONE = "ALERTS_ONE"
    PORTFOLIOS_THREE = "PORTFOLIOS_THREE"
    PRO_RESEARCH_API = "PRO_RESEARCH_API"


#: docs/08's own proposed TEST-MODE fixture prices (USD cents/month) --
#: "founder-review pricing hypotheses ... Live amounts require CARD-4
#: approval." Never used to charge anything real; there is no Stripe
#: transport in this build (see app/services/stripe_webhook.py's own
#: docstring).
TEST_MODE_MONTHLY_PRICE_CENTS: dict[ProductTier, int] = {
    ProductTier.FREE_RESEARCH: 0,
    ProductTier.ALERTS_ONE: 3900,
    ProductTier.PORTFOLIOS_THREE: 9900,
    ProductTier.PRO_RESEARCH_API: 19900,
}


class SubscriptionState(str, enum.Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    TRIAL_AUTHORIZED = "TRIAL_AUTHORIZED"
    ACTIVE_PAID = "ACTIVE_PAID"
    CANCEL_AT_PERIOD_END = "CANCEL_AT_PERIOD_END"
    PAST_DUE = "PAST_DUE"
    SUSPENDED_NEW_ENTRIES = "SUSPENDED_NEW_ENTRIES"
    ENDED = "ENDED"
    DISPUTED = "DISPUTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tier: Mapped[ProductTier] = mapped_column(Enum(ProductTier, native_enum=False), nullable=False)
    state: Mapped[SubscriptionState] = mapped_column(
        Enum(SubscriptionState, native_enum=False), nullable=False, default=SubscriptionState.PENDING_PAYMENT
    )
    #: The exact display price actually charged/proposed for this row --
    #: never recomputed from TEST_MODE_MONTHLY_PRICE_CENTS at read time,
    #: so a later price change never silently rewrites history.
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="usd")
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The Stripe (or future processor) subscription/customer id this row
    #: mirrors -- opaque here, never parsed for business meaning.
    processor_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
