"""Track 34 -- GET /system/readiness real HTTP tests, same shape as
tests/test_health_metrics_endpoints.py (owner/publisher_operator-gated,
like /metrics)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.db import set_tenant_scope
from app.main import create_app
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.product import ProductLifecycleState
from app.models.rights import RightsGrant, RightsStatus, RightsUse
from app.models.sleeve import Sleeve
from app.models.tenancy import MembershipRole, Tenant
from app.services.auth import issue_token
from app.services.product_admin import create_draft_product, update_product_draft
from app.services.release_taxonomy import ReleaseStage
from app.services.trading_authority import MISSING_INPUT_EXECUTION_ACTIVATION

_NOW = datetime.now(timezone.utc)


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False)


def _auth_headers(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER):
    token = issue_token(tenant_id, user_id, role)
    return {"Authorization": f"Bearer {token}"}


def _seed_tenant(db_session, tenant_id="tenant-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name=tenant_id, environment="LOCAL_SIM"))
    db_session.commit()


def test_readiness_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/system/readiness")
    assert response.status_code == 401


def test_readiness_denies_a_customer(db_session):
    client = _client(db_session)
    response = client.get("/system/readiness", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 403


def test_readiness_is_honest_and_empty_before_any_product_exists(db_session):
    _seed_tenant(db_session)
    client = _client(db_session)
    response = client.get("/system/readiness", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["database_ok"] is True
    assert body["status"] == "ok"
    assert body["scope"] is None
    #: No single product is named -- the top-level fields stay None
    #: rather than collapsing a (currently empty) rollup into one value.
    assert body["release_status"] is None
    assert body["trading_authority"] is None
    assert body["products"] == []


def test_readiness_reports_a_real_per_product_release_status_and_trading_authority(db_session):
    _seed_tenant(db_session)
    set_tenant_scope(db_session, "tenant-a")
    sleeve = Sleeve(
        tenant_id="tenant-a", provider="acme-research", analyst="jane", strategy_horizon="swing",
        asset_class="equity", parser_version="v1", execution_policy_id="ep-1", cost_model_id="cm-1",
        capacity_policy_id="cap-1", risk_unit_id="ru-1", history_origin="acme-research",
    )
    db_session.add(sleeve)
    db_session.flush()
    portfolio_version = PortfolioVersion(
        tenant_id="tenant-a", portfolio_id="p-readiness", version_number=1, cash_weight=Decimal("0"),
        research_cutoff=_NOW, max_subscriber_capacity=100, consent_disclosure_version="v1",
    )
    db_session.add(portfolio_version)
    db_session.flush()
    db_session.add(
        PortfolioVersionSleeve(
            portfolio_version_id=portfolio_version.portfolio_version_id, sleeve_id=sleeve.sleeve_id,
            weight=Decimal("1.0"), tenant_id="tenant-a",
        )
    )
    db_session.add(
        RightsGrant(
            grant_id="grant-alerts-readiness", source_id="acme-research", grantee_entity="tenant-a",
            contract_hash="hash", status=RightsStatus.GRANTED, uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["web"], jurisdictions=["US"], assets=["equity"],
            effective_at=_NOW - timedelta(days=1), expires_at=_NOW + timedelta(days=365),
            attribution_policy_id="attr-1", wind_down_policy_id="wind-1", review_id="review-1",
        )
    )
    db_session.flush()

    product = create_draft_product(db_session, tenant_id="tenant-a", product_name="Readiness Product", slug="readiness-product")
    update_product_draft(
        db_session, product, expected_revision=1, portfolio_version_id=portfolio_version.portfolio_version_id,
        cash_bps=0, service_modes=["copying"], audience_policy_id="audience-1", research_report_id="report-1",
        methodology_document_id="method-1",
    )
    db_session.commit()
    set_tenant_scope(db_session, "tenant-a")

    client = _client(db_session)
    response = client.get("/system/readiness", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert len(body["products"]) == 1
    item = body["products"][0]
    assert item["product_id"] == product.product_id
    assert item["release_status"] == ReleaseStage.RESEARCH_ONLY.value
    assert item["trading_authority"]["applicable"] is True
    assert item["trading_authority"]["qualified"] is False
    assert item["trading_authority"]["reason"] == "not_qualified:product_not_published"
    #: Unscoped: the top-level fields stay None, matching AD-22-P05's
    #: own "do not collapse ... into one active badge" panel contract.
    assert body["release_status"] is None
    assert body["trading_authority"] is None

    #: Scoped (`?scope=<product_id>`): a single real answer, matching
    #: the per-item one.
    scoped = client.get(f"/system/readiness?scope={product.product_id}", headers=_auth_headers())
    assert scoped.status_code == 200
    scoped_body = scoped.json()
    assert scoped_body["scope"] == product.product_id
    assert scoped_body["release_status"] == ReleaseStage.RESEARCH_ONLY.value
    assert scoped_body["trading_authority"]["reason"] == "not_qualified:product_not_published"


def test_readiness_scope_to_unknown_product_is_404(db_session):
    _seed_tenant(db_session)
    client = _client(db_session)
    response = client.get("/system/readiness?scope=does-not-exist", headers=_auth_headers())
    assert response.status_code == 404


def test_readiness_never_reports_qualified_true_for_a_published_copying_product_without_execution_pipeline(
    db_session,
):
    """Even a product forced all the way to PUBLISHED with an approved
    review and granted execution rights still reports
    `MISSING_INPUT_EXECUTION_ACTIVATION` -- this is the load-bearing
    proof, at the HTTP boundary, that `/system/readiness` never
    fabricates a qualified trading-authority answer."""
    from app.models.release_review import ReleaseReview, ReleaseReviewState

    _seed_tenant(db_session)
    set_tenant_scope(db_session, "tenant-a")
    sleeve = Sleeve(
        tenant_id="tenant-a", provider="acme-research", analyst="jane", strategy_horizon="swing",
        asset_class="equity", parser_version="v1", execution_policy_id="ep-1", cost_model_id="cm-1",
        capacity_policy_id="cap-1", risk_unit_id="ru-1", history_origin="acme-research",
    )
    db_session.add(sleeve)
    db_session.flush()
    portfolio_version = PortfolioVersion(
        tenant_id="tenant-a", portfolio_id="p-fully-ready", version_number=1, cash_weight=Decimal("0"),
        research_cutoff=_NOW, max_subscriber_capacity=100, consent_disclosure_version="v1",
    )
    db_session.add(portfolio_version)
    db_session.flush()
    db_session.add(
        PortfolioVersionSleeve(
            portfolio_version_id=portfolio_version.portfolio_version_id, sleeve_id=sleeve.sleeve_id,
            weight=Decimal("1.0"), tenant_id="tenant-a",
        )
    )
    for grant_id, use, channels in (
        ("grant-alerts-fully-ready", RightsUse.COMMERCIAL_ALERTS, ["web"]),
        ("grant-exec-fully-ready", RightsUse.AUTOMATED_PUBLICATION, ["execution"]),
    ):
        db_session.add(
            RightsGrant(
                grant_id=grant_id, source_id="acme-research", grantee_entity="tenant-a", contract_hash="hash",
                status=RightsStatus.GRANTED, uses=[use.value], channels=channels, jurisdictions=["US"],
                assets=["equity"], effective_at=_NOW - timedelta(days=1), expires_at=_NOW + timedelta(days=365),
                attribution_policy_id="attr-1", wind_down_policy_id="wind-1", review_id="review-1",
            )
        )
    db_session.flush()

    product = create_draft_product(db_session, tenant_id="tenant-a", product_name="Fully Ready", slug="fully-ready")
    update_product_draft(
        db_session, product, expected_revision=1, portfolio_version_id=portfolio_version.portfolio_version_id,
        cash_bps=0, service_modes=["copying"], audience_policy_id="audience-1", research_report_id="report-1",
        methodology_document_id="method-1",
    )
    db_session.add(
        ReleaseReview(
            tenant_id="tenant-a", product_id=product.product_id, object_revision_reviewed=product.revision,
            proposer_user_id="user-proposer", evidence_manifest_id="evidence-1", audience_policy_id="audience-1",
            state=ReleaseReviewState.APPROVED, reviewer_user_id="user-reviewer", reason="approved",
            decided_at=_NOW,
        )
    )
    product.lifecycle_state = ProductLifecycleState.PUBLISHED
    db_session.commit()
    set_tenant_scope(db_session, "tenant-a")

    client = _client(db_session)
    response = client.get(f"/system/readiness?scope={product.product_id}", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["release_status"] == ReleaseStage.FULLY_RELEASED.value
    assert body["trading_authority"]["qualified"] is False
    assert body["trading_authority"]["reason"] == MISSING_INPUT_EXECUTION_ACTIVATION
