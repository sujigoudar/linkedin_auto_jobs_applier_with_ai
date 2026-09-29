"""AD-08 "Release and change approvals" -- the release-review service's
own tests. Runs through `tenant_session_factory` + `set_tenant_scope`
like tests/test_product_admin.py, since this reuses
`product_admin.compute_publication_blockers` and mutates `Product` rows
those RLS policies protect.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db import set_tenant_scope
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.product import ProductLifecycleState
from app.models.release_review import ReleaseReviewState
from app.models.rights import RightsGrant, RightsStatus, RightsUse
from app.models.sleeve import Sleeve
from app.models.tenancy import Tenant
from app.services.product_admin import create_draft_product, update_product_draft
from app.services.release_review import (
    InvalidReviewDecisionError,
    ReviewNotEligibleError,
    SelfReviewNotAllowedError,
    StaleReviewTargetError,
    decide_release_review,
    get_release_review,
    list_release_reviews,
    request_release_review,
)


def _seed_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def _ready_product(session, *, tenant_id, slug):
    """A product with zero publication blockers, real enough to be
    submitted for review -- mirrors tests/test_product_admin.py's own
    `_seed_portfolio_version_with_one_sleeve` fixture."""
    sleeve = Sleeve(
        tenant_id=tenant_id,
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
    session.add(sleeve)
    session.flush()

    portfolio_version = PortfolioVersion(
        tenant_id=tenant_id,
        portfolio_id="p-01",
        version_number=1,
        cash_weight=Decimal("0"),
        research_cutoff=datetime.now(timezone.utc),
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    session.add(portfolio_version)
    session.flush()
    session.add(
        PortfolioVersionSleeve(
            portfolio_version_id=portfolio_version.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("1.0"),
            tenant_id=tenant_id,
        )
    )
    grant = RightsGrant(
        grant_id=f"grant-{slug}",
        source_id="acme-research",
        grantee_entity="tenant-a",
        contract_hash="hash",
        status=RightsStatus.GRANTED,
        uses=[RightsUse.COMMERCIAL_ALERTS.value],
        channels=["web"],
        jurisdictions=["US"],
        assets=["equity"],
        effective_at=datetime.now(timezone.utc) - timedelta(days=1),
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
        attribution_policy_id="attr-1",
        wind_down_policy_id="wind-1",
        review_id="review-1",
    )
    session.add(grant)
    session.flush()

    product = create_draft_product(session, tenant_id=tenant_id, product_name="Ready", slug=slug)
    update_product_draft(
        session,
        product,
        expected_revision=1,
        portfolio_version_id=portfolio_version.portfolio_version_id,
        cash_bps=0,
        service_modes=["alerts"],
        audience_policy_id="audience-1",
        research_report_id="report-1",
        methodology_document_id="method-1",
    )
    return product, grant


def test_request_review_fails_when_product_has_blockers(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="Blocked", slug="blocked-review")
        with pytest.raises(ReviewNotEligibleError):
            request_release_review(
                session,
                tenant_id="tenant-a",
                product=product,
                proposer_user_id="user-proposer",
                evidence_manifest_id="evidence-1",
                audience_policy_id="audience-1",
            )
    finally:
        session.rollback()
        session.close()


def test_request_review_succeeds_and_moves_product_to_validated(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, _grant = _ready_product(session, tenant_id="tenant-a", slug="ready-review")
        review = request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        assert review.state == ReleaseReviewState.QUEUED
        assert review.object_revision_reviewed == product.revision
        assert product.lifecycle_state == ProductLifecycleState.VALIDATED

        listed = list_release_reviews(session, tenant_id="tenant-a")
        assert [r.release_review_id for r in listed] == [review.release_review_id]
    finally:
        session.rollback()
        session.close()


def test_request_review_refuses_a_second_request_while_already_validated(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, _grant = _ready_product(session, tenant_id="tenant-a", slug="double-request")
        request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        with pytest.raises(ReviewNotEligibleError):
            request_release_review(
                session,
                tenant_id="tenant-a",
                product=product,
                proposer_user_id="user-proposer",
                evidence_manifest_id="evidence-2",
                audience_policy_id="audience-1",
            )
    finally:
        session.rollback()
        session.close()


def test_decide_approve_moves_product_to_approved(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, _grant = _ready_product(session, tenant_id="tenant-a", slug="approve-me")
        review = request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        decide_release_review(
            session, review, product, reviewer_user_id="user-reviewer", decision="approve", reason="looks good"
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        assert review.state == ReleaseReviewState.APPROVED
        assert product.lifecycle_state == ProductLifecycleState.APPROVED
        assert review.reviewer_user_id == "user-reviewer"
    finally:
        session.rollback()
        session.close()


def test_decide_reject_returns_product_to_draft(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, _grant = _ready_product(session, tenant_id="tenant-a", slug="reject-me")
        review = request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        decide_release_review(
            session, review, product, reviewer_user_id="user-reviewer", decision="reject", reason="not ready"
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        assert review.state == ReleaseReviewState.REJECTED
        assert product.lifecycle_state == ProductLifecycleState.DRAFT
    finally:
        session.rollback()
        session.close()


def test_decide_refuses_self_review(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, _grant = _ready_product(session, tenant_id="tenant-a", slug="self-review")
        review = request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        with pytest.raises(SelfReviewNotAllowedError):
            decide_release_review(
                session, review, product, reviewer_user_id="user-proposer", decision="approve", reason="self"
            )
    finally:
        session.rollback()
        session.close()


def test_decide_refuses_a_stale_review_target(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, _grant = _ready_product(session, tenant_id="tenant-a", slug="stale-review")
        review = request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        # Simulate the product changing after the review request the
        # only way that's currently possible while VALIDATED -- directly,
        # since update_product_draft doesn't gate on lifecycle_state.
        product.revision += 1
        session.commit()
        set_tenant_scope(session, "tenant-a")

        with pytest.raises(StaleReviewTargetError):
            decide_release_review(
                session, review, product, reviewer_user_id="user-reviewer", decision="approve", reason="stale"
            )
    finally:
        session.rollback()
        session.close()


def test_decide_approve_rechecks_gates_and_refuses_if_rights_were_revoked_meanwhile(
    db_session, tenant_session_factory
):
    """A grant revoked after the review was requested changes
    compute_publication_blockers's answer without touching the
    product's own `revision` -- this is exactly why approval re-checks
    gates independently of the stale-revision check."""
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, grant = _ready_product(session, tenant_id="tenant-a", slug="revoked-mid-review")
        review = request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        grant.status = RightsStatus.REVOKED
        session.commit()
        set_tenant_scope(session, "tenant-a")

        with pytest.raises(ReviewNotEligibleError):
            decide_release_review(
                session, review, product, reviewer_user_id="user-reviewer", decision="approve", reason="approve"
            )
    finally:
        session.rollback()
        session.close()


def test_decide_rejects_an_unknown_decision_value(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product, _grant = _ready_product(session, tenant_id="tenant-a", slug="bad-decision")
        review = request_release_review(
            session,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        with pytest.raises(InvalidReviewDecisionError):
            decide_release_review(
                session, review, product, reviewer_user_id="user-reviewer", decision="maybe", reason="???"
            )
    finally:
        session.rollback()
        session.close()


def test_get_release_review_cross_tenant_returns_none(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        product, _grant = _ready_product(session_a, tenant_id="tenant-a", slug="cross-tenant-review")
        review = request_release_review(
            session_a,
            tenant_id="tenant-a",
            product=product,
            proposer_user_id="user-proposer",
            evidence_manifest_id="evidence-1",
            audience_policy_id="audience-1",
        )
        session_a.commit()
        set_tenant_scope(session_a, "tenant-a")
        review_id = review.release_review_id
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        assert get_release_review(session_b, review_id, tenant_id="tenant-b") is None
        assert list_release_reviews(session_b, tenant_id="tenant-b") == []
    finally:
        session_b.rollback()
        session_b.close()


def test_list_release_reviews_is_empty_before_any_review_is_requested(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        assert list_release_reviews(session, tenant_id="tenant-a") == []
    finally:
        session.rollback()
        session.close()
