"""app/services/portfolio_rights.py -- CP-002 "Every component grant
must qualify". Requires a real Postgres session (RightsGrant's
ARRAY(String) columns and the append-only PortfolioVersion tables)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.rights import RightsGrant, RightsStatus, RightsUse
from app.models.sleeve import Sleeve
from app.services.portfolio_rights import check_portfolio_rights


def _sleeve(session, sleeve_id, provider):
    sleeve = Sleeve(
        sleeve_id=sleeve_id,
        tenant_id="tenant-a",
        provider=provider,
        analyst="jane",
        strategy_horizon="intraday-momentum",
        asset_class="EQUITY",
        parser_version="v1",
        execution_policy_id="policy-1",
        cost_model_id="cost-1",
        capacity_policy_id="capacity-1",
        risk_unit_id="risk-1",
        history_origin="broker-confirmed",
    )
    session.add(sleeve)
    return sleeve


def _grant(session, *, source_id, status=RightsStatus.GRANTED):
    now = datetime.now(timezone.utc)
    grant = RightsGrant(
        grant_id=f"grant-{source_id}",
        source_id=source_id,
        grantee_entity="Owner LLC",
        contract_hash="deadbeef",
        status=status,
        uses=[RightsUse.COMMERCIAL_ALERTS.value],
        channels=["web"],
        jurisdictions=["US"],
        assets=["EQUITY"],
        effective_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=365),
        attribution_policy_id="attrib-1",
        wind_down_policy_id="wind-1",
        review_id="review-1",
    )
    session.add(grant)
    return grant


def _portfolio_version(session, portfolio_version_id, sleeve_ids):
    pv = PortfolioVersion(
        portfolio_version_id=portfolio_version_id,
        tenant_id="tenant-a",
        portfolio_id="P01",
        version_number=1,
        cash_weight=Decimal("0.15"),
        research_cutoff=datetime.now(timezone.utc),
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    session.add(pv)
    session.flush()
    weight = (Decimal(1) - pv.cash_weight) / Decimal(len(sleeve_ids)) if sleeve_ids else Decimal(0)
    for sleeve_id in sleeve_ids:
        session.add(PortfolioVersionSleeve(portfolio_version_id=portfolio_version_id, sleeve_id=sleeve_id, weight=weight))
    return pv


def _check(**overrides):
    defaults = dict(
        portfolio_version_id="pv-1",
        use=RightsUse.COMMERCIAL_ALERTS,
        channel="web",
        jurisdiction="US",
        asset="EQUITY",
    )
    defaults.update(overrides)
    return defaults


def test_a_portfolio_with_no_sleeves_is_denied(db_session):
    _portfolio_version(db_session, "pv-1", [])
    db_session.commit()

    result = check_portfolio_rights(db_session, **_check())
    assert result.allowed is False
    assert result.reason == "PORTFOLIO_HAS_NO_SLEEVES"


def test_a_portfolio_where_every_sleeve_qualifies_is_allowed(db_session):
    _sleeve(db_session, "sleeve-a", "acme")
    _sleeve(db_session, "sleeve-b", "acme")
    _grant(db_session, source_id="acme")
    _portfolio_version(db_session, "pv-1", ["sleeve-a", "sleeve-b"])
    db_session.commit()

    result = check_portfolio_rights(db_session, **_check())
    assert result.allowed is True


def test_one_ungranted_sleeve_blocks_the_whole_portfolio(db_session):
    """CP-002's own boundary contract: one unqualified component denies
    the whole version -- it is never silently dropped to publish the
    rest."""
    _sleeve(db_session, "sleeve-a", "acme")  # has a grant
    _sleeve(db_session, "sleeve-b", "unknown-provider")  # no grant at all
    _grant(db_session, source_id="acme")
    _portfolio_version(db_session, "pv-1", ["sleeve-a", "sleeve-b"])
    db_session.commit()

    result = check_portfolio_rights(db_session, **_check())
    assert result.allowed is False
    assert result.failing_sleeve_id == "sleeve-b"


def test_a_revoked_grant_on_one_sleeve_blocks_the_whole_portfolio(db_session):
    _sleeve(db_session, "sleeve-a", "acme")
    _sleeve(db_session, "sleeve-b", "bad-actor")
    _grant(db_session, source_id="acme")
    _grant(db_session, source_id="bad-actor", status=RightsStatus.REVOKED)
    _portfolio_version(db_session, "pv-1", ["sleeve-a", "sleeve-b"])
    db_session.commit()

    result = check_portfolio_rights(db_session, **_check())
    assert result.allowed is False
    assert result.failing_sleeve_id == "sleeve-b"


def test_referencing_a_nonexistent_sleeve_is_denied(db_session):
    pv = PortfolioVersion(
        portfolio_version_id="pv-2",
        tenant_id="tenant-a",
        portfolio_id="P02",
        version_number=1,
        cash_weight=Decimal("0.15"),
        research_cutoff=datetime.now(timezone.utc),
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    db_session.add(pv)
    db_session.flush()
    # No Sleeve row exists for "does-not-exist" -- FK would normally
    # reject this, but we test the service's own defensive lookup here
    # by using a sleeve_id that WILL exist at membership-insert time but
    # simulating a lookup miss is redundant with the FK; instead assert
    # the FK itself protects this invariant.
    from sqlalchemy.exc import IntegrityError

    db_session.add(PortfolioVersionSleeve(portfolio_version_id="pv-2", sleeve_id="does-not-exist", weight=Decimal("1")))
    try:
        db_session.flush()
        raise AssertionError("expected the FK to reject a membership row for a nonexistent sleeve")
    except IntegrityError:
        db_session.rollback()
