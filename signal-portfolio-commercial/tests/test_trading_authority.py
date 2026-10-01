"""Track 34 -- `app.services.trading_authority.assess_trading_authority`'s
own tests. Runs through `tenant_session_factory` + `set_tenant_scope`,
same shape as tests/test_release_review.py (reuses
`product_admin.compute_publication_blockers` and reads/writes `Product`/
`ReleaseReview`/`RightsGrant`/`Incident` rows those RLS policies
protect)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db import set_tenant_scope
from app.models.incident import Incident, IncidentService, IncidentSeverity, IncidentState
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.product import ProductLifecycleState
from app.models.release_review import ReleaseReview, ReleaseReviewState
from app.models.rights import RightsGrant, RightsStatus, RightsUse
from app.models.sleeve import Sleeve
from app.models.tenancy import Tenant
from app.services.product_admin import create_draft_product, update_product_draft
from app.services.trading_authority import MISSING_INPUT_EXECUTION_ACTIVATION, assess_trading_authority

_NOW = datetime.now(timezone.utc)


def _seed_tenant(db_session, tenant_id="tenant-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name=tenant_id, environment="LOCAL_SIM"))
    db_session.commit()


def _grant(*, grant_id, source_id, tenant_id, use: RightsUse, channels, assets=("equity",)):
    return RightsGrant(
        grant_id=grant_id,
        source_id=source_id,
        grantee_entity=tenant_id,
        contract_hash="hash",
        status=RightsStatus.GRANTED,
        uses=[use.value],
        channels=list(channels),
        jurisdictions=["US"],
        assets=list(assets),
        effective_at=_NOW - timedelta(days=1),
        expires_at=_NOW + timedelta(days=365),
        attribution_policy_id="attr-1",
        wind_down_policy_id="wind-1",
        review_id="review-1",
    )


def _ready_product(session, *, tenant_id, slug, service_modes, grant_automated_publication=False):
    """A product with zero publication blockers and the requested
    `service_modes` -- mirrors tests/test_release_review.py's own
    `_ready_product` fixture, extended to optionally also grant
    `AUTOMATED_PUBLICATION` rights (the order-routing use this module
    checks, distinct from the `COMMERCIAL_ALERTS` grant
    `compute_publication_blockers` itself checks)."""
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
        portfolio_id=f"p-{slug}",
        version_number=1,
        cash_weight=Decimal("0"),
        research_cutoff=_NOW,
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
    session.add(_grant(grant_id=f"grant-alerts-{slug}", source_id="acme-research", tenant_id=tenant_id,
                        use=RightsUse.COMMERCIAL_ALERTS, channels=["web"]))
    if grant_automated_publication:
        session.add(_grant(grant_id=f"grant-exec-{slug}", source_id="acme-research", tenant_id=tenant_id,
                            use=RightsUse.AUTOMATED_PUBLICATION, channels=["execution"]))
    session.flush()

    product = create_draft_product(session, tenant_id=tenant_id, product_name=slug, slug=slug)
    update_product_draft(
        session,
        product,
        expected_revision=1,
        portfolio_version_id=portfolio_version.portfolio_version_id,
        cash_bps=0,
        service_modes=service_modes,
        audience_policy_id="audience-1",
        research_report_id="report-1",
        methodology_document_id="method-1",
    )
    return product


def _approve(session, product, *, tenant_id, slug):
    review = ReleaseReview(
        tenant_id=tenant_id,
        product_id=product.product_id,
        object_revision_reviewed=product.revision,
        proposer_user_id="user-proposer",
        evidence_manifest_id="evidence-1",
        audience_policy_id="audience-1",
        state=ReleaseReviewState.APPROVED,
        reviewer_user_id="user-reviewer",
        reason="approved for test",
        decided_at=_NOW,
    )
    session.add(review)
    product.lifecycle_state = ProductLifecycleState.APPROVED
    session.flush()
    return review


def test_alerts_only_product_is_not_applicable(db_session, tenant_session_factory):
    _seed_tenant(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = _ready_product(session, tenant_id="tenant-a", slug="alerts-only", service_modes=["alerts"])
        assessment = assess_trading_authority(session, product)
        assert assessment.applicable is False
        assert assessment.qualified is None
        assert assessment.reason == "not_applicable"
    finally:
        session.rollback()
        session.close()


def test_unpublished_copying_product_is_not_qualified(db_session, tenant_session_factory):
    _seed_tenant(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = _ready_product(session, tenant_id="tenant-a", slug="draft-copy", service_modes=["copying"])
        assert product.lifecycle_state == ProductLifecycleState.DRAFT

        assessment = assess_trading_authority(session, product)
        assert assessment.applicable is True
        assert assessment.qualified is False
        assert assessment.reason == "not_qualified:product_not_published"
    finally:
        session.rollback()
        session.close()


def test_published_product_with_outstanding_blockers_is_not_qualified(db_session, tenant_session_factory):
    """A product forced directly to PUBLISHED (bypassing the normal
    pipeline, same "mutate the row directly to exercise this gate in
    isolation" precedent tests/test_release_review.py's own stale-
    revision test uses) with no portfolio version selected at all is
    still correctly refused -- `product_published` passing is not
    enough on its own."""
    _seed_tenant(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="Blocked", slug="blocked-copy")
        product.service_modes = ["copying"]
        product.lifecycle_state = ProductLifecycleState.PUBLISHED
        session.flush()

        assessment = assess_trading_authority(session, product)
        assert assessment.applicable is True
        assert assessment.qualified is False
        assert assessment.reason.startswith("not_qualified:publication_blockers:")
        assert "NO_PORTFOLIO_VERSION_SELECTED" in assessment.reason
    finally:
        session.rollback()
        session.close()


def test_published_product_with_no_approved_review_is_not_qualified(db_session, tenant_session_factory):
    _seed_tenant(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = _ready_product(
            session, tenant_id="tenant-a", slug="published-no-review", service_modes=["copying"],
            grant_automated_publication=True,
        )
        product.lifecycle_state = ProductLifecycleState.PUBLISHED
        session.flush()

        assessment = assess_trading_authority(session, product)
        assert assessment.qualified is False
        assert assessment.reason == "not_qualified:no_approved_release_review_for_current_revision"
    finally:
        session.rollback()
        session.close()


def test_published_product_without_execution_rights_is_not_qualified(db_session, tenant_session_factory):
    _seed_tenant(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = _ready_product(
            session, tenant_id="tenant-a", slug="no-exec-rights", service_modes=["copying"],
            grant_automated_publication=False,
        )
        _approve(session, product, tenant_id="tenant-a", slug="no-exec-rights")
        product.lifecycle_state = ProductLifecycleState.PUBLISHED
        session.flush()

        assessment = assess_trading_authority(session, product)
        assert assessment.qualified is False
        assert assessment.reason.startswith("not_qualified:order_routing_rights_not_granted")
    finally:
        session.rollback()
        session.close()


def test_blocking_incident_is_not_qualified(db_session, tenant_session_factory):
    _seed_tenant(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = _ready_product(
            session, tenant_id="tenant-a", slug="incident-blocked", service_modes=["copying"],
            grant_automated_publication=True,
        )
        _approve(session, product, tenant_id="tenant-a", slug="incident-blocked")
        product.lifecycle_state = ProductLifecycleState.PUBLISHED
        session.flush()

        incident = Incident(
            tenant_id="tenant-a",
            service=IncidentService.CUSTOMER_EXPOSURE,
            severity=IncidentSeverity.CRITICAL,
            state=IncidentState.OPEN,
            title="real exposure incident",
            affected_object_type="product",
            affected_object_id=product.product_id,
        )
        session.add(incident)
        session.flush()

        assessment = assess_trading_authority(session, product)
        assert assessment.qualified is False
        assert assessment.reason == f"not_qualified:open_incident:{incident.incident_id}"
    finally:
        session.rollback()
        session.close()


def test_fully_qualified_product_still_fails_closed_on_missing_execution_pipeline(db_session, tenant_session_factory):
    """Every real platform-side gate this build has passes -- product
    published, current revision independently approved, order-routing
    rights granted, no blocking incident -- and the gate STILL reports
    `qualified=False`, because this build genuinely has no execution-
    activation pipeline for `CopyMandate`/`ManagedProgram` yet. This is
    the load-bearing proof that this gate never fabricates a pass."""
    _seed_tenant(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = _ready_product(
            session, tenant_id="tenant-a", slug="fully-ready", service_modes=["copying"],
            grant_automated_publication=True,
        )
        _approve(session, product, tenant_id="tenant-a", slug="fully-ready")
        product.lifecycle_state = ProductLifecycleState.PUBLISHED
        session.flush()

        assessment = assess_trading_authority(session, product)
        assert assessment.applicable is True
        assert assessment.qualified is False
        assert assessment.reason == MISSING_INPUT_EXECUTION_ACTIVATION
        assert assessment.checks["product_published"] == "PASS"
        assert assessment.checks["no_publication_blockers"] == "PASS"
        assert assessment.checks["has_current_approved_release_review"] == "PASS"
        assert assessment.checks["order_routing_rights_granted"] == "PASS"
        assert assessment.checks["no_blocking_open_incident"] == "PASS"
        assert assessment.checks["execution_activation_pipeline"] == "MISSING"
    finally:
        session.rollback()
        session.close()
