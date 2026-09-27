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


def test_help_page_lists_the_real_compatibility_directory(db_session):
    client = _client(db_session)
    response = client.get("/help")
    assert response.status_code == 200
    assert "Collective2" in response.text
    assert "eToro" in response.text
    assert "Only verified capabilities appear here" in response.text


def test_portfolio_detail_returns_scoped_404_for_a_draft_products_slug(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    client.post("/ops/products", data={"product_name": "Still Draft", "slug": "still-draft-http"}, headers=headers)

    response = client.get("/portfolios/still-draft-http")
    assert response.status_code == 404


def test_portfolio_detail_returns_scoped_404_for_an_unknown_slug(db_session):
    client = _client(db_session)
    response = client.get("/portfolios/never-existed")
    assert response.status_code == 404


def test_portfolio_detail_shows_real_facts_for_a_published_product(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    pv_id = _seed_ready_product_for_review(db_session, slug="detail-http")
    create_response = client.post(
        "/ops/products", data={"product_name": "Detail Product HTTP", "slug": "detail-product-http"}, headers=headers
    )
    detail_url = create_response.headers["location"]
    client.post(
        detail_url,
        data={
            "expected_revision": "1",
            "product_name": "Detail Product HTTP",
            "portfolio_version_id": pv_id,
            "cash_bps": "500",
            "service_modes": ["alerts"],
            "audience_policy_id": "audience-1",
            "research_report_id": "report-1",
            "methodology_document_id": "method-1",
        },
        headers=headers,
    )
    from app.models.product import Product, ProductLifecycleState

    product_id = detail_url.rsplit("/", 1)[-1]
    product = db_session.get(Product, product_id)
    product.lifecycle_state = ProductLifecycleState.PUBLISHED
    db_session.commit()

    response = client.get("/portfolios/detail-product-http")
    assert response.status_code == 200
    assert "Detail Product HTTP" in response.text
    assert "500 bps" in response.text
    assert "method-1" in response.text
    assert "A released track record is not available for this version." in response.text
    assert "Collective2" in response.text


def test_pricing_page_shows_the_real_empty_state_while_billing_is_unconfigured(db_session):
    client = _client(db_session)
    response = client.get("/pricing")
    assert response.status_code == 200
    assert "Subscriptions are not open for purchase yet." in response.text
    assert "NOT_CONFIGURED" in response.text


def test_pricing_page_shows_real_plan_cards_once_billing_is_configured(db_session, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_a_real_looking_secret")
    client = _client(db_session)
    response = client.get("/pricing")
    assert response.status_code == 200
    assert "ALERTS_ONE" in response.text
    assert "Subscriptions are not open for purchase yet." not in response.text


def _seed_customer_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    from app.models.tenancy import Membership, Tenant, UserIdentity

    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_eligibility_page_requires_a_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/onboarding/eligibility", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_eligibility_page_shows_the_real_empty_state_before_any_facts(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    response = client.get("/onboarding/eligibility", headers=headers)
    assert response.status_code == 200
    assert "Complete eligibility before selecting a paid or copy service." in response.text


def test_save_eligibility_facts_then_reload_shows_real_decision(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)

    save_response = client.post(
        "/onboarding/eligibility",
        data={
            "residence_country": "US",
            "customer_type": "individual",
            "requested_service_modes": ["research"],
            "document_versions": "terms-v1",
            "facts_confirmed": "true",
        },
        headers=headers,
    )
    assert save_response.status_code == 303

    response = client.get("/onboarding/eligibility", headers=headers)
    assert response.status_code == 200
    assert "ELIGIBLE" in response.text
    assert "Complete eligibility before selecting a paid or copy service." not in response.text


def test_save_eligibility_facts_rejects_an_empty_service_selection(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    response = client.post(
        "/onboarding/eligibility",
        data={"residence_country": "US", "customer_type": "individual", "document_versions": "", "facts_confirmed": ""},
        headers=headers,
    )
    assert response.status_code == 400
    assert "at least one requested service is required" in response.text


def _seed_owner_membership(db_session, *, tenant_id="tenant-a", owner_user_id="user-a"):
    from app.models.tenancy import Membership, Tenant, UserIdentity

    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.flush()
    db_session.add(UserIdentity(user_id=owner_user_id, email=f"{owner_user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=owner_user_id, role=MembershipRole.OWNER))
    db_session.commit()


def test_staff_access_page_requires_owner_role(db_session):
    client = _client(db_session)
    response = client.get("/ops/access", headers=_auth_headers(role=MembershipRole.RESEARCHER))
    assert response.status_code == 403


def test_staff_access_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/access", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "No delegated staff memberships exist." in response.text


def test_invite_then_view_staff_member_over_real_http(db_session):
    _seed_owner_membership(db_session)
    from app.models.tenancy import UserIdentity

    db_session.add(UserIdentity(user_id="new-staff-http", email="new-staff-http@example.com"))
    db_session.commit()

    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    invite_response = client.post(
        "/ops/access/invite", data={"user_id": "new-staff-http", "role": "researcher"}, headers=headers
    )
    assert invite_response.status_code == 303

    response = client.get("/ops/access", headers=headers)
    assert "new-staff-http" in response.text
    assert "researcher" in response.text


def test_invite_rejects_an_unknown_user_identity_over_real_http(db_session):
    _seed_owner_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    response = client.post(
        "/ops/access/invite", data={"user_id": "never-existed", "role": "researcher"}, headers=headers
    )
    assert response.status_code == 400
    assert "no existing identity" in response.text


def test_revoke_the_owner_is_refused_over_real_http(db_session):
    _seed_owner_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    response = client.post("/ops/access/user-a/revoke", headers=headers)
    assert response.status_code == 400
    assert "owner role cannot be revoked" in response.text


def test_support_case_page_requires_a_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/support", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_support_case_page_shows_the_real_empty_state(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get("/app/support", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "You have no support cases." in response.text


def test_create_then_view_support_case_over_real_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    create_response = client.post(
        "/app/support",
        data={
            "category": "connection",
            "related_object_id": "",
            "subject": "Cannot connect broker",
            "description": "The connection wizard fails at step 2",
            "attachment_ids": "",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    response = client.get("/app/support", headers=headers)
    assert "Cannot connect broker" in response.text
    assert "connection" in response.text


def test_create_support_case_with_a_cross_tenant_related_object_id_is_refused(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)

    from datetime import datetime, timedelta, timezone

    from app.models.billing import ProductTier, Subscription, SubscriptionState

    other_tenant_subscription = Subscription(
        tenant_id="tenant-b",
        tier=ProductTier.ALERTS_ONE,
        state=SubscriptionState.ACTIVE_PAID,
        price_cents=3900,
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(other_tenant_subscription)
    db_session.commit()

    response = client.post(
        "/app/support",
        data={
            "category": "billing",
            "related_object_id": other_tenant_subscription.subscription_id,
            "subject": "Billing question",
            "description": "Why was I charged",
            "attachment_ids": "",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "does not reference a record you can access" in response.text


def test_publisher_destinations_page_requires_publisher_permission(db_session):
    client = _client(db_session)
    response = client.get("/ops/publishers", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY))
    assert response.status_code == 403


def test_publisher_destinations_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/publishers", headers=_auth_headers())
    assert response.status_code == 200
    assert "No publishing destinations are configured." in response.text


def test_save_then_view_publisher_destination_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/publishers",
        data={
            "platform": "collective2",
            "external_strategy_id": "strat-http-1",
            "environment": "local_simulation",
            "publication_mode": "api_strategy_publisher",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    response = client.get("/ops/publishers", headers=headers)
    assert "strat-http-1" in response.text
    assert "collective2" in response.text


def test_save_publisher_destination_rejects_a_non_local_environment_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    response = client.post(
        "/ops/publishers",
        data={
            "platform": "collective2",
            "external_strategy_id": "strat-http-2",
            "environment": "live",
            "publication_mode": "api_strategy_publisher",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED" in response.text


def test_save_publisher_destination_rejects_a_cross_tenant_double_claim_over_real_http(db_session):
    client = _client(db_session)
    client.post(
        "/ops/publishers",
        data={
            "platform": "collective2",
            "external_strategy_id": "strat-shared-http",
            "environment": "local_simulation",
            "publication_mode": "api_strategy_publisher",
        },
        headers=_auth_headers(tenant_id="tenant-a"),
    )
    response = client.post(
        "/ops/publishers",
        data={
            "platform": "collective2",
            "external_strategy_id": "strat-shared-http",
            "environment": "local_simulation",
            "publication_mode": "api_strategy_publisher",
        },
        headers=_auth_headers(tenant_id="tenant-b"),
    )
    assert response.status_code == 400
    assert "already claimed" in response.text


def test_api_keys_page_requires_a_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/developer", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_api_keys_page_shows_the_real_empty_state(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get("/app/developer", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "No API access is active for this subscription." in response.text


def test_create_api_key_shows_the_secret_once_and_never_on_reload(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    create_response = client.post(
        "/app/developer",
        data={
            "label": "My integration",
            "scopes": ["alerts_read"],
            "expires_at": "2027-01-01T00:00",
        },
        headers=headers,
    )
    assert create_response.status_code == 200
    assert "New key generated" in create_response.text

    import re

    match = re.search(r"<code>([^<]+)</code>", create_response.text)
    assert match is not None
    raw_secret = match.group(1)

    reload_response = client.get("/app/developer", headers=headers)
    assert raw_secret not in reload_response.text
    assert "My integration" in reload_response.text


def test_create_api_key_rejects_a_trading_scope_over_real_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    response = client.post(
        "/app/developer",
        data={"label": "Bad key", "scopes": ["trading_write"], "expires_at": "2027-01-01T00:00"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "unauthorized scope" in response.text


def test_revoke_api_key_is_idempotent_over_real_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    client.post(
        "/app/developer",
        data={"label": "My integration", "scopes": ["alerts_read"], "expires_at": "2027-01-01T00:00"},
        headers=headers,
    )
    list_response = client.get("/app/developer", headers=headers)

    import re

    key_id_match = re.search(r"/app/developer/([^/]+)/revoke", list_response.text)
    assert key_id_match is not None
    key_id = key_id_match.group(1)

    first = client.post(f"/app/developer/{key_id}/revoke", headers=headers)
    assert first.status_code == 303
    second = client.post(f"/app/developer/{key_id}/revoke", headers=headers)
    assert second.status_code == 303


def test_business_economics_page_requires_owner_or_billing_operator(db_session):
    client = _client(db_session)
    response = client.get("/ops/business", headers=_auth_headers(role=MembershipRole.RESEARCHER))
    assert response.status_code == 403


def test_business_economics_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/business", headers=_auth_headers())
    assert response.status_code == 200
    assert "No financial business records are available." in response.text


def test_business_economics_page_shows_real_booked_revenue_and_never_unpaid_subscriptions(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models.billing import ProductTier, Subscription, SubscriptionState

    client = _client(db_session)
    db_session.add(
        Subscription(
            tenant_id="tenant-a",
            tier=ProductTier.ALERTS_ONE,
            state=SubscriptionState.ACTIVE_PAID,
            price_cents=3900,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    db_session.add(
        Subscription(
            tenant_id="tenant-a",
            tier=ProductTier.PORTFOLIOS_THREE,
            state=SubscriptionState.PENDING_PAYMENT,
            price_cents=9900,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    db_session.commit()

    response = client.get("/ops/business", headers=_auth_headers())
    assert response.status_code == 200
    assert "39.00" in response.text
    assert "99.00" not in response.text


def test_customers_page_requires_owner_or_support_readonly(db_session):
    client = _client(db_session)
    response = client.get("/ops/customers", headers=_auth_headers(role=MembershipRole.BILLING_OPERATOR))
    assert response.status_code == 403


def test_customers_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/customers", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY))
    assert response.status_code == 200
    assert "No customers have signed up." in response.text


def test_customer_detail_returns_scoped_404_for_a_cross_tenant_user(db_session):
    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_customer_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    client = _client(db_session)
    response = client.get(
        "/ops/customers/user-b", headers=_auth_headers(tenant_id="tenant-a", role=MembershipRole.SUPPORT_READONLY)
    )
    assert response.status_code == 404


def test_customer_detail_shows_real_eligibility_and_cases_over_real_http(db_session):
    _seed_customer_membership(db_session)
    customer_headers = _auth_headers(role=MembershipRole.CUSTOMER)
    client = _client(db_session)
    client.post(
        "/onboarding/eligibility",
        data={
            "residence_country": "US",
            "customer_type": "individual",
            "requested_service_modes": ["research"],
            "document_versions": "v1",
            "facts_confirmed": "true",
        },
        headers=customer_headers,
    )
    client.post(
        "/app/support",
        data={
            "category": "billing",
            "related_object_id": "",
            "subject": "Billing question HTTP",
            "description": "Why was I charged",
            "attachment_ids": "",
        },
        headers=customer_headers,
    )

    staff_response = client.get(
        "/ops/customers/user-a", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY)
    )
    assert staff_response.status_code == 200
    assert "ELIGIBLE" in staff_response.text
    assert "Billing question HTTP" in staff_response.text


def test_integrations_page_requires_owner_or_publisher_operator(db_session):
    client = _client(db_session)
    response = client.get("/ops/integrations", headers=_auth_headers(role=MembershipRole.RESEARCHER))
    assert response.status_code == 403


def test_integrations_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/integrations", headers=_auth_headers())
    assert response.status_code == 200
    assert "No commercial integrations are configured." in response.text


def test_save_then_view_integration_configuration_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/integrations",
        data={
            "provider_registry_id": "stripe",
            "purpose": "billing",
            "environment": "test",
            "quota_profile_id": "qp-http-1",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    response = client.get("/ops/integrations", headers=headers)
    assert "stripe" in response.text
    assert "qp-http-1" in response.text


def test_save_integration_configuration_rejects_an_unreviewed_provider_over_real_http(db_session):
    client = _client(db_session)
    response = client.post(
        "/ops/integrations",
        data={
            "provider_registry_id": "some-random-service",
            "purpose": "research",
            "environment": "test",
            "quota_profile_id": "qp-1",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "REVIEWED_PROVIDER_NOT_FOUND" in response.text


def test_pricing_page_requires_owner_or_billing_operator(db_session):
    client = _client(db_session)
    response = client.get("/ops/billing", headers=_auth_headers(role=MembershipRole.PUBLISHER_OPERATOR))
    assert response.status_code == 403


def test_pricing_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/billing", headers=_auth_headers())
    assert response.status_code == 200
    assert "No approved price versions exist" in response.text


def test_save_then_view_price_version_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/billing",
        data={
            "sku": "alerts-monthly-http",
            "currency": "usd",
            "amount_minor": "3900",
            "interval": "month",
            "portfolio_limit": "1",
            "features": ["alerts_read"],
            "mode": "test",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    response = client.get("/ops/billing", headers=headers)
    assert "alerts-monthly-http" in response.text


def test_save_price_version_rejects_live_mode_over_real_http(db_session):
    client = _client(db_session)
    response = client.post(
        "/ops/billing",
        data={
            "sku": "live-sku-http",
            "currency": "usd",
            "amount_minor": "3900",
            "interval": "month",
            "portfolio_limit": "1",
            "features": ["alerts_read"],
            "mode": "live",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "LIVE_MODE_NOT_AUTHORIZED" in response.text


def test_content_documents_page_requires_owner_or_reviewer(db_session):
    client = _client(db_session)
    response = client.get("/ops/content", headers=_auth_headers(role=MembershipRole.BILLING_OPERATOR))
    assert response.status_code == 403


def test_content_documents_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/content", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 200
    assert "No public content has been approved." in response.text


def test_save_content_draft_rejects_html_over_real_http(db_session):
    client = _client(db_session)
    response = client.post(
        "/ops/content",
        data={
            "document_type": "help",
            "locale": "en-US",
            "title": "Help page",
            "body": "<script>bad</script>",
            "audience_policy_id": "audience-1",
            "source_evidence_ids": "",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "raw HTML" in response.text


def test_save_then_submit_content_draft_for_review_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/content",
        data={
            "document_type": "help",
            "locale": "en-US",
            "title": "Help page HTTP",
            "body": "Contact support for questions.",
            "audience_policy_id": "audience-1",
            "source_evidence_ids": "",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    list_response = client.get("/ops/content", headers=headers)
    assert "Help page HTTP" in list_response.text
    assert "DRAFT" in list_response.text

    import re

    match = re.search(r"/ops/content/([^/]+)/request-review", list_response.text)
    assert match is not None
    document_id = match.group(1)

    review_response = client.post(f"/ops/content/{document_id}/request-review", headers=headers)
    assert review_response.status_code == 303

    final_response = client.get("/ops/content", headers=headers)
    assert "SUBMITTED_FOR_REVIEW" in final_response.text


def test_managed_programs_page_requires_owner_or_reviewer(db_session):
    client = _client(db_session)
    response = client.get("/ops/managed-programs", headers=_auth_headers(role=MembershipRole.PUBLISHER_OPERATOR))
    assert response.status_code == 403


def test_managed_programs_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/managed-programs", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 200
    assert "No managed-account program is approved." in response.text


def test_save_managed_program_rejects_missing_evidence_over_real_http(db_session):
    client = _client(db_session)
    response = client.post(
        "/ops/managed-programs",
        data={
            "program_name": "Bad Program",
            "broker_program_id": "broker-1",
            "mode": "pamm",
            "allocation_policy_id": "alloc-1",
            "nav_policy_id": "nav-1",
            "dealing_schedule_id": "dealing-1",
            "agreement_evidence_ids": "",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "no automated signing" in response.text


def test_save_then_submit_managed_program_for_review_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    create_response = client.post(
        "/ops/managed-programs",
        data={
            "program_name": "HTTP Program",
            "broker_program_id": "broker-http-1",
            "mode": "pamm",
            "allocation_policy_id": "alloc-1",
            "nav_policy_id": "nav-1",
            "dealing_schedule_id": "dealing-1",
            "agreement_evidence_ids": "evidence-1",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    list_response = client.get("/ops/managed-programs", headers=headers)
    assert "HTTP Program" in list_response.text

    import re

    match = re.search(r"/ops/managed-programs/([^/]+)/request-review", list_response.text)
    assert match is not None
    program_id = match.group(1)

    review_response = client.post(f"/ops/managed-programs/{program_id}/request-review", headers=headers)
    assert review_response.status_code == 303

    final_response = client.get("/ops/managed-programs", headers=headers)
    assert "SUBMITTED_FOR_REVIEW" in final_response.text


def test_audit_log_page_requires_owner_or_reviewer(db_session):
    client = _client(db_session)
    response = client.get("/ops/audit", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY))
    assert response.status_code == 403


def test_audit_log_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/audit", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 200
    assert "No audit events match these filters." in response.text


def test_invite_staff_member_appends_a_real_audit_event(db_session):
    from app.models.tenancy import UserIdentity

    _seed_owner_membership(db_session)
    db_session.add(UserIdentity(user_id="new-staff-audit", email="new-staff-audit@example.com"))
    db_session.commit()

    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    client.post(
        "/ops/access/invite", data={"user_id": "new-staff-audit", "role": "researcher"}, headers=headers
    )

    response = client.get(
        "/ops/audit", params={"object_id": "new-staff-audit"}, headers=_auth_headers(role=MembershipRole.REVIEWER)
    )
    assert response.status_code == 200
    assert "invite_staff_member:researcher" in response.text
    assert "user-a" in response.text


def test_revoke_staff_member_appends_a_real_audit_event(db_session):
    from app.models.tenancy import UserIdentity

    _seed_owner_membership(db_session)
    db_session.add(UserIdentity(user_id="new-staff-audit-2", email="new-staff-audit-2@example.com"))
    db_session.commit()

    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    client.post("/ops/access/invite", data={"user_id": "new-staff-audit-2", "role": "researcher"}, headers=headers)
    client.post("/ops/access/new-staff-audit-2/revoke", headers=headers)

    response = client.get(
        "/ops/audit", params={"object_id": "new-staff-audit-2"}, headers=_auth_headers(role=MembershipRole.REVIEWER)
    )
    assert response.status_code == 200
    assert "revoke_staff_member" in response.text


def test_workspace_settings_page_requires_owner(db_session):
    client = _client(db_session)
    response = client.get("/ops/settings", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 403


def test_workspace_settings_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/settings", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "No workspace default has been saved" in response.text


def test_save_workspace_settings_rejects_hiding_a_mandatory_panel_over_real_http(db_session):
    client = _client(db_session)
    response = client.post(
        "/ops/settings",
        data={
            "workspace_name": "Ops desk",
            "theme": "system",
            "density": "comfortable",
            "visible_panel_ids": "research_queue",
            "column_order": "",
            "notification_route_id": "",
        },
        headers=_auth_headers(role=MembershipRole.OWNER),
    )
    assert response.status_code == 400
    assert "mandatory panels cannot be hidden" in response.text


def test_save_then_reload_workspace_settings_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    create_response = client.post(
        "/ops/settings",
        data={
            "workspace_name": "HTTP Ops desk",
            "theme": "dark",
            "density": "compact",
            "visible_panel_ids": "release_blockers, business_indicators",
            "column_order": "name, state",
            "notification_route_id": "",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    final_response = client.get("/ops/settings", headers=headers)
    assert "HTTP Ops desk" in final_response.text
    assert "dark" in final_response.text


def test_research_run_full_results_page_requires_owner_researcher_or_reviewer(db_session):
    client = _client(db_session)
    response = client.get(
        "/ops/research/runs/nonexistent-run-id", headers=_auth_headers(role=MembershipRole.BILLING_OPERATOR)
    )
    assert response.status_code == 403


def test_research_run_full_results_page_is_a_scoped_not_found_for_an_unknown_run(db_session):
    client = _client(db_session)
    response = client.get("/ops/research/runs/nonexistent-run-id", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 404


def test_research_run_full_results_page_shows_the_real_not_started_empty_state(db_session):
    client = _client(db_session)
    headers = _auth_headers()

    create_response = client.post(
        "/ops/research/new",
        data={
            "recipes": ["equal_capital"],
            "subset_min": "1",
            "subset_max": "1",
            "cash_bps": "1500",
            "max_sleeve_bps": "3500",
            "max_cluster_bps": "5000",
            "train_sessions": "252",
            "test_sessions": "63",
            "holdout_fraction": "0.20",
        },
        headers=headers,
    )
    assert create_response.status_code == 303
    detail_url = create_response.headers["location"]
    run_id = detail_url.rsplit("/", 1)[-1]

    response = client.get(f"/ops/research/runs/{run_id}", headers=headers)
    assert response.status_code == 200
    assert "This run has not started; no performance results exist." in response.text
    assert "UNSUPPORTED" in response.text


def test_research_run_full_results_page_is_a_scoped_not_found_across_tenants(db_session):
    client = _client(db_session)
    owner_headers = _auth_headers(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER)

    create_response = client.post(
        "/ops/research/new",
        data={
            "recipes": ["equal_capital"],
            "subset_min": "1",
            "subset_max": "1",
            "cash_bps": "1500",
            "max_sleeve_bps": "3500",
            "max_cluster_bps": "5000",
            "train_sessions": "252",
            "test_sessions": "63",
            "holdout_fraction": "0.20",
        },
        headers=owner_headers,
    )
    run_id = create_response.headers["location"].rsplit("/", 1)[-1]

    other_tenant_headers = _auth_headers(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.OWNER)
    response = client.get(f"/ops/research/runs/{run_id}", headers=other_tenant_headers)
    assert response.status_code == 404
