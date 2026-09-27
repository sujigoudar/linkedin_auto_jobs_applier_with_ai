"""AD-13 "Pricing, entitlements and billing operations" -- app/services/price_version.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.services.price_version import (
    InvalidPriceVersionError,
    SkuAlreadyExistsError,
    create_price_version,
    list_price_versions,
)


def test_list_price_versions_is_empty_before_any_are_saved(db_session):
    assert list_price_versions(db_session, tenant_id="tenant-a") == []


def test_create_accepts_a_real_reviewed_feature_and_test_mode(db_session):
    create_price_version(
        db_session,
        tenant_id="tenant-a",
        sku="alerts-monthly",
        currency="usd",
        amount_minor=3900,
        interval="month",
        is_unlimited_portfolios=False,
        portfolio_limit=1,
        features=["alerts_read"],
        mode="test",
    )
    db_session.commit()

    versions = list_price_versions(db_session, tenant_id="tenant-a")
    assert len(versions) == 1
    assert versions[0].sku == "alerts-monthly"


def test_create_rejects_a_trading_feature(db_session):
    with pytest.raises(InvalidPriceVersionError, match="no implicit trading right"):
        create_price_version(
            db_session,
            tenant_id="tenant-a",
            sku="bad-sku",
            currency="usd",
            amount_minor=3900,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=1,
            features=["trading_write"],
            mode="test",
        )


def test_create_rejects_live_mode(db_session):
    with pytest.raises(InvalidPriceVersionError, match="LIVE_MODE_NOT_AUTHORIZED"):
        create_price_version(
            db_session,
            tenant_id="tenant-a",
            sku="live-sku",
            currency="usd",
            amount_minor=3900,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=1,
            features=["alerts_read"],
            mode="live",
        )


def test_create_rejects_a_negative_amount(db_session):
    with pytest.raises(InvalidPriceVersionError):
        create_price_version(
            db_session,
            tenant_id="tenant-a",
            sku="negative-sku",
            currency="usd",
            amount_minor=-1,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=1,
            features=["alerts_read"],
            mode="test",
        )


def test_create_requires_a_nonnegative_portfolio_limit_unless_unlimited(db_session):
    with pytest.raises(InvalidPriceVersionError):
        create_price_version(
            db_session,
            tenant_id="tenant-a",
            sku="no-limit-sku",
            currency="usd",
            amount_minor=3900,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )


def test_create_accepts_explicit_unlimited_without_a_portfolio_limit(db_session):
    version = create_price_version(
        db_session,
        tenant_id="tenant-a",
        sku="unlimited-sku",
        currency="usd",
        amount_minor=19900,
        interval="month",
        is_unlimited_portfolios=True,
        portfolio_limit=None,
        features=["alerts_read", "research_api"],
        mode="test",
    )
    assert version.portfolio_limit is None
    assert version.is_unlimited_portfolios is True


def test_create_rejects_a_duplicate_sku(db_session):
    create_price_version(
        db_session,
        tenant_id="tenant-a",
        sku="dup-sku",
        currency="usd",
        amount_minor=3900,
        interval="month",
        is_unlimited_portfolios=False,
        portfolio_limit=1,
        features=["alerts_read"],
        mode="test",
    )
    db_session.commit()

    with pytest.raises(SkuAlreadyExistsError):
        create_price_version(
            db_session,
            tenant_id="tenant-a",
            sku="dup-sku",
            currency="usd",
            amount_minor=9900,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=3,
            features=["reports_read"],
            mode="test",
        )
