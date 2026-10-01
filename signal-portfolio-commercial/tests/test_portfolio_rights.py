"""app/services/portfolio_rights.py -- CP-002 "Every component grant
must qualify". Requires a real Postgres session (RightsGrant's
ARRAY(String) columns and the append-only PortfolioVersion tables)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db import set_tenant_scope
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
        session.add(
            PortfolioVersionSleeve(
                portfolio_version_id=portfolio_version_id, sleeve_id=sleeve_id, weight=weight, tenant_id="tenant-a"
            )
        )
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
    # `PortfolioRightsResult.failing_sleeve_id`'s own default must stay
    # None (not "", or any other falsy-but-wrong sentinel) -- this is
    # the one construction site (`_EMPTY_PORTFOLIO`) that relies on the
    # dataclass field default rather than passing the argument
    # explicitly.
    assert result.failing_sleeve_id is None


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

    db_session.add(
        PortfolioVersionSleeve(
            portfolio_version_id="pv-2", sleeve_id="does-not-exist", weight=Decimal("1"), tenant_id="tenant-a"
        )
    )
    try:
        db_session.flush()
        raise AssertionError("expected the FK to reject a membership row for a nonexistent sleeve")
    except IntegrityError:
        db_session.rollback()


def test_a_cross_tenant_sleeve_reference_hidden_by_rls_is_treated_as_unknown_not_allowed(
    db_session, tenant_session_factory
):
    """The FK on `PortfolioVersionSleeve.sleeve_id` (see that model's own
    docstring: "a database-level RLS backstop, not this table's own
    scoping mechanism") only guarantees the referenced sleeve_id exists
    SOMEWHERE in `sleeves` -- it says nothing about which tenant owns it.
    A membership row under tenant-a can legitimately reference a real
    sleeve_id that belongs to tenant-b; under RLS that sleeve is
    genuinely invisible to tenant-a's own session, so
    `session.get(Sleeve, ...)` returns None exactly like a nonexistent
    sleeve would, and `check_portfolio_rights` must fail closed
    (UNKNOWN_SLEEVE, denied) rather than silently treating "I can't see
    it" as "it must qualify." The previous test only proved the FK
    blocks referencing a sleeve_id that exists nowhere at all -- it never
    exercised this RLS-scoped, non-superuser path, so the `sleeve is
    None` branch's own `allowed=False` was never actually checked by any
    assertion calling `check_portfolio_rights` itself."""
    db_session.add(
        Sleeve(
            sleeve_id="sleeve-b",
            tenant_id="tenant-b",
            provider="acme",
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
    )
    _grant(db_session, source_id="acme")
    pv = PortfolioVersion(
        portfolio_version_id="pv-3",
        tenant_id="tenant-a",
        portfolio_id="P03",
        version_number=1,
        cash_weight=Decimal("0.15"),
        research_cutoff=datetime.now(timezone.utc),
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    db_session.add(pv)
    db_session.flush()
    # sleeve-b is owned by tenant-b (it was added to the Sleeve model
    # above under that tenant_id); this membership row deliberately
    # records a DIFFERENT tenant_id ("tenant-a") -- the FK on sleeve_id
    # alone permits this, since it only checks the row exists, not that
    # tenant_id matches.
    db_session.add(
        PortfolioVersionSleeve(
            portfolio_version_id="pv-3", sleeve_id="sleeve-b", weight=Decimal("1"), tenant_id="tenant-a"
        )
    )
    db_session.commit()

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        result = check_portfolio_rights(session, **_check(portfolio_version_id="pv-3"))
        assert result.allowed is False
        assert result.reason == "UNKNOWN_SLEEVE"
        assert result.failing_sleeve_id == "sleeve-b"
    finally:
        session.rollback()
        session.close()
