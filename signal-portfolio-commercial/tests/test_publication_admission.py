"""app/services/publication_admission.py -- the real effect boundary
combining a fresh rights recheck (CP-003), entitlement gating for new
exposure (CP-051), and single-writer-authority claiming (CP-045)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.publication import Environment, PublicationAction, PublicationIntent, QuantityBasis
from app.models.rights import RightsGrant, RightsStatus, RightsUse
from app.models.sleeve import Sleeve
from app.services.publication_admission import (
    EntitlementDeniedAtAdmissionError,
    RightsDeniedAtAdmissionError,
    admit_publication_intent,
)
from app.services.publisher_writer_claim import WriterAlreadyClaimedError


def _sleeve(session, sleeve_id, provider):
    session.add(
        Sleeve(
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
    )


def _grant(session, source_id, *, use=RightsUse.AUTOMATED_PUBLICATION):
    now = datetime.now(timezone.utc)
    session.add(
        RightsGrant(
            grant_id=f"grant-{source_id}",
            source_id=source_id,
            grantee_entity="Owner LLC",
            contract_hash="deadbeef",
            status=RightsStatus.GRANTED,
            uses=[use.value],
            channels=["collective2"],
            jurisdictions=["US"],
            assets=["EQUITY"],
            effective_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=365),
            attribution_policy_id="attrib-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
    )


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
    for sleeve_id in sleeve_ids:
        session.add(PortfolioVersionSleeve(portfolio_version_id=portfolio_version_id, sleeve_id=sleeve_id, weight=Decimal("0.85")))


def _subscription(state=SubscriptionState.ACTIVE_PAID):
    return Subscription(
        tenant_id="tenant-a",
        tier=ProductTier.ALERTS_ONE,
        state=state,
        price_cents=3900,
        currency="usd",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )


def _intent(**overrides) -> PublicationIntent:
    now = datetime.now(timezone.utc)
    defaults = dict(
        environment=Environment.LOCAL_SIM,
        portfolio_version_id="pv-1",
        episode_id="ep-1",
        revision=1,
        action=PublicationAction.OPEN,
        channel="collective2",
        external_strategy_id="strategy-1",
        instrument_id="AAPL",
        quantity="10",
        quantity_basis=QuantityBasis.UNITS,
        price_basis="market",
        policy_hash="policy-hash-1",
        audience_snapshot_hash="audience-hash-1",
        source_revision_ids=["src-rev-1"],
        rights_grant_ids=["grant-1"],
        body_hash="body-hash-1",
        idempotency_key="idem-1",
        valid_from=now,
        expires_at=now + timedelta(hours=1),
    )
    defaults.update(overrides)
    return PublicationIntent(**defaults)


def test_a_fully_qualified_open_is_admitted(db_session):
    _sleeve(db_session, "sleeve-a", "acme")
    _grant(db_session, "acme")
    _portfolio_version(db_session, "pv-1", ["sleeve-a"])
    db_session.commit()

    admitted = admit_publication_intent(
        db_session,
        _intent(),
        portfolio_version_id="pv-1",
        writer_identity="worker-a",
        subscription=_subscription(),
        jurisdiction="US",
        asset="EQUITY",
    )
    assert admitted.intent_id is not None


def test_a_rights_denied_portfolio_blocks_admission(db_session):
    _sleeve(db_session, "sleeve-a", "unknown-provider")  # no grant
    _portfolio_version(db_session, "pv-1", ["sleeve-a"])
    db_session.commit()

    with pytest.raises(RightsDeniedAtAdmissionError):
        admit_publication_intent(
            db_session,
            _intent(),
            portfolio_version_id="pv-1",
            writer_identity="worker-a",
            subscription=_subscription(),
            jurisdiction="US",
            asset="EQUITY",
        )


def test_an_unentitled_subscription_blocks_a_new_open(db_session):
    _sleeve(db_session, "sleeve-a", "acme")
    _grant(db_session, "acme")
    _portfolio_version(db_session, "pv-1", ["sleeve-a"])
    db_session.commit()

    with pytest.raises(EntitlementDeniedAtAdmissionError):
        admit_publication_intent(
            db_session,
            _intent(),
            portfolio_version_id="pv-1",
            writer_identity="worker-a",
            subscription=_subscription(SubscriptionState.PAST_DUE),
            jurisdiction="US",
            asset="EQUITY",
        )


def test_a_stop_update_is_admitted_with_no_subscription_at_all(db_session):
    """Risk-reducing management (STOP_UPDATE) is not gated by
    entitlement -- see app/services/entitlement.py's own separation.
    A rights-eligible STOP_UPDATE succeeds even with subscription=None,
    which would fail EntitlementDeniedAtAdmissionError for an OPEN/ADD."""
    _sleeve(db_session, "sleeve-a", "acme")
    _grant(db_session, "acme")
    _portfolio_version(db_session, "pv-1", ["sleeve-a"])
    db_session.commit()

    admitted = admit_publication_intent(
        db_session,
        _intent(action=PublicationAction.STOP_UPDATE, quantity=None),
        portfolio_version_id="pv-1",
        writer_identity="worker-a",
        subscription=None,
        jurisdiction="US",
        asset="EQUITY",
    )
    assert admitted.intent_id is not None


def test_a_second_writer_for_the_same_strategy_is_rejected(db_session):
    _sleeve(db_session, "sleeve-a", "acme")
    _grant(db_session, "acme")
    _portfolio_version(db_session, "pv-1", ["sleeve-a"])
    db_session.commit()

    admit_publication_intent(
        db_session,
        _intent(),
        portfolio_version_id="pv-1",
        writer_identity="worker-a",
        subscription=_subscription(),
        jurisdiction="US",
        asset="EQUITY",
    )
    db_session.commit()

    with pytest.raises(WriterAlreadyClaimedError):
        admit_publication_intent(
            db_session,
            _intent(idempotency_key="idem-2", body_hash="body-2", revision=2),
            portfolio_version_id="pv-1",
            writer_identity="worker-b",
            subscription=_subscription(),
            jurisdiction="US",
            asset="EQUITY",
        )
