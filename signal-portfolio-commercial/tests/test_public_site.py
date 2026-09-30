"""PU-01 "Public home" / PU-08 "Help and compatibility guide" / PU-03
"Portfolio detail" -- get_service_status/get_channel_compatibility/
get_published_portfolio_detail's own tests."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import config
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.product import Product, ProductLifecycleState
from app.models.sleeve import Sleeve
from app.models.billing import ProductTier, TEST_MODE_MONTHLY_PRICE_CENTS
from app.services.public_site import (
    MAX_COMPARISON_SLUGS,
    TooManyComparisonSlugsError,
    compare_published_portfolios,
    get_channel_compatibility,
    get_pricing_plans,
    get_published_portfolio_detail,
    get_service_status,
)


def test_environment_status_reflects_the_real_configured_environment(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "LOCAL_SIM")
    statuses = {item.name: item.status for item in get_service_status()}
    assert statuses["Environment"] == "LOCAL_SIM"


def test_billing_is_reported_not_configured_with_the_placeholder_secret(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_LOCAL_SIM_not_a_real_stripe_secret")
    statuses = {item.name: item.status for item in get_service_status()}
    assert statuses["Billing (Stripe)"] == "NOT_CONFIGURED"


def test_billing_is_reported_configured_once_a_real_secret_is_set(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_a_real_looking_secret")
    statuses = {item.name: item.status for item in get_service_status()}
    assert statuses["Billing (Stripe)"] == "CONFIGURED"


def test_channel_compatibility_names_every_real_adapter_and_its_real_limitation():
    channels = {item.channel: item.limitation for item in get_channel_compatibility()}
    assert "Collective2" in channels
    assert "no Collective2 sandbox or real credentials" in channels["Collective2"]
    assert "eToro" in channels
    assert "demo account mode" in channels["eToro"] or "demo" in channels["eToro"]
    assert "MetaApi CopyFactory (close-only)" in channels


def test_a_draft_products_slug_returns_scoped_not_found(db_session):
    db_session.add(Product(tenant_id="tenant-a", product_name="Still Draft", slug="still-draft"))
    db_session.commit()
    assert get_published_portfolio_detail(db_session, "still-draft") is None


def test_an_unknown_slug_returns_scoped_not_found(db_session):
    assert get_published_portfolio_detail(db_session, "never-existed") is None


def test_a_published_product_with_a_version_returns_its_real_facts(db_session):
    now = datetime.now(timezone.utc)
    sleeve = Sleeve(
        tenant_id="tenant-a",
        provider="acme-research",
        analyst="jane",
        strategy_horizon="swing",
        asset_class="equity",
        parser_version="v1",
        execution_policy_id="ep-1",
        cost_model_id="cm-1",
        capacity_policy_id="cap-1",
        risk_unit_id="ru-1",
        history_origin="acme-research",
    )
    db_session.add(sleeve)
    db_session.flush()
    pv = PortfolioVersion(
        tenant_id="tenant-a",
        portfolio_id="p-detail",
        version_number=3,
        cash_weight=Decimal("0"),
        research_cutoff=now,
        max_subscriber_capacity=250,
        consent_disclosure_version="v1",
    )
    db_session.add(pv)
    db_session.flush()
    db_session.add(
        PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("1.0"),
            tenant_id="tenant-a",
        )
    )
    db_session.add(
        Product(
            tenant_id="tenant-a",
            product_name="Detail Product",
            slug="detail-product",
            portfolio_version_id=pv.portfolio_version_id,
            cash_bps=500,
            service_modes=["alerts"],
            methodology_document_id="method-1",
            research_report_id="report-1",
            lifecycle_state=ProductLifecycleState.PUBLISHED,
        )
    )
    db_session.commit()

    detail = get_published_portfolio_detail(db_session, "detail-product")
    assert detail is not None
    assert detail.product_name == "Detail Product"
    assert detail.portfolio_version_number == 3
    assert detail.sleeve_count == 1
    assert detail.max_subscriber_capacity == 250
    assert detail.methodology_document_id == "method-1"


def test_pricing_plans_are_absent_while_billing_is_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_LOCAL_SIM_not_a_real_stripe_secret")
    assert get_pricing_plans() == []


def test_pricing_plans_appear_once_billing_is_genuinely_configured(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_a_real_looking_secret")
    plans = {plan.tier: plan.monthly_price_cents for plan in get_pricing_plans()}
    assert plans[ProductTier.ALERTS_ONE.value] == TEST_MODE_MONTHLY_PRICE_CENTS[ProductTier.ALERTS_ONE]


def _published_product(db_session, *, slug, tenant_id="tenant-a"):
    product = Product(
        tenant_id=tenant_id, product_name=f"Product {slug}", slug=slug, lifecycle_state=ProductLifecycleState.PUBLISHED
    )
    db_session.add(product)
    db_session.commit()
    return product


def test_compare_published_portfolios_is_empty_for_no_slugs(db_session):
    result = compare_published_portfolios(db_session, [])
    assert result.matched == []
    assert result.unmatched_slugs == []


def test_compare_published_portfolios_matches_real_published_slugs(db_session):
    _published_product(db_session, slug="compare-a")
    _published_product(db_session, slug="compare-b")

    result = compare_published_portfolios(db_session, ["compare-a", "compare-b"])
    assert {d.slug for d in result.matched} == {"compare-a", "compare-b"}
    assert result.unmatched_slugs == []


def test_compare_published_portfolios_reports_unknown_slugs_by_name(db_session):
    _published_product(db_session, slug="compare-c")

    result = compare_published_portfolios(db_session, ["compare-c", "never-existed"])
    assert {d.slug for d in result.matched} == {"compare-c"}
    assert result.unmatched_slugs == ["never-existed"]


def test_compare_published_portfolios_excludes_a_draft_slug(db_session):
    db_session.add(Product(tenant_id="tenant-a", product_name="Draft", slug="compare-draft"))
    db_session.commit()

    result = compare_published_portfolios(db_session, ["compare-draft"])
    assert result.matched == []
    assert result.unmatched_slugs == ["compare-draft"]


def test_compare_published_portfolios_rejects_more_than_the_max(db_session):
    slugs = [f"compare-max-{i}" for i in range(MAX_COMPARISON_SLUGS + 1)]
    for slug in slugs:
        _published_product(db_session, slug=slug)

    with pytest.raises(TooManyComparisonSlugsError):
        compare_published_portfolios(db_session, slugs)


def test_compare_published_portfolios_dedupes_a_repeated_slug(db_session):
    _published_product(db_session, slug="compare-dedupe")
    result = compare_published_portfolios(db_session, ["compare-dedupe", "compare-dedupe"])
    assert len(result.matched) == 1
