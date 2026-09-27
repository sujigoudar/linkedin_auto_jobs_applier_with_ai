"""app/services/rights_registry.py against a real, disposable Postgres
cluster (see tests/conftest.py) -- ARRAY(String) columns and enum
handling are Postgres-specific, so this is deliberately not tested
against SQLite or a mock session.
"""
from datetime import datetime, timedelta, timezone

from app.models.rights import RightsGrant, RightsStatus, RightsUse
from app.services.rights_registry import (
    NAMED_UNKNOWN_SOURCES,
    check_rights,
    seed_unknown_source,
)


def _granted(
    session,
    *,
    source_id="acme",
    uses=(RightsUse.COMMERCIAL_ALERTS.value,),
    channels=("web",),
    jurisdictions=("US",),
    assets=("EQUITY",),
    effective_at=None,
    expires_at=None,
):
    now = datetime.now(timezone.utc)
    grant = RightsGrant(
        grant_id=f"grant-{source_id}",
        source_id=source_id,
        grantee_entity="Owner LLC",
        contract_hash="deadbeef",
        status=RightsStatus.GRANTED,
        uses=list(uses),
        channels=list(channels),
        jurisdictions=list(jurisdictions),
        assets=list(assets),
        effective_at=effective_at or (now - timedelta(days=1)),
        expires_at=expires_at or (now + timedelta(days=365)),
        attribution_policy_id="attrib-1",
        wind_down_policy_id="wind-1",
        review_id="review-1",
    )
    session.add(grant)
    session.flush()
    return grant


def test_no_grant_at_all_is_denied(db_session):
    result = check_rights(
        db_session, source_id="nobody", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is False
    assert result.reason == "RIGHTS_USE_NOT_GRANTED"


def test_a_matching_granted_grant_is_allowed(db_session):
    grant = _granted(db_session)
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is True
    assert result.grant_id == grant.grant_id


def test_a_use_not_in_the_grant_is_denied(db_session):
    _granted(db_session, uses=(RightsUse.PRIVATE_RESEARCH.value,))
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is False


def test_a_channel_not_in_the_grant_is_denied(db_session):
    _granted(db_session, channels=("web",))
    result = check_rights(
        db_session,
        source_id="acme",
        use=RightsUse.COMMERCIAL_ALERTS,
        channel="collective2",
        jurisdiction="US",
        asset="EQUITY",
    )
    assert result.allowed is False


def test_a_jurisdiction_not_in_the_grant_is_denied(db_session):
    _granted(db_session, jurisdictions=("US",))
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="UK", asset="EQUITY"
    )
    assert result.allowed is False


def test_an_asset_not_in_the_grant_is_denied(db_session):
    _granted(db_session, assets=("EQUITY",))
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="CRYPTO"
    )
    assert result.allowed is False


def test_empty_channels_means_no_channel_is_permitted_not_all_channels(db_session):
    """Fail-closed: an empty list is NOT "unrestricted" -- it's "nothing
    reviewed/approved yet.\""""
    _granted(db_session, channels=())
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is False


def test_an_expired_grant_is_denied(db_session):
    now = datetime.now(timezone.utc)
    _granted(db_session, effective_at=now - timedelta(days=30), expires_at=now - timedelta(days=1))
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is False


def test_a_not_yet_effective_grant_is_denied(db_session):
    now = datetime.now(timezone.utc)
    _granted(db_session, effective_at=now + timedelta(days=1), expires_at=now + timedelta(days=365))
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is False


def test_denied_status_is_denied_even_with_matching_fields(db_session):
    now = datetime.now(timezone.utc)
    grant = RightsGrant(
        grant_id="denied-1",
        source_id="acme",
        grantee_entity="Owner LLC",
        contract_hash="x",
        status=RightsStatus.DENIED,
        uses=[RightsUse.COMMERCIAL_ALERTS.value],
        channels=["web"],
        jurisdictions=["US"],
        assets=["EQUITY"],
        effective_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=365),
        attribution_policy_id="a",
        wind_down_policy_id="w",
        review_id="r",
    )
    db_session.add(grant)
    db_session.flush()

    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is False


def test_revoked_status_is_denied(db_session):
    now = datetime.now(timezone.utc)
    grant = RightsGrant(
        grant_id="revoked-1",
        source_id="acme",
        grantee_entity="Owner LLC",
        contract_hash="x",
        status=RightsStatus.REVOKED,
        uses=[RightsUse.COMMERCIAL_ALERTS.value],
        channels=["web"],
        jurisdictions=["US"],
        assets=["EQUITY"],
        effective_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=365),
        attribution_policy_id="a",
        wind_down_policy_id="w",
        review_id="r",
    )
    db_session.add(grant)
    db_session.flush()

    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is False


def test_a_paid_subscription_status_is_not_a_rights_basis(db_session):
    """There is no `paid`/`subscribed` field this function even looks at
    -- the only inputs are source_id/use/channel/jurisdiction/asset/time.
    This test exists to make that omission a deliberate, checked
    invariant rather than something a future refactor could silently
    break by adding a "customer.is_paid" shortcut."""
    import inspect

    from app.services.rights_registry import check_rights as fn

    params = set(inspect.signature(fn).parameters)
    assert not params & {"paid", "is_paid", "subscription_status", "customer"}


def test_seed_unknown_source_is_idempotent(db_session):
    first = seed_unknown_source(db_session, "buyalerts", "Owner LLC")
    second = seed_unknown_source(db_session, "buyalerts", "Owner LLC")
    assert first.grant_id == second.grant_id

    rows = db_session.query(RightsGrant).filter_by(source_id="buyalerts").all()
    assert len(rows) == 1
    assert rows[0].status == RightsStatus.UNKNOWN


def test_seeded_unknown_source_is_denied_for_every_use(db_session):
    seed_unknown_source(db_session, "tradealgo", "Owner LLC")
    result = check_rights(
        db_session,
        source_id="tradealgo",
        use=RightsUse.PRIVATE_RESEARCH,
        channel="web",
        jurisdiction="US",
        asset="EQUITY",
    )
    assert result.allowed is False


def test_named_unknown_sources_constant_matches_the_request():
    assert set(NAMED_UNKNOWN_SOURCES) == {"buyalerts", "tradealgo", "kamdenai"}
