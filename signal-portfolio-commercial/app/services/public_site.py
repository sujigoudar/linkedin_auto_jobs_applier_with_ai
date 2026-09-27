"""PU-01 "Public home" -- the real service-status checklist and
published-product summary backing the anonymous landing page. See
dashboard_spec/screens/PU-01.md for the full screen contract this
implements a bounded slice of.

"Service status" (AD-01-style checklist, per PU-01-P05's own contract:
"enumerates independently evaluated conditions, actual outcome, reason
code") is computed from real configuration state, never a decorative
"all systems operational" banner: the environment tag and whether
billing is genuinely connected are both read from app.config, exactly
as configured -- a placeholder Stripe secret is honestly reported as
NOT_CONFIGURED, never silently upgraded to look connected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.models.billing import TEST_MODE_MONTHLY_PRICE_CENTS
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.product import Product, ProductLifecycleState

_PLACEHOLDER_STRIPE_SECRET = "whsec_LOCAL_SIM_not_a_real_stripe_secret"


@dataclass(frozen=True)
class ServiceStatusItem:
    name: str
    status: str
    reason: str


@dataclass(frozen=True)
class ChannelCompatibilityItem:
    channel: str
    supported_service: str
    limitation: str


@dataclass(frozen=True)
class PortfolioDetail:
    product_name: str
    slug: str
    service_modes: list[str]
    cash_bps: int
    as_of: datetime
    portfolio_version_number: int | None
    sleeve_count: int | None
    max_subscriber_capacity: int | None
    research_cutoff: datetime | None
    methodology_document_id: str | None
    research_report_id: str | None


def get_published_portfolio_detail(session: Session, slug: str) -> PortfolioDetail | None:
    """PU-03 "Portfolio detail" -- returns None both when the slug never
    existed AND when it belongs to a DRAFT/VALIDATED/APPROVED (not yet
    PUBLISHED) product, so callers turn a None here into a 404, never a
    200 that leaks an unreleased product's content by URL guess (PU-03's
    own "draft/retired restricted slugs return scoped not-found").

    No NAV/marks-history model exists in this build for the standard
    equal-weight portfolio products (PAMM/MAM programs are a separate,
    unrelated model) -- the performance/drawdown panel and its metrics
    are therefore always reported as genuinely unavailable here, never a
    guessed or zero-filled curve, matching PU-03's own acceptance
    criterion."""
    product = session.scalars(
        select(Product).where(Product.slug == slug, Product.lifecycle_state == ProductLifecycleState.PUBLISHED)
    ).first()
    if product is None:
        return None

    version_number: int | None = None
    sleeve_count: int | None = None
    max_subscriber_capacity: int | None = None
    research_cutoff: datetime | None = None
    if product.portfolio_version_id is not None:
        version = session.get(PortfolioVersion, product.portfolio_version_id)
        if version is not None:
            version_number = version.version_number
            max_subscriber_capacity = version.max_subscriber_capacity
            research_cutoff = version.research_cutoff
            sleeve_count = session.scalar(
                select(func.count())
                .select_from(PortfolioVersionSleeve)
                .where(PortfolioVersionSleeve.portfolio_version_id == version.portfolio_version_id)
            )

    return PortfolioDetail(
        product_name=product.product_name,
        slug=product.slug,
        service_modes=list(product.service_modes),
        cash_bps=product.cash_bps,
        as_of=product.updated_at,
        portfolio_version_number=version_number,
        sleeve_count=sleeve_count,
        max_subscriber_capacity=max_subscriber_capacity,
        research_cutoff=research_cutoff,
        methodology_document_id=product.methodology_document_id,
        research_report_id=product.research_report_id,
    )


@dataclass(frozen=True)
class PlanCard:
    tier: str
    monthly_price_cents: int
    currency: str


def get_pricing_plans() -> list[PlanCard]:
    """PU-05 "Pricing and service compatibility" -- Plan cards.

    app/models/billing.py's own docstring calls TEST_MODE_MONTHLY_PRICE_CENTS
    "founder-review pricing hypotheses and test fixtures, not
    user-approved prices". PU-05's own acceptance criterion is
    "Unapproved price drafts are absent from public responses" -- so
    this returns nothing at all until billing is genuinely connected
    (the same real config signal PU-01's Service status already
    reports), never the fixture prices leaking onto a public page just
    because rows exist in code."""
    if config.STRIPE_WEBHOOK_SECRET == _PLACEHOLDER_STRIPE_SECRET:
        return []
    return [
        PlanCard(tier=tier.value, monthly_price_cents=price_cents, currency="usd")
        for tier, price_cents in TEST_MODE_MONTHLY_PRICE_CENTS.items()
    ]


def get_channel_compatibility() -> list[ChannelCompatibilityItem]:
    """PU-08 "Help and compatibility guide" -- Compatibility directory.
    Grounded in the actual state of each adapter module
    (app/services/collective2_publisher.py, etoro_adapter.py,
    copyfactory_close_only.py), not a marketing claim: request-building
    is real and tested; live transmission is blocked in every case by
    the same fact -- no real platform credentials exist in this
    environment. "Only verified capabilities appear as available; no
    promises of universal broker coverage" (PU-08's own acceptance
    text)."""
    return [
        ChannelCompatibilityItem(
            channel="Collective2",
            supported_service="Request building/validation (API4 order envelope)",
            limitation="Not transmitted -- no Collective2 sandbox or real credentials exist in this environment.",
        ),
        ChannelCompatibilityItem(
            channel="eToro",
            supported_service="Request building (Builders API trade request shape), demo transport only",
            limitation="No application is registered with eToro; the code path refuses any non-demo account mode outright.",
        ),
        ChannelCompatibilityItem(
            channel="MetaApi CopyFactory (close-only)",
            supported_service="Close-only mode classification (by-position/by-symbol/immediately)",
            limitation="Classification only -- this module never calls CopyFactory itself.",
        ),
    ]


def get_service_status() -> list[ServiceStatusItem]:
    billing_configured = config.STRIPE_WEBHOOK_SECRET != _PLACEHOLDER_STRIPE_SECRET
    return [
        ServiceStatusItem(
            name="Environment",
            status=config.ENVIRONMENT,
            reason="Every trade and subscription on this deployment is paper-traded/simulated unless this reads COMMERCIAL_LIVE.",
        ),
        ServiceStatusItem(
            name="Billing (Stripe)",
            status="CONFIGURED" if billing_configured else "NOT_CONFIGURED",
            reason=(
                "A real Stripe webhook secret has been set."
                if billing_configured
                else "No real Stripe account has been connected yet -- billing runs against a local placeholder secret only."
            ),
        ),
    ]
