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
    list_rights_grants,
    seed_unknown_source,
)


def _granted(
    session,
    *,
    source_id="acme",
    grant_id=None,
    uses=(RightsUse.COMMERCIAL_ALERTS.value,),
    channels=("web",),
    jurisdictions=("US",),
    assets=("EQUITY",),
    effective_at=None,
    expires_at=None,
):
    now = datetime.now(timezone.utc)
    grant = RightsGrant(
        grant_id=grant_id or f"grant-{source_id}",
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
    # `_DENIED_NO_GRANT`'s own `grant_id` relies on the dataclass field's
    # default -- it must stay None, never "" or any other falsy sentinel
    # a caller might mistake for a real (if empty) grant id.
    assert result.grant_id is None


def test_a_matching_granted_grant_is_allowed(db_session):
    grant = _granted(db_session)
    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is True
    assert result.grant_id == grant.grant_id
    assert result.reason == "GRANTED"


def test_an_effective_at_exactly_equal_to_now_is_allowed_not_denied(db_session):
    """`check_rights` denies only `effective_at > now` (strictly in the
    future) -- a grant becoming effective at this exact instant must
    already count as effective, not be treated as "not yet" for one
    more tick."""
    now = datetime.now(timezone.utc)
    grant = _granted(db_session, effective_at=now, expires_at=now + timedelta(days=365))
    result = check_rights(
        db_session,
        source_id="acme",
        use=RightsUse.COMMERCIAL_ALERTS,
        channel="web",
        jurisdiction="US",
        asset="EQUITY",
        at=now,
    )
    assert result.allowed is True
    assert result.grant_id == grant.grant_id


def test_an_expires_at_exactly_equal_to_now_is_allowed_not_denied(db_session):
    """Symmetric to the above: `check_rights` denies only `expires_at <
    now` (strictly in the past) -- a grant expiring at this exact
    instant has not yet lapsed."""
    now = datetime.now(timezone.utc)
    grant = _granted(db_session, effective_at=now - timedelta(days=30), expires_at=now)
    result = check_rights(
        db_session,
        source_id="acme",
        use=RightsUse.COMMERCIAL_ALERTS,
        channel="web",
        jurisdiction="US",
        asset="EQUITY",
        at=now,
    )
    assert result.allowed is True
    assert result.grant_id == grant.grant_id


def test_a_non_matching_grant_does_not_short_circuit_a_later_matching_one(db_session):
    """`check_rights` loops over every GRANTED grant for a source_id and
    must keep checking subsequent grants when an earlier one fails any
    single criterion (use/window/channel/jurisdiction/asset) -- it must
    never give up on the whole source_id just because the FIRST grant it
    happens to see doesn't match. One grant per failing criterion, each
    one otherwise valid, followed by a grant that matches everything."""
    now = datetime.now(timezone.utc)
    _granted(
        db_session, source_id="acme", grant_id="grant-acme-1", uses=(RightsUse.PRIVATE_RESEARCH.value,)
    )  # fails on `use`
    _granted(
        db_session,
        source_id="acme",
        grant_id="grant-acme-2",
        effective_at=now + timedelta(days=1),
        expires_at=now + timedelta(days=365),
    )  # fails on window (not yet effective)
    _granted(
        db_session, source_id="acme", grant_id="grant-acme-3", channels=("collective2",)
    )  # fails on `channel`
    _granted(
        db_session, source_id="acme", grant_id="grant-acme-4", jurisdictions=("UK",)
    )  # fails on `jurisdiction`
    _granted(
        db_session, source_id="acme", grant_id="grant-acme-5", assets=("CRYPTO",)
    )  # fails on `asset`
    _granted(db_session, source_id="acme", grant_id="grant-acme-good")  # matches everything

    result = check_rights(
        db_session, source_id="acme", use=RightsUse.COMMERCIAL_ALERTS, channel="web", jurisdiction="US", asset="EQUITY"
    )
    assert result.allowed is True
    assert result.grant_id == "grant-acme-good"


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


def test_list_rights_grants_is_empty_before_any_grant_is_recorded(db_session):
    """AD-02's own empty state -- "No commercial rights grants have been
    approved" -- is a real, valid result, not a fixture gap."""
    assert list_rights_grants(db_session) == []


def test_list_rights_grants_returns_every_recorded_grant_regardless_of_status(db_session):
    granted = _granted(db_session, source_id="acme")
    seeded_unknown = seed_unknown_source(db_session, "tradealgo", "Owner LLC")

    grants = list_rights_grants(db_session)
    assert {g.grant_id for g in grants} == {granted.grant_id, seeded_unknown.grant_id}
