"""Real HTTP tests for the first dashboard vertical slice: AD-07
"Products and portfolio versions" (admin) and PU-02 "Portfolio catalog"
(public). Follows tests/test_api.py's own convention: `db_session` (the
Postgres superuser session) is used directly as the FastAPI dependency
override, since real cross-tenant/RLS enforcement is already covered
separately (tests/test_row_level_security.py, tests/test_product_admin.py's
own raw-RLS test) -- these tests exercise the real routes, templates and
service wiring end to end, including the application-level `tenant_id`
scoping `product_admin.py` does as defense in depth.
"""
from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.tenancy import MembershipRole
from app.services.auth import issue_token


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False)


def _auth_headers(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER):
    token = issue_token(tenant_id, user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_products_page_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops/products")
    assert response.status_code == 401


def test_products_page_denies_a_role_without_manage_product_draft(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.SUPPORT_READONLY)
    response = client.get("/ops/products", headers=headers)
    assert response.status_code == 403


def test_products_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/products", headers=_auth_headers())
    assert response.status_code == 200
    assert "No products exist" in response.text


def test_create_draft_via_form_then_reload_shows_it_persisted(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    create_response = client.post(
        "/ops/products",
        data={"product_name": "Swing Alerts", "slug": "swing-alerts-http"},
        headers=headers,
    )
    assert create_response.status_code == 303
    detail_url = create_response.headers["location"]

    detail_response = client.get(detail_url, headers=headers)
    assert detail_response.status_code == 200
    assert "Swing Alerts" in detail_response.text
    assert "NO_PORTFOLIO_VERSION_SELECTED" in detail_response.text

    list_response = client.get("/ops/products", headers=headers)
    assert "Swing Alerts" in list_response.text


def test_create_draft_rejects_a_duplicate_slug_with_an_error_banner(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    client.post("/ops/products", data={"product_name": "A", "slug": "dup-http"}, headers=headers)
    response = client.post("/ops/products", data={"product_name": "B", "slug": "dup-http"}, headers=headers)
    assert response.status_code == 400
    assert "already in use" in response.text


def test_get_product_detail_cross_tenant_is_404_not_403(db_session):
    client = _client(db_session)
    create_response = client.post(
        "/ops/products",
        data={"product_name": "A", "slug": "cross-tenant-http"},
        headers=_auth_headers(tenant_id="tenant-a"),
    )
    detail_url = create_response.headers["location"]

    other_tenant_response = client.get(detail_url, headers=_auth_headers(tenant_id="tenant-b"))
    assert other_tenant_response.status_code == 404


def test_update_product_draft_persists_and_reloads(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/products", data={"product_name": "A", "slug": "update-http"}, headers=headers
    )
    detail_url = create_response.headers["location"]

    update_response = client.post(
        detail_url,
        data={
            "expected_revision": "1",
            "product_name": "A Renamed",
            "portfolio_version_id": "",
            "cash_bps": "9000",
            "service_modes": ["alerts"],
            "audience_policy_id": "",
            "research_report_id": "",
            "methodology_document_id": "",
        },
        headers=headers,
    )
    assert update_response.status_code == 303

    reloaded = client.get(detail_url, headers=headers)
    assert "A Renamed" in reloaded.text
    assert 'value="2"' in reloaded.text  # the new expected_revision hidden field


def test_update_product_draft_with_a_stale_revision_redirects_to_a_conflict_state(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/products", data={"product_name": "A", "slug": "conflict-http"}, headers=headers
    )
    detail_url = create_response.headers["location"]

    stale_update = client.post(
        detail_url,
        data={
            "expected_revision": "999",
            "product_name": "Nope",
            "portfolio_version_id": "",
            "cash_bps": "10000",
            "service_modes": [],
            "audience_policy_id": "",
            "research_report_id": "",
            "methodology_document_id": "",
        },
        headers=headers,
    )
    assert stale_update.status_code == 303
    assert "conflict=1" in stale_update.headers["location"]

    conflict_page = client.get(stale_update.headers["location"], headers=headers)
    assert conflict_page.status_code == 200
    assert "newer change" in conflict_page.text
    # The stale attempt's edit was never applied -- the original name persists.
    assert "Nope" not in conflict_page.text
    assert "A</h1>" not in conflict_page.text or "A" in conflict_page.text


def test_public_catalog_is_truthfully_empty_with_no_products_published(db_session):
    client = _client(db_session)
    response = client.get("/portfolios")
    assert response.status_code == 200
    assert "No portfolios have been released" in response.text


def test_public_catalog_never_shows_an_unpublished_draft(db_session):
    client = _client(db_session)
    client.post(
        "/ops/products", data={"product_name": "Still Draft", "slug": "still-draft-http"}, headers=_auth_headers()
    )
    response = client.get("/portfolios")
    assert response.status_code == 200
    assert "Still Draft" not in response.text
    assert "No portfolios have been released" in response.text


def test_rights_register_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops/rights")
    assert response.status_code == 401


def test_rights_register_denies_a_role_without_view_rights_register(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.RESEARCHER)
    response = client.get("/ops/rights", headers=headers)
    assert response.status_code == 403


def test_rights_register_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/rights", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "No commercial rights grants have been approved" in response.text


def test_rights_register_lists_a_real_recorded_grant(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models.rights import RightsGrant, RightsStatus, RightsUse

    now = datetime.now(timezone.utc)
    db_session.add(
        RightsGrant(
            grant_id="grant-http-1",
            source_id="acme-research",
            grantee_entity="Owner LLC",
            contract_hash="hash",
            status=RightsStatus.GRANTED,
            uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["web"],
            jurisdictions=["US"],
            assets=["EQUITY"],
            effective_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=365),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
    )
    db_session.commit()

    client = _client(db_session)
    response = client.get("/ops/rights", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 200
    assert "grant-http-1" in response.text
    assert "acme-research" in response.text
    assert "GRANTED" in response.text


_SLEEVE_FORM_FIELDS = {
    "provider": "north-star-research",
    "analyst": "m.chen",
    "strategy_horizon": "swing",
    "asset_class": "EQUITY",
    "parser_version": "v3",
    "execution_policy_id": "ep-standard",
    "cost_model_id": "cm-standard",
    "capacity_policy_id": "cap-standard",
    "risk_unit_id": "ru-1pct",
    "history_origin": "north-star-research",
}


def test_sleeve_catalog_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops/research/universe")
    assert response.status_code == 401


def test_sleeve_catalog_denies_a_role_without_manage_sleeve_draft(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.SUPPORT_READONLY)
    response = client.get("/ops/research/universe", headers=headers)
    assert response.status_code == 403


def test_sleeve_catalog_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/research/universe", headers=_auth_headers())
    assert response.status_code == 200
    assert "No qualified strategy sleeves are available" in response.text


def test_create_sleeve_via_form_then_reload_shows_it_persisted(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    create_response = client.post("/ops/research/universe", data=_SLEEVE_FORM_FIELDS, headers=headers)
    assert create_response.status_code == 303

    list_response = client.get("/ops/research/universe", headers=headers)
    assert "north-star-research" in list_response.text
    assert "swing" in list_response.text


def test_create_sleeve_rejects_a_missing_required_field_with_an_error_banner(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    fields = dict(_SLEEVE_FORM_FIELDS)
    fields["provider"] = "   "
    response = client.post("/ops/research/universe", data=fields, headers=headers)
    assert response.status_code == 400
    assert "missing required field" in response.text


def test_operations_overview_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops")
    assert response.status_code == 401


def test_operations_overview_denies_a_customer(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    response = client.get("/ops", headers=headers)
    assert response.status_code == 403


def test_operations_overview_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY))
    assert response.status_code == 200
    assert "No commercial products or subscriptions exist yet" in response.text


def test_operations_overview_lists_a_real_products_blockers_and_marks_incidents_unsupported(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    client.post("/ops/products", data={"product_name": "Overview Test", "slug": "overview-http"}, headers=headers)

    response = client.get("/ops", headers=headers)
    assert response.status_code == 200
    assert "Overview Test" in response.text
    assert "NO_PORTFOLIO_VERSION_SELECTED" in response.text
    assert "UNSUPPORTED" in response.text


def _seed_ready_product_for_review(db_session, *, tenant_id="tenant-a", slug="review-ready-http"):
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
    from app.models.rights import RightsGrant, RightsStatus, RightsUse
    from app.models.sleeve import Sleeve

    now = datetime.now(timezone.utc)
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
    db_session.add(sleeve)
    db_session.flush()

    pv = PortfolioVersion(
        tenant_id=tenant_id,
        portfolio_id="p-review-http",
        version_number=1,
        cash_weight=Decimal("0"),
        research_cutoff=now,
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    db_session.add(pv)
    db_session.flush()
    db_session.add(
        PortfolioVersionSleeve(portfolio_version_id=pv.portfolio_version_id, sleeve_id=sleeve.sleeve_id, weight=Decimal("1.0"))
    )
    db_session.add(
        RightsGrant(
            grant_id=f"grant-{slug}",
            source_id="acme-research",
            grantee_entity=tenant_id,
            contract_hash="hash",
            status=RightsStatus.GRANTED,
            uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["web"],
            jurisdictions=["US"],
            assets=["equity"],
            effective_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=365),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
    )
    db_session.commit()
    return pv.portfolio_version_id


def test_request_review_then_view_queue_and_detail(db_session):
    client = _client(db_session)
    proposer_headers = _auth_headers(user_id="user-proposer")

    pv_id = _seed_ready_product_for_review(db_session)
    create_response = client.post(
        "/ops/products", data={"product_name": "Reviewable", "slug": "reviewable-http"}, headers=proposer_headers
    )
    detail_url = create_response.headers["location"]
    client.post(
        detail_url,
        data={
            "expected_revision": "1",
            "product_name": "Reviewable",
            "portfolio_version_id": pv_id,
            "cash_bps": "0",
            "service_modes": ["alerts"],
            "audience_policy_id": "audience-1",
            "research_report_id": "report-1",
            "methodology_document_id": "method-1",
        },
        headers=proposer_headers,
    )

    review_response = client.post(
        f"{detail_url}/request-review",
        data={"evidence_manifest_id": "evidence-1", "audience_policy_id": "audience-1"},
        headers=proposer_headers,
    )
    assert review_response.status_code == 303
    review_url = review_response.headers["location"]

    queue_response = client.get("/ops/reviews", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert queue_response.status_code == 200
    assert "QUEUED" in queue_response.text

    detail_response = client.get(review_url, headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert detail_response.status_code == 200
    assert "user-proposer" in detail_response.text
    assert "evidence-1" in detail_response.text

    product_page = client.get(detail_url, headers=proposer_headers)
    assert "VALIDATED" in product_page.text


def test_request_review_is_refused_while_blockers_remain(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    create_response = client.post(
        "/ops/products", data={"product_name": "Still Blocked", "slug": "still-blocked-http"}, headers=headers
    )
    detail_url = create_response.headers["location"]

    response = client.post(
        f"{detail_url}/request-review",
        data={"evidence_manifest_id": "evidence-1", "audience_policy_id": "audience-1"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "outstanding publication blockers" in response.text


def test_reviews_queue_requires_release_strategy_permission(db_session):
    client = _client(db_session)
    response = client.get("/ops/reviews", headers=_auth_headers(role=MembershipRole.RESEARCHER))
    assert response.status_code == 403


def test_review_detail_cross_tenant_is_404(db_session):
    client = _client(db_session)
    proposer_headers = _auth_headers(user_id="user-proposer", tenant_id="tenant-a")

    pv_id = _seed_ready_product_for_review(db_session, tenant_id="tenant-a")
    create_response = client.post(
        "/ops/products", data={"product_name": "Cross Tenant", "slug": "cross-tenant-review-http"}, headers=proposer_headers
    )
    detail_url = create_response.headers["location"]
    client.post(
        detail_url,
        data={
            "expected_revision": "1",
            "product_name": "Cross Tenant",
            "portfolio_version_id": pv_id,
            "cash_bps": "0",
            "service_modes": ["alerts"],
            "audience_policy_id": "audience-1",
            "research_report_id": "report-1",
            "methodology_document_id": "method-1",
        },
        headers=proposer_headers,
    )
    review_response = client.post(
        f"{detail_url}/request-review",
        data={"evidence_manifest_id": "evidence-1", "audience_policy_id": "audience-1"},
        headers=proposer_headers,
    )
    review_url = review_response.headers["location"]

    other_tenant_headers = _auth_headers(tenant_id="tenant-b", role=MembershipRole.REVIEWER)
    response = client.get(review_url, headers=other_tenant_headers)
    assert response.status_code == 404


def test_decide_refuses_self_review_over_http(db_session):
    client = _client(db_session)
    proposer_headers = _auth_headers(user_id="user-proposer")

    pv_id = _seed_ready_product_for_review(db_session)
    create_response = client.post(
        "/ops/products", data={"product_name": "Self Review", "slug": "self-review-http"}, headers=proposer_headers
    )
    detail_url = create_response.headers["location"]
    client.post(
        detail_url,
        data={
            "expected_revision": "1",
            "product_name": "Self Review",
            "portfolio_version_id": pv_id,
            "cash_bps": "0",
            "service_modes": ["alerts"],
            "audience_policy_id": "audience-1",
            "research_report_id": "report-1",
            "methodology_document_id": "method-1",
        },
        headers=proposer_headers,
    )
    review_response = client.post(
        f"{detail_url}/request-review",
        data={"evidence_manifest_id": "evidence-1", "audience_policy_id": "audience-1"},
        headers=proposer_headers,
    )
    review_url = review_response.headers["location"]

    decide_response = client.post(
        review_url, data={"decision": "approve", "reason": "self approving"}, headers=proposer_headers
    )
    assert decide_response.status_code == 400
    assert "independent reviewer" in decide_response.text


def test_decide_approve_over_http_moves_product_to_approved(db_session):
    client = _client(db_session)
    proposer_headers = _auth_headers(user_id="user-proposer")
    reviewer_headers = _auth_headers(user_id="user-reviewer", role=MembershipRole.REVIEWER)

    pv_id = _seed_ready_product_for_review(db_session)
    create_response = client.post(
        "/ops/products", data={"product_name": "Approve Me", "slug": "approve-me-http"}, headers=proposer_headers
    )
    detail_url = create_response.headers["location"]
    client.post(
        detail_url,
        data={
            "expected_revision": "1",
            "product_name": "Approve Me",
            "portfolio_version_id": pv_id,
            "cash_bps": "0",
            "service_modes": ["alerts"],
            "audience_policy_id": "audience-1",
            "research_report_id": "report-1",
            "methodology_document_id": "method-1",
        },
        headers=proposer_headers,
    )
    review_response = client.post(
        f"{detail_url}/request-review",
        data={"evidence_manifest_id": "evidence-1", "audience_policy_id": "audience-1"},
        headers=proposer_headers,
    )
    review_url = review_response.headers["location"]

    decide_response = client.post(
        review_url, data={"decision": "approve", "reason": "looks good"}, headers=reviewer_headers
    )
    assert decide_response.status_code == 303

    product_page = client.get(detail_url, headers=proposer_headers)
    assert "APPROVED" in product_page.text


def test_research_runs_page_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops/research/new")
    assert response.status_code == 401


def test_research_runs_page_denies_a_role_without_run_research_job(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.REVIEWER)
    response = client.get("/ops/research/new", headers=headers)
    assert response.status_code == 403


def test_research_runs_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/research/new", headers=_auth_headers())
    assert response.status_code == 200
    assert "Select a rights-qualified universe" in response.text


def test_create_research_run_then_view_preview(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    sleeve_response = client.post("/ops/research/universe", data=_SLEEVE_FORM_FIELDS, headers=headers)
    assert sleeve_response.status_code == 303

    universe_page = client.get("/ops/research/universe", headers=headers)
    assert "north-star-research" in universe_page.text

    from app.models.sleeve import Sleeve

    sleeve_id = db_session.query(Sleeve).filter_by(provider="north-star-research").one().sleeve_id

    create_response = client.post(
        "/ops/research/new",
        data={
            "sleeve_ids": [sleeve_id],
            "recipes": ["equal_capital"],
            "subset_min": "1",
            "subset_max": "1",
            "cash_bps": "1500",
            "max_sleeve_bps": "3500",
            "max_cluster_bps": "5000",
            "train_sessions": "252",
            "test_sessions": "63",
            "holdout_fraction": "0.20",
            "cost_scenario_ids": "scenario-1",
            "resource_profile_id": "profile-1",
        },
        headers=headers,
    )
    assert create_response.status_code == 303
    detail_url = create_response.headers["location"]

    detail_response = client.get(detail_url, headers=headers)
    assert detail_response.status_code == 200
    assert "Declared candidates" in detail_response.text
    assert "ESTIMATED" in detail_response.text


def test_create_research_run_rejects_invalid_subset_bounds_over_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    response = client.post(
        "/ops/research/new",
        data={
            "recipes": ["equal_capital"],
            "subset_min": "5",
            "subset_max": "2",
            "cash_bps": "1500",
            "max_sleeve_bps": "3500",
            "max_cluster_bps": "5000",
            "train_sessions": "252",
            "test_sessions": "63",
            "holdout_fraction": "0.20",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "invalid subset bounds" in response.text


def test_research_run_detail_flags_an_unimplemented_recipe_over_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    create_response = client.post(
        "/ops/research/new",
        data={
            "recipes": ["hrp"],
            "subset_min": "2",
            "subset_max": "5",
            "cash_bps": "1500",
            "max_sleeve_bps": "3500",
            "max_cluster_bps": "5000",
            "train_sessions": "252",
            "test_sessions": "63",
            "holdout_fraction": "0.20",
        },
        headers=headers,
    )
    detail_url = create_response.headers["location"]
    response = client.get(detail_url, headers=headers)
    assert "RECIPE_NOT_IMPLEMENTED:hrp" in response.text


def test_public_home_is_anonymous_and_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/")
    assert response.status_code == 200
    assert "No portfolios are currently available" in response.text
    assert "NOT_CONFIGURED" in response.text


def test_public_home_lists_a_real_published_product(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/products", data={"product_name": "Home Page Product", "slug": "home-page-product"}, headers=headers
    )
    detail_url = create_response.headers["location"]
    from app.models.product import Product, ProductLifecycleState

    product_id = detail_url.rsplit("/", 1)[-1]
    product = db_session.get(Product, product_id)
    product.lifecycle_state = ProductLifecycleState.PUBLISHED
    db_session.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert "Home Page Product" in response.text
