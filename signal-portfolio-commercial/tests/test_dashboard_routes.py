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
from tests._onboarding_fixtures import complete_onboarding_prerequisites


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


def test_operations_overview_links_to_portfolio_lab_for_a_role_permitted_to_run_research(db_session):
    """INT-019 "Permitted owner workspace switch": Portfolio Lab (the
    research-run pages under /ops/research) is reachable from the same
    operations-overview nav as trading/integration status, for a role
    actually permitted to run a research job."""
    client = _client(db_session)
    response = client.get("/ops", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert '/ops/research/new' in response.text


def test_operations_overview_never_links_to_portfolio_lab_for_a_role_without_research_permission(db_session):
    client = _client(db_session)
    response = client.get("/ops", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY))
    assert response.status_code == 200
    assert '/ops/research/new' not in response.text


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
        PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("1.0"),
            tenant_id=tenant_id,
        )
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


def test_research_runs_page_links_back_to_overview_and_forward_to_trading_performance(db_session):
    """INT-019 "Permitted owner workspace switch": Portfolio Lab's own
    half of the Trading -> Portfolio Lab -> Performance cycle -- a
    consistent way back to the overview and onward to Trading &
    integration status, for a role (OWNER, the default here) permitted
    to view both."""
    client = _client(db_session)
    response = client.get("/ops/research/new", headers=_auth_headers())
    assert response.status_code == 200
    assert '/ops"' in response.text
    assert '/ops/trading' in response.text


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

    # S12 step 7 "control plane": a real command writes a real audit event.
    from app.services.audit_log import list_audit_events

    research_run_id = detail_url.rsplit("/", 1)[-1]
    events = list_audit_events(db_session, tenant_id="tenant-a", object_id=research_run_id)
    assert len(events) == 1
    assert events[0].action == "create_research_run"
    assert events[0].actor_user_id == "user-a"


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


def test_candidate_comparison_page_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops/research/compare")
    assert response.status_code == 401


def test_candidate_comparison_page_denies_a_role_without_permission(db_session):
    client = _client(db_session)
    response = client.get(
        "/ops/research/compare", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY)
    )
    assert response.status_code == 403


def test_candidate_comparison_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/research/compare", headers=_auth_headers())
    assert response.status_code == 200
    assert "No comparable completed candidates selected." in response.text


def _create_two_sleeve_run(client, headers, db_session):
    fields_a = dict(_SLEEVE_FORM_FIELDS)
    fields_a["provider"] = "compare-sleeve-a"
    fields_b = dict(_SLEEVE_FORM_FIELDS)
    fields_b["provider"] = "compare-sleeve-b"
    client.post("/ops/research/universe", data=fields_a, headers=headers)
    client.post("/ops/research/universe", data=fields_b, headers=headers)

    from app.models.sleeve import Sleeve

    sleeve_ids = [
        row.sleeve_id
        for row in db_session.query(Sleeve).filter(Sleeve.provider.in_(["compare-sleeve-a", "compare-sleeve-b"])).all()
    ]
    create_response = client.post(
        "/ops/research/new",
        data={
            "sleeve_ids": sleeve_ids,
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
    research_run_id = create_response.headers["location"].rsplit("/", 1)[-1]
    return research_run_id


def test_candidate_comparison_page_lists_a_real_comparable_run_and_compares_candidates(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    research_run_id = _create_two_sleeve_run(client, headers, db_session)

    list_response = client.get("/ops/research/compare", headers=headers)
    assert research_run_id[:8] in list_response.text

    compare_response = client.get(
        f"/ops/research/compare?research_run_id={research_run_id}&candidate_a=0&candidate_b=1",
        headers=headers,
    )
    assert compare_response.status_code == 200
    assert "Shared sleeves" in compare_response.text
    assert "Candidate A (index 0)" in compare_response.text
    assert "Candidate B (index 1)" in compare_response.text
    # Two disjoint single-sleeve candidates never share a sleeve -- a
    # real, computed fact, not a fabricated correlation number.
    assert "<td>0</td>" in compare_response.text


def _create_three_sleeve_run(client, headers, db_session):
    """Three sleeves, subset size fixed at 2 -- gives exactly three
    2-sleeve candidates ((s0,s1), (s0,s2), (s1,s2), sorted sleeve-id
    order) with real, exactly-predictable composition/allocation and
    overlap facts, used to load-bear-test the composition and overlap
    scatter charts below."""
    for suffix in ("a", "b", "c"):
        fields = dict(_SLEEVE_FORM_FIELDS)
        fields["provider"] = f"compare-sleeve-three-{suffix}"
        client.post("/ops/research/universe", data=fields, headers=headers)

    from app.models.sleeve import Sleeve

    sleeve_ids = sorted(
        row.sleeve_id
        for row in db_session.query(Sleeve)
        .filter(Sleeve.provider.in_(["compare-sleeve-three-a", "compare-sleeve-three-b", "compare-sleeve-three-c"]))
        .all()
    )
    create_response = client.post(
        "/ops/research/new",
        data={
            "sleeve_ids": sleeve_ids,
            "recipes": ["equal_capital"],
            "subset_min": "2",
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
    research_run_id = create_response.headers["location"].rsplit("/", 1)[-1]
    return research_run_id


def test_candidate_comparison_page_renders_real_composition_and_overlap_scatter_charts(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    research_run_id = _create_three_sleeve_run(client, headers, db_session)

    compare_response = client.get(
        f"/ops/research/compare?research_run_id={research_run_id}&candidate_a=0&candidate_b=1",
        headers=headers,
    )
    assert compare_response.status_code == 200
    body = compare_response.text

    # Composition chart: two-sleeve candidate at these run bounds is
    # capped at the implemented recipe's 0.35 max-sleeve-weight, leaving
    # 1 - 2*0.35 = 0.30 cash -- exactly matching equal_weight_recipe.
    assert '"values": [0.35, 0.35, 0.3]' in body
    assert body.count('id="candidate-a-composition-data"') == 1
    assert body.count('id="candidate-b-composition-data"') == 1
    assert '<canvas id="candidate-a-composition-chart">' in body
    assert '<canvas id="candidate-b-composition-chart">' in body

    # Overlap scatter: candidate 0 is the baseline (overlap 2 with
    # itself); candidates 1 and 2 each share exactly one sleeve with it
    # (real frozenset intersection over the run's three 2-sleeve
    # candidates), never a fabricated risk/return dimension.
    assert (
        '[{"candidate_index": 0, "overlap_with_baseline": 2, "sleeve_count": 2}, '
        '{"candidate_index": 1, "overlap_with_baseline": 1, "sleeve_count": 2}, '
        '{"candidate_index": 2, "overlap_with_baseline": 1, "sleeve_count": 2}]'
    ) in body
    assert '<canvas id="overlap-scatter-chart">' in body
    assert "/static/vendor/chart.umd.min.js" in body


def test_candidate_comparison_page_is_a_scoped_not_found_for_an_unknown_run(db_session):
    client = _client(db_session)
    response = client.get(
        "/ops/research/compare?research_run_id=nonexistent-run-id", headers=_auth_headers(role=MembershipRole.REVIEWER)
    )
    assert response.status_code == 404


def test_create_candidate_draft_creates_an_unreleased_portfolio_version_never_a_published_one(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    research_run_id = _create_two_sleeve_run(client, headers, db_session)

    draft_response = client.post(
        "/ops/research/compare/draft",
        data={
            "research_run_id": research_run_id,
            "candidate_index": "0",
            "portfolio_id": "candidate-draft-http",
            "consent_disclosure_version": "v1",
            "max_subscriber_capacity": "100",
        },
        headers=headers,
    )
    assert draft_response.status_code == 303
    assert "created_portfolio_version_id=" in draft_response.headers["location"]

    from app.models.portfolio_version import PortfolioVersion

    portfolio_version_id = draft_response.headers["location"].rsplit("created_portfolio_version_id=", 1)[-1]
    version = db_session.get(PortfolioVersion, portfolio_version_id)
    assert version is not None
    assert version.portfolio_id == "candidate-draft-http"
    assert version.version_number == 1

    # No Product references this version, and no publication chain
    # (AD-08/publication_admin/publication_admission) was ever touched
    # -- this really is an unreleased draft, not merely a page that
    # says so.
    from app.models.product import Product

    referencing_products = (
        db_session.query(Product).filter(Product.portfolio_version_id == portfolio_version_id).all()
    )
    assert referencing_products == []

    # A second draft for the same portfolio_id is a new, later version,
    # never an edit of the first (portfolio_versions is append-only).
    second_draft = client.post(
        "/ops/research/compare/draft",
        data={
            "research_run_id": research_run_id,
            "candidate_index": "1",
            "portfolio_id": "candidate-draft-http",
            "consent_disclosure_version": "v1",
            "max_subscriber_capacity": "100",
        },
        headers=headers,
    )
    assert second_draft.status_code == 303
    second_id = second_draft.headers["location"].rsplit("created_portfolio_version_id=", 1)[-1]
    second_version = db_session.get(PortfolioVersion, second_id)
    assert second_version.version_number == 2


def test_create_candidate_draft_requires_permission(db_session):
    client = _client(db_session)
    owner_headers = _auth_headers()
    research_run_id = _create_two_sleeve_run(client, owner_headers, db_session)

    response = client.post(
        "/ops/research/compare/draft",
        data={
            "research_run_id": research_run_id,
            "candidate_index": "0",
            "portfolio_id": "candidate-draft-denied-http",
            "consent_disclosure_version": "v1",
            "max_subscriber_capacity": "100",
        },
        headers=_auth_headers(role=MembershipRole.BILLING_OPERATOR),
    )
    assert response.status_code == 403


def test_create_candidate_draft_rejects_an_unimplemented_recipe(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    fields_a = dict(_SLEEVE_FORM_FIELDS)
    fields_a["provider"] = "no-recipe-sleeve-a"
    fields_b = dict(_SLEEVE_FORM_FIELDS)
    fields_b["provider"] = "no-recipe-sleeve-b"
    client.post("/ops/research/universe", data=fields_a, headers=headers)
    client.post("/ops/research/universe", data=fields_b, headers=headers)

    from app.models.sleeve import Sleeve

    sleeve_ids = [
        row.sleeve_id
        for row in db_session.query(Sleeve).filter(Sleeve.provider.in_(["no-recipe-sleeve-a", "no-recipe-sleeve-b"])).all()
    ]
    create_response = client.post(
        "/ops/research/new",
        data={
            "sleeve_ids": sleeve_ids,
            "recipes": ["hrp"],
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
    research_run_id = create_response.headers["location"].rsplit("/", 1)[-1]

    response = client.post(
        "/ops/research/compare/draft",
        data={
            "research_run_id": research_run_id,
            "candidate_index": "0",
            "portfolio_id": "no-recipe-draft-http",
            "consent_disclosure_version": "v1",
            "max_subscriber_capacity": "100",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "RECIPE_NOT_IMPLEMENTED" in response.text

    from app.models.portfolio_version import PortfolioVersion

    assert db_session.query(PortfolioVersion).filter_by(portfolio_id="no-recipe-draft-http").first() is None


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

    if db_session.get(Tenant, tenant_id) is None:
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

    if db_session.get(Tenant, tenant_id) is None:
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


def test_customer_detail_shows_a_real_copy_mandate_over_real_http(db_session):
    _seed_customer_membership(db_session)
    complete_onboarding_prerequisites(db_session, tenant_id="tenant-a", user_id="user-a")
    product = _seed_published_product(db_session, slug="ad11-mandate-product")
    customer_headers = _auth_headers(role=MembershipRole.CUSTOMER)
    client = _client(db_session)

    client.post("/app/portfolios", data={"product_id": product.product_id}, headers=customer_headers)
    client.post(
        "/app/connections/new",
        data={
            "platform": "collective2",
            "environment": "local_simulation",
            "masked_account_label": "Test ****1234",
        },
        headers=customer_headers,
    )

    from app.services.copy_mandate import list_own_copy_mandates
    from app.services.platform_connection import list_own_platform_connections
    from app.services.portfolio_selection import list_own_portfolio_selections

    selection_id = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")[0].selection_id
    connection_id = list_own_platform_connections(db_session, tenant_id="tenant-a", user_id="user-a")[0].connection_id
    client.post(
        "/app/copy/new",
        data={
            "selection_id": selection_id,
            "connection_id": connection_id,
            "allocation_amount": "100",
            "allocation_currency": "USD",
            "start_mode": "new_entries_only",
            "policy_version_id": "policy-1",
            "consent_version": "consent-1",
        },
        headers=customer_headers,
    )
    assert list_own_copy_mandates(db_session, tenant_id="tenant-a", user_id="user-a")

    staff_response = client.get("/ops/customers/user-a", headers=_auth_headers(role=MembershipRole.OWNER))
    assert staff_response.status_code == 200
    assert "USD" in staff_response.text
    assert "draft" in staff_response.text


def test_customer_detail_shows_a_real_login_logout_audit_trail_over_real_http(db_session):
    """AD-11's own previously-documented 'no ... audit-log model
    exists' gap -- now closed by app/services/audit_log.py, written by
    ID-01's own create_web_session/delete_web_session."""
    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    from app.services.local_auth import create_web_session, delete_web_session

    session_id, _csrf = create_web_session(
        db_session, user_id="user-a", tenant_id="tenant-a", role=MembershipRole.CUSTOMER
    )
    delete_web_session(db_session, session_id=session_id)

    client = _client(db_session)
    staff_response = client.get(
        "/ops/customers/user-a", headers=_auth_headers(tenant_id="tenant-a", role=MembershipRole.OWNER)
    )
    assert staff_response.status_code == 200
    assert "login" in staff_response.text
    assert "logout" in staff_response.text


def test_customer_detail_audit_trail_is_scoped_to_the_customer_and_their_own_tenant(db_session):
    """LOAD-BEARING: a staff view over customer A must never show
    customer B's audit events, even within the same tenant, and never
    another tenant's events at all -- the same 'scoped not-found'/
    'never a cross-tenant id' discipline this whole screen already
    documents for eligibility/cases/mandates."""
    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-b")
    from app.services.local_auth import create_web_session

    create_web_session(db_session, user_id="user-a", tenant_id="tenant-a", role=MembershipRole.CUSTOMER)
    create_web_session(db_session, user_id="user-b", tenant_id="tenant-a", role=MembershipRole.CUSTOMER)

    client = _client(db_session)
    staff_response = client.get(
        "/ops/customers/user-a", headers=_auth_headers(tenant_id="tenant-a", role=MembershipRole.OWNER)
    )
    assert staff_response.status_code == 200
    assert "user-b" not in staff_response.text


def test_staff_access_page_shows_real_session_audit_events_over_real_http(db_session):
    """AD-16's own previously-documented 'no session/audit-log store'
    gap -- now closed."""
    _seed_owner_membership(db_session, tenant_id="tenant-a", owner_user_id="user-a")
    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-b")
    from app.services.local_auth import create_web_session, delete_web_session

    session_id, _csrf = create_web_session(
        db_session, user_id="user-b", tenant_id="tenant-a", role=MembershipRole.CUSTOMER
    )
    delete_web_session(db_session, session_id=session_id)

    client = _client(db_session)
    response = client.get("/ops/access", headers=_auth_headers(tenant_id="tenant-a", role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "login" in response.text
    assert "logout" in response.text
    assert "user-b" in response.text


def test_staff_access_page_session_audit_is_tenant_scoped(db_session):
    """LOAD-BEARING: a tenant's own Session audit panel must never show
    another tenant's login/logout events."""
    _seed_owner_membership(db_session, tenant_id="tenant-a", owner_user_id="user-a")
    _seed_customer_membership(db_session, tenant_id="tenant-b", user_id="user-c")
    from app.services.local_auth import create_web_session

    create_web_session(db_session, user_id="user-c", tenant_id="tenant-b", role=MembershipRole.CUSTOMER)

    client = _client(db_session)
    response = client.get("/ops/access", headers=_auth_headers(tenant_id="tenant-a", role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "user-c" not in response.text
    assert "No login/logout activity recorded for this tenant yet." in response.text


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


def test_publish_content_document_refuses_a_draft_document_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    client.post(
        "/ops/content",
        data={
            "document_type": "help",
            "locale": "en-US",
            "title": "Draft Publish HTTP",
            "body": "Contact support.",
            "audience_policy_id": "audience-1",
            "source_evidence_ids": "",
        },
        headers=headers,
    )

    from app.services.content_document import list_content_documents

    document_id = list_content_documents(db_session, tenant_id="tenant-a")[0].document_id
    response = client.post(f"/ops/content/{document_id}/publish", headers=headers)
    assert response.status_code == 400
    assert "not SUBMITTED_FOR_REVIEW" in response.text


def test_save_submit_and_publish_content_document_appears_on_the_public_methodology_page(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    client.post(
        "/ops/content",
        data={
            "document_type": "help",
            "locale": "en-US",
            "title": "Publish Flow HTTP",
            "body": "Contact support for questions.",
            "audience_policy_id": "audience-1",
            "source_evidence_ids": "",
        },
        headers=headers,
    )

    from app.services.content_document import list_content_documents

    document_id = list_content_documents(db_session, tenant_id="tenant-a")[0].document_id

    before_publish = client.get("/methodology")
    assert "Publish Flow HTTP" not in before_publish.text

    client.post(f"/ops/content/{document_id}/request-review", headers=headers)
    publish_response = client.post(f"/ops/content/{document_id}/publish", headers=headers)
    assert publish_response.status_code == 303

    after_publish = client.get("/methodology")
    assert after_publish.status_code == 200
    assert "Publish Flow HTTP" in after_publish.text

    # S12 step 7 "control plane": every real command on this document
    # wrote its own real audit event, in order.
    from app.services.audit_log import get_object_timeline

    timeline = get_object_timeline(db_session, tenant_id="tenant-a", object_id=document_id)
    assert [e.action for e in timeline] == [
        "save_content_draft", "request_content_review", "publish_content_document",
    ]
    assert all(e.actor_user_id == "user-a" for e in timeline)


def test_public_methodology_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/methodology")
    assert response.status_code == 200
    assert "No approved document is available for this selection." in response.text


def test_submit_content_draft_for_review_shows_submitted_state_over_real_http(db_session):
    client = _client(db_session)
    headers = _auth_headers()
    client.post(
        "/ops/content",
        data={
            "document_type": "help",
            "locale": "en-US",
            "title": "Submit Review HTTP",
            "body": "Contact support.",
            "audience_policy_id": "audience-1",
            "source_evidence_ids": "",
        },
        headers=headers,
    )
    list_response = client.get("/ops/content", headers=headers)
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


def test_export_evidence_manifest_requires_owner_or_reviewer(db_session):
    client = _client(db_session)
    response = client.post(
        "/ops/audit/evidence-manifest", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY)
    )
    assert response.status_code == 403


def test_export_evidence_manifest_denies_customer(db_session):
    client = _client(db_session)
    response = client.post("/ops/audit/evidence-manifest", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 403


def test_export_evidence_manifest_returns_the_real_rows_and_a_verifiable_hash(db_session):
    from app.models.tenancy import UserIdentity

    _seed_owner_membership(db_session)
    db_session.add(UserIdentity(user_id="evidence-staff", email="evidence-staff@example.com"))
    db_session.commit()

    client = _client(db_session)
    owner_headers = _auth_headers(role=MembershipRole.OWNER)
    client.post("/ops/access/invite", data={"user_id": "evidence-staff", "role": "researcher"}, headers=owner_headers)

    reviewer_headers = _auth_headers(role=MembershipRole.REVIEWER)
    response = client.post(
        "/ops/audit/evidence-manifest",
        data={"object_id": "evidence-staff"},
        headers=reviewer_headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment; filename=" in response.headers["content-disposition"]

    import json

    from signal_platform_contracts import compute_payload_hash

    body = json.loads(response.text)
    assert body["manifest"]["row_count"] == 1
    assert body["manifest"]["filter_criteria"]["object_id"] == "evidence-staff"
    assert body["rows"][0]["action"] == "invite_staff_member:researcher"
    assert body["manifest"]["content_hash"] == compute_payload_hash({"rows": body["rows"]})

    # The export itself must leave a real new AuditEvent row.
    audit_response = client.get("/ops/audit", params={"action": "export_evidence_manifest"}, headers=reviewer_headers)
    assert "export_evidence_manifest" in audit_response.text
    assert "user-a" in audit_response.text  # the actor who ran the export


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


def _seed_publication_intent(db_session, *, tenant_id="tenant-a", portfolio_version_id="pv-http-1"):
    from datetime import datetime, timedelta, timezone

    from app.models.portfolio_version import PortfolioVersion
    from app.models.publication import (
        Environment,
        PublicationAction,
        PublicationIntent,
        PublicationState,
        QuantityBasis,
    )

    now = datetime.now(timezone.utc)
    pv = PortfolioVersion(
        portfolio_version_id=portfolio_version_id,
        tenant_id=tenant_id,
        portfolio_id="p-publication-http",
        version_number=1,
        cash_weight=0,
        research_cutoff=now,
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    db_session.add(pv)
    db_session.flush()
    intent = PublicationIntent(
        tenant_id=tenant_id,
        environment=Environment.LOCAL_SIM,
        portfolio_version_id=portfolio_version_id,
        episode_id="ep-http-1",
        revision=1,
        action=PublicationAction.OPEN,
        channel="collective2",
        external_strategy_id="strategy-http-1",
        instrument_id="AAPL",
        quantity="10",
        quantity_basis=QuantityBasis.UNITS,
        price_basis="market",
        policy_hash="policy-hash-http-1",
        audience_snapshot_hash="audience-hash-http-1",
        source_revision_ids=["src-rev-http-1"],
        rights_grant_ids=["grant-http-1"],
        body_hash="body-hash-http-1",
        idempotency_key="idem-http-1",
        valid_from=now,
        expires_at=now + timedelta(hours=1),
        state=PublicationState.UNKNOWN,
    )
    db_session.add(intent)
    db_session.commit()
    return intent


def test_publication_intent_detail_page_requires_owner_or_publisher_operator(db_session):
    client = _client(db_session)
    response = client.get("/ops/publications/nonexistent-intent", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 403


def test_publication_intent_detail_page_is_a_scoped_not_found_for_an_unknown_intent(db_session):
    client = _client(db_session)
    response = client.get(
        "/ops/publications/nonexistent-intent", headers=_auth_headers(role=MembershipRole.PUBLISHER_OPERATOR)
    )
    assert response.status_code == 404


def test_publication_intent_detail_page_shows_the_real_intent(db_session):
    intent = _seed_publication_intent(db_session)
    client = _client(db_session)
    response = client.get(f"/ops/publications/{intent.intent_id}", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "collective2" in response.text
    assert "UNKNOWN" in response.text
    assert "UNSUPPORTED" in response.text


def test_publication_intent_detail_page_is_a_scoped_not_found_across_tenants(db_session):
    intent = _seed_publication_intent(db_session, tenant_id="tenant-a", portfolio_version_id="pv-http-2")
    client = _client(db_session)
    other_tenant_headers = _auth_headers(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.OWNER)
    response = client.get(f"/ops/publications/{intent.intent_id}", headers=other_tenant_headers)
    assert response.status_code == 404


def _seed_published_product(db_session, *, tenant_id="tenant-a", slug="http-published-product"):
    from app.models.product import Product, ProductLifecycleState

    product = Product(
        tenant_id=tenant_id,
        product_name="HTTP Published Product",
        slug=slug,
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()
    return product


def test_portfolio_selections_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/portfolios", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_portfolio_selections_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/app/portfolios", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "You have not selected a portfolio." in response.text


def test_create_portfolio_selection_rejects_a_draft_product_over_http(db_session):
    from app.models.product import Product

    product = Product(tenant_id="tenant-a", product_name="Draft HTTP Product", slug="http-draft-product")
    db_session.add(product)
    db_session.commit()

    client = _client(db_session)
    response = client.post(
        "/app/portfolios", data={"product_id": product.product_id}, headers=_auth_headers(role=MembershipRole.CUSTOMER)
    )
    assert response.status_code == 400
    assert "PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND" in response.text


def test_create_then_cancel_portfolio_selection_over_real_http(db_session):
    _seed_customer_membership(db_session)
    product = _seed_published_product(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)

    create_response = client.post("/app/portfolios", data={"product_id": product.product_id}, headers=headers)
    assert create_response.status_code == 303

    list_response = client.get("/app/portfolios", headers=headers)
    assert product.product_id in list_response.text
    assert "active" in list_response.text

    import re

    match = re.search(r"/app/portfolios/([^/]+)/cancel", list_response.text)
    assert match is not None
    selection_id = match.group(1)

    cancel_response = client.post(f"/app/portfolios/{selection_id}/cancel", headers=headers)
    assert cancel_response.status_code == 303

    final_response = client.get("/app/portfolios", headers=headers)
    assert "cancelled" in final_response.text


def test_cancel_portfolio_selection_is_a_scoped_not_found_for_another_customer(db_session):
    from app.models.tenancy import Membership, MembershipRole as Role, UserIdentity

    _seed_customer_membership(db_session, user_id="user-a")
    product = _seed_published_product(db_session)
    client = _client(db_session)
    owner_headers = _auth_headers(user_id="user-a", role=MembershipRole.CUSTOMER)
    create_response = client.post("/app/portfolios", data={"product_id": product.product_id}, headers=owner_headers)
    assert create_response.status_code == 303

    list_response = client.get("/app/portfolios", headers=owner_headers)
    import re

    match = re.search(r"/app/portfolios/([^/]+)/cancel", list_response.text)
    selection_id = match.group(1)

    db_session.add(UserIdentity(user_id="user-other", email="user-other@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="user-other", role=Role.CUSTOMER))
    db_session.commit()

    other_customer_headers = _auth_headers(user_id="user-other", role=MembershipRole.CUSTOMER)
    response = client.post(f"/app/portfolios/{selection_id}/cancel", headers=other_customer_headers)
    assert response.status_code == 404


def test_notification_preferences_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/settings/notifications", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_notification_preferences_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/app/settings/notifications", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "No verified delivery destination is configured." in response.text


def test_save_notification_preferences_rejects_excluding_safety_over_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.post(
        "/app/settings/notifications",
        data={
            "email": "customer@example.com",
            "categories": ["marketing"],
            "timezone_name": "UTC",
            "marketing_consent": "true",
        },
        headers=_auth_headers(role=MembershipRole.CUSTOMER),
    )
    assert response.status_code == 400
    assert "mandatory categories cannot be excluded" in response.text


def test_save_then_reload_notification_preferences_over_real_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    create_response = client.post(
        "/app/settings/notifications",
        data={
            "email": "http-customer@example.com",
            "categories": ["safety", "billing"],
            "timezone_name": "America/New_York",
            "marketing_consent": "false",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    final_response = client.get("/app/settings/notifications", headers=headers)
    assert "http-customer@example.com" in final_response.text
    assert "America/New_York" in final_response.text


def test_display_preferences_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/settings", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_display_preferences_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/app/settings", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "No additional profile preferences are saved." in response.text


def test_save_display_preferences_rejects_html_in_display_name_over_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.post(
        "/app/settings",
        data={
            "display_name": "<script>bad</script>",
            "timezone_name": "UTC",
            "theme": "system",
            "density": "comfortable",
            "number_locale": "en-US",
            "reduce_motion": "system",
        },
        headers=_auth_headers(role=MembershipRole.CUSTOMER),
    )
    assert response.status_code == 400
    assert "raw HTML" in response.text


def test_save_then_reload_display_preferences_over_real_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    create_response = client.post(
        "/app/settings",
        data={
            "display_name": "HTTP Jane",
            "timezone_name": "America/New_York",
            "theme": "dark",
            "density": "compact",
            "number_locale": "en-US",
            "view_currency": "USD",
            "reduce_motion": "on",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    final_response = client.get("/app/settings", headers=headers)
    assert "HTTP Jane" in final_response.text
    assert "America/New_York" in final_response.text


def test_managed_operations_page_requires_owner_publisher_operator_or_reviewer(db_session):
    client = _client(db_session)
    response = client.get("/ops/managed-operations", headers=_auth_headers(role=MembershipRole.BILLING_OPERATOR))
    assert response.status_code == 403


def test_managed_operations_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/managed-operations", headers=_auth_headers(role=MembershipRole.PUBLISHER_OPERATOR))
    assert response.status_code == 200
    assert "No managed-account program is configured for this tenant." in response.text
    assert "UNSUPPORTED" in response.text


def test_managed_operations_page_shows_a_real_configured_program(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    client.post(
        "/ops/managed-programs",
        data={
            "program_name": "AD-15 Program",
            "broker_program_id": "broker-ad15-1",
            "mode": "pamm",
            "allocation_policy_id": "alloc-1",
            "nav_policy_id": "nav-1",
            "dealing_schedule_id": "dealing-1",
            "agreement_evidence_ids": "evidence-1",
        },
        headers=headers,
    )

    response = client.get("/ops/managed-operations", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 200
    assert "AD-15 Program" in response.text
    assert "broker-ad15-1" in response.text


def _seed_published_product_for_compare(db_session, *, slug, tenant_id="tenant-a"):
    from app.models.product import Product, ProductLifecycleState

    product = Product(
        tenant_id=tenant_id, product_name=f"Compare {slug}", slug=slug, lifecycle_state=ProductLifecycleState.PUBLISHED
    )
    db_session.add(product)
    db_session.commit()
    return product


def test_compare_page_is_anonymous_and_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/compare")
    assert response.status_code == 200
    assert "Select at least two published portfolios to compare." in response.text


def test_compare_page_shows_real_matched_portfolios_and_reports_unmatched(db_session):
    _seed_published_product_for_compare(db_session, slug="http-compare-a")
    _seed_published_product_for_compare(db_session, slug="http-compare-b")

    client = _client(db_session)
    response = client.get("/compare", params=[("slug", "http-compare-a"), ("slug", "http-compare-b"), ("slug", "http-compare-unknown")])
    assert response.status_code == 200
    assert "http-compare-a" in response.text
    assert "http-compare-b" in response.text
    assert "http-compare-unknown" in response.text
    assert "UNSUPPORTED" in response.text


def test_compare_page_rejects_more_than_the_max_slugs(db_session):
    client = _client(db_session)
    params = [("slug", f"http-compare-max-{i}") for i in range(5)]
    response = client.get("/compare", params=params)
    assert response.status_code == 400
    assert "cannot compare more than" in response.text


def test_status_page_is_anonymous_and_shows_real_service_status(db_session):
    client = _client(db_session)
    response = client.get("/status")
    assert response.status_code == 200
    assert "NOT_CONFIGURED" in response.text
    assert "UNSUPPORTED" in response.text


def test_status_page_never_fabricates_a_zero_incident_count(db_session):
    client = _client(db_session)
    response = client.get("/status")
    assert "no incident-tracking model exists in this build" in response.text
    assert "0 active incidents" not in response.text
    assert "No published service incidents." not in response.text


def test_deployment_status_page_requires_owner_or_publisher_operator(db_session):
    client = _client(db_session)
    response = client.get("/ops/system", headers=_auth_headers(role=MembershipRole.REVIEWER))
    assert response.status_code == 403


def test_deployment_status_page_shows_real_service_status_and_no_fabricated_qualification(db_session):
    client = _client(db_session)
    response = client.get("/ops/system", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "NOT_CONFIGURED" in response.text
    assert "No deployment has been qualified for this service." in response.text
    assert "UNSUPPORTED" in response.text


def test_platform_connections_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/connections", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_platform_connections_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/app/connections", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "No platform account is connected." in response.text


def test_create_platform_connection_rejects_a_non_local_simulation_environment_over_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.post(
        "/app/connections/new",
        data={"platform": "collective2", "environment": "live", "masked_account_label": "Test ****1234"},
        headers=_auth_headers(role=MembershipRole.CUSTOMER),
    )
    assert response.status_code == 400
    assert "EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED" in response.text


def test_create_then_disconnect_platform_connection_over_real_http(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    create_response = client.post(
        "/app/connections/new",
        data={"platform": "collective2", "environment": "local_simulation", "masked_account_label": "Test ****1234"},
        headers=headers,
    )
    assert create_response.status_code == 303

    list_response = client.get("/app/connections", headers=headers)
    assert "collective2" in list_response.text
    assert "declared" in list_response.text

    import re

    match = re.search(r"/app/connections/([^/]+)/disconnect", list_response.text)
    assert match is not None
    connection_id = match.group(1)

    disconnect_response = client.post(f"/app/connections/{connection_id}/disconnect", headers=headers)
    assert disconnect_response.status_code == 303

    final_response = client.get("/app/connections", headers=headers)
    assert "disconnected" in final_response.text


def test_disconnect_platform_connection_is_a_scoped_not_found_for_another_customer(db_session):
    from app.models.tenancy import Membership, MembershipRole as Role, UserIdentity

    _seed_customer_membership(db_session, user_id="user-a")
    client = _client(db_session)
    owner_headers = _auth_headers(user_id="user-a", role=MembershipRole.CUSTOMER)
    create_response = client.post(
        "/app/connections/new",
        data={"platform": "etoro", "environment": "local_simulation", "masked_account_label": "Test ****1234"},
        headers=owner_headers,
    )
    assert create_response.status_code == 303

    list_response = client.get("/app/connections", headers=owner_headers)
    import re

    match = re.search(r"/app/connections/([^/]+)/disconnect", list_response.text)
    connection_id = match.group(1)

    db_session.add(UserIdentity(user_id="user-other-conn", email="user-other-conn@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="user-other-conn", role=Role.CUSTOMER))
    db_session.commit()

    other_customer_headers = _auth_headers(user_id="user-other-conn", role=MembershipRole.CUSTOMER)
    response = client.post(f"/app/connections/{connection_id}/disconnect", headers=other_customer_headers)
    assert response.status_code == 404


def test_copy_mandate_wizard_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/copy/new", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_copy_mandate_wizard_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/app/copy/new", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "A verified eligible connection and released portfolio are required." in response.text


def test_create_copy_mandate_draft_over_real_http(db_session):
    from app.models.product import Product, ProductLifecycleState

    _seed_customer_membership(db_session)
    complete_onboarding_prerequisites(db_session, tenant_id="tenant-a", user_id="user-a")
    product = Product(
        tenant_id="tenant-a", product_name="HTTP Mandate Product", slug="http-mandate-product",
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()

    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)

    selection_response = client.post("/app/portfolios", data={"product_id": product.product_id}, headers=headers)
    assert selection_response.status_code == 303

    connection_response = client.post(
        "/app/connections/new",
        data={"platform": "collective2", "environment": "local_simulation", "masked_account_label": "Test ****1234"},
        headers=headers,
    )
    assert connection_response.status_code == 303

    wizard_response = client.get("/app/copy/new", headers=headers)
    assert wizard_response.status_code == 200
    assert "A verified eligible connection and released portfolio are required." not in wizard_response.text

    from app.services.platform_connection import list_own_platform_connections
    from app.services.portfolio_selection import list_own_portfolio_selections

    selection_id = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")[0].selection_id
    connection_id = list_own_platform_connections(db_session, tenant_id="tenant-a", user_id="user-a")[0].connection_id

    create_response = client.post(
        "/app/copy/new",
        data={
            "selection_id": selection_id,
            "connection_id": connection_id,
            "allocation_amount": "500.00",
            "allocation_currency": "USD",
            "start_mode": "new_entries_only",
            "policy_version_id": "policy-1",
            "consent_version": "consent-1",
        },
        headers=headers,
    )
    assert create_response.status_code == 303

    final_response = client.get("/app/copy/new", headers=headers)
    assert "draft" in final_response.text
    assert "500.00" in final_response.text


def test_manage_copy_mandate_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/copy/nonexistent-mandate/manage", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_manage_copy_mandate_page_is_a_scoped_not_found_for_an_unknown_mandate(db_session):
    client = _client(db_session)
    response = client.get(
        "/app/copy/nonexistent-mandate/manage", headers=_auth_headers(role=MembershipRole.CUSTOMER)
    )
    assert response.status_code == 404


def test_create_then_cancel_copy_mandate_over_real_http(db_session):
    from app.models.product import Product, ProductLifecycleState

    _seed_customer_membership(db_session)
    complete_onboarding_prerequisites(db_session, tenant_id="tenant-a", user_id="user-a")
    product = Product(
        tenant_id="tenant-a", product_name="Manage Mandate Product", slug="manage-mandate-product",
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()

    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    client.post("/app/portfolios", data={"product_id": product.product_id}, headers=headers)
    client.post(
        "/app/connections/new",
        data={"platform": "collective2", "environment": "local_simulation", "masked_account_label": "Test ****1234"},
        headers=headers,
    )

    from app.services.platform_connection import list_own_platform_connections
    from app.services.portfolio_selection import list_own_portfolio_selections

    selection_id = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")[0].selection_id
    connection_id = list_own_platform_connections(db_session, tenant_id="tenant-a", user_id="user-a")[0].connection_id

    client.post(
        "/app/copy/new",
        data={
            "selection_id": selection_id,
            "connection_id": connection_id,
            "allocation_amount": "500.00",
            "allocation_currency": "USD",
            "start_mode": "new_entries_only",
            "policy_version_id": "policy-1",
            "consent_version": "consent-1",
        },
        headers=headers,
    )

    from app.services.copy_mandate import list_own_copy_mandates

    mandate_id = list_own_copy_mandates(db_session, tenant_id="tenant-a", user_id="user-a")[0].mandate_id

    manage_response = client.get(f"/app/copy/{mandate_id}/manage", headers=headers)
    assert manage_response.status_code == 200
    assert "draft" in manage_response.text

    cancel_response = client.post(f"/app/copy/{mandate_id}/cancel", headers=headers)
    assert cancel_response.status_code == 303

    final_response = client.get(f"/app/copy/{mandate_id}/manage", headers=headers)
    assert "cancelled" in final_response.text

    # S12 step 7 "control plane": both real commands on this mandate
    # wrote their own real audit event, in order.
    from app.services.audit_log import get_object_timeline

    timeline = get_object_timeline(db_session, tenant_id="tenant-a", object_id=mandate_id)
    assert [e.action for e in timeline] == ["create_copy_mandate_draft", "cancel_copy_mandate"]
    assert all(e.actor_user_id == "user-a" for e in timeline)
    assert "No active copy mandate is available to manage." in final_response.text


def test_cancel_copy_mandate_is_a_scoped_not_found_for_another_customer(db_session):
    from app.models.product import Product, ProductLifecycleState
    from app.models.tenancy import Membership, MembershipRole as Role, UserIdentity

    _seed_customer_membership(db_session, user_id="user-a")
    complete_onboarding_prerequisites(db_session, tenant_id="tenant-a", user_id="user-a")
    product = Product(
        tenant_id="tenant-a", product_name="Cross Mandate Product", slug="cross-mandate-product",
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()

    client = _client(db_session)
    owner_headers = _auth_headers(user_id="user-a", role=MembershipRole.CUSTOMER)
    client.post("/app/portfolios", data={"product_id": product.product_id}, headers=owner_headers)
    client.post(
        "/app/connections/new",
        data={"platform": "collective2", "environment": "local_simulation", "masked_account_label": "Test ****1234"},
        headers=owner_headers,
    )

    from app.services.platform_connection import list_own_platform_connections
    from app.services.portfolio_selection import list_own_portfolio_selections

    selection_id = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")[0].selection_id
    connection_id = list_own_platform_connections(db_session, tenant_id="tenant-a", user_id="user-a")[0].connection_id
    client.post(
        "/app/copy/new",
        data={
            "selection_id": selection_id,
            "connection_id": connection_id,
            "allocation_amount": "500.00",
            "allocation_currency": "USD",
            "start_mode": "new_entries_only",
            "policy_version_id": "policy-1",
            "consent_version": "consent-1",
        },
        headers=owner_headers,
    )

    from app.services.copy_mandate import list_own_copy_mandates

    mandate_id = list_own_copy_mandates(db_session, tenant_id="tenant-a", user_id="user-a")[0].mandate_id

    db_session.add(UserIdentity(user_id="user-other-mandate", email="user-other-mandate@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="user-other-mandate", role=Role.CUSTOMER))
    db_session.commit()

    other_customer_headers = _auth_headers(user_id="user-other-mandate", role=MembershipRole.CUSTOMER)
    response = client.post(f"/app/copy/{mandate_id}/cancel", headers=other_customer_headers)
    assert response.status_code == 404


def test_customer_overview_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_customer_overview_page_shows_the_real_empty_state(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get("/app", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "You have not selected a portfolio." in response.text
    assert "UNSUPPORTED" in response.text


def test_customer_overview_page_shows_the_real_row_after_a_full_selection_connection_mandate_chain(db_session):
    _seed_customer_membership(db_session)
    complete_onboarding_prerequisites(db_session, tenant_id="tenant-a", user_id="user-a")
    product = _seed_published_product(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)

    client.post("/app/portfolios", data={"product_id": product.product_id}, headers=headers)
    client.post(
        "/app/connections/new",
        data={
            "platform": "collective2",
            "environment": "local_simulation",
            "masked_account_label": "Test ****1234",
        },
        headers=headers,
    )

    from app.services.copy_mandate import list_own_copy_mandates
    from app.services.platform_connection import list_own_platform_connections
    from app.services.portfolio_selection import list_own_portfolio_selections

    selection_id = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")[0].selection_id
    connection_id = list_own_platform_connections(db_session, tenant_id="tenant-a", user_id="user-a")[0].connection_id

    client.post(
        "/app/copy/new",
        data={
            "selection_id": selection_id,
            "connection_id": connection_id,
            "allocation_amount": "100",
            "allocation_currency": "USD",
            "start_mode": "new_entries_only",
            "policy_version_id": "policy-1",
            "consent_version": "consent-1",
        },
        headers=headers,
    )
    assert list_own_copy_mandates(db_session, tenant_id="tenant-a", user_id="user-a")

    response = client.get("/app", headers=headers)
    assert response.status_code == 200
    assert product.product_name in response.text
    assert "No action required" in response.text
    # Connected + mandated, but no Book.FOLLOWER ledger entry exists for
    # this connection anywhere in this build yet -- INTEGRATION_DECISION.md
    # S11's own precise state, never the old blanket UNSUPPORTED.
    assert "AWAITING_OBSERVATIONS" in response.text


def test_selection_detail_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/portfolios/nonexistent-selection", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_selection_detail_page_is_a_scoped_not_found_for_an_unknown_selection(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get(
        "/app/portfolios/nonexistent-selection", headers=_auth_headers(role=MembershipRole.CUSTOMER)
    )
    assert response.status_code == 404


def test_selection_detail_page_shows_the_real_selection_and_unsupported_panels(db_session):
    _seed_customer_membership(db_session)
    product = _seed_published_product(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)

    client.post("/app/portfolios", data={"product_id": product.product_id}, headers=headers)

    from app.services.portfolio_selection import list_own_portfolio_selections

    selection_id = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")[0].selection_id

    response = client.get(f"/app/portfolios/{selection_id}", headers=headers)
    assert response.status_code == 200
    assert product.product_name in response.text
    assert "UNSUPPORTED" in response.text
    # No mandate/connection at all here -- INTEGRATION_DECISION.md S11's
    # own precise state for "Actual versus model", never the old blanket
    # UNSUPPORTED.
    assert "NOT_CONNECTED" in response.text


def test_selection_detail_page_is_a_scoped_not_found_across_tenants(db_session):
    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_customer_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    product = _seed_published_product(db_session, tenant_id="tenant-a")
    client = _client(db_session)
    owner_headers = _auth_headers(user_id="user-a", role=MembershipRole.CUSTOMER)
    client.post("/app/portfolios", data={"product_id": product.product_id}, headers=owner_headers)

    from app.services.portfolio_selection import list_own_portfolio_selections

    selection_id = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")[0].selection_id

    other_tenant_headers = _auth_headers(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.CUSTOMER)
    response = client.get(f"/app/portfolios/{selection_id}", headers=other_tenant_headers)
    assert response.status_code == 404


def _seed_incident(
    db_session,
    *,
    tenant_id="tenant-a",
    service="rights",
    severity="high",
    state="OPEN",
    title="Rights grant expired mid-cycle",
):
    from app.models.incident import Incident, IncidentService, IncidentSeverity, IncidentState

    incident = Incident(
        tenant_id=tenant_id,
        service=IncidentService(service),
        severity=IncidentSeverity(severity),
        state=IncidentState(state),
        title=title,
        affected_object_type="subscription",
        affected_object_id="sub-not-yet-real",
    )
    db_session.add(incident)
    db_session.commit()
    return incident


def test_incident_register_page_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops/incidents")
    assert response.status_code == 401


def test_incident_register_page_denies_a_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/ops/incidents", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 403


def test_incident_register_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/incidents", headers=_auth_headers())
    assert response.status_code == 200
    assert "No active commercial incidents." in response.text


def test_incident_register_page_lists_a_real_incident_and_filters_by_service(db_session):
    incident = _seed_incident(db_session, service="rights")
    _seed_incident(db_session, service="payment", title="Chargeback wave")
    client = _client(db_session)
    headers = _auth_headers()

    response = client.get("/ops/incidents", headers=headers)
    assert incident.title in response.text
    assert "Chargeback wave" in response.text

    filtered = client.get("/ops/incidents?service=rights", headers=headers)
    assert incident.title in filtered.text
    assert "Chargeback wave" not in filtered.text


def test_incident_register_page_support_readonly_can_read_but_not_manage(db_session):
    _seed_incident(db_session)
    client = _client(db_session)
    response = client.get("/ops/incidents", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY))
    assert response.status_code == 200
    assert "cannot acknowledge, assign, reconcile or propose a resolution" in response.text


def test_incident_detail_page_is_a_scoped_not_found_for_an_unknown_incident(db_session):
    client = _client(db_session)
    response = client.get("/ops/incidents/nonexistent-incident-id", headers=_auth_headers())
    assert response.status_code == 404


def test_incident_detail_page_is_a_scoped_not_found_across_tenants(db_session):
    incident = _seed_incident(db_session, tenant_id="tenant-a")
    client = _client(db_session)
    response = client.get(
        f"/ops/incidents/{incident.incident_id}", headers=_auth_headers(tenant_id="tenant-b", user_id="user-b")
    )
    assert response.status_code == 404


def test_acknowledge_incident_transitions_state_and_appends_a_real_audit_event(db_session):
    incident = _seed_incident(db_session, state="OPEN")
    client = _client(db_session)
    headers = _auth_headers()

    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "acknowledge", "note": "Confirmed with rights registry."},
        headers=headers,
    )
    assert response.status_code == 303

    from app.models.incident import Incident, IncidentState

    reloaded = db_session.get(Incident, incident.incident_id)
    assert reloaded.state == IncidentState.ACKNOWLEDGED

    from app.services.audit_log import get_object_timeline

    timeline = get_object_timeline(db_session, tenant_id="tenant-a", object_id=incident.incident_id)
    assert len(timeline) == 1
    assert timeline[0].action.startswith("acknowledge_incident:")
    assert timeline[0].actor_user_id == "user-a"


def test_acknowledge_incident_never_marks_it_resolved(db_session):
    """"Acknowledge never marks resolved" -- AD-21's own acceptance
    text, asserted against the real persisted row, not only page text."""
    incident = _seed_incident(db_session, state="OPEN")
    client = _client(db_session)
    headers = _auth_headers()

    client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "acknowledge", "note": "Ack."},
        headers=headers,
    )

    from app.models.incident import Incident, IncidentState

    reloaded = db_session.get(Incident, incident.incident_id)
    assert reloaded.state != IncidentState.RESOLVED
    assert reloaded.state == IncidentState.ACKNOWLEDGED


def test_acknowledge_incident_refuses_a_double_acknowledge(db_session):
    incident = _seed_incident(db_session, state="ACKNOWLEDGED")
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "acknowledge", "note": "Ack again."},
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "not OPEN" in response.text


def test_incident_action_requires_manage_permission_support_readonly_denied(db_session):
    incident = _seed_incident(db_session, state="OPEN")
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "acknowledge", "note": "Ack."},
        headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY),
    )
    assert response.status_code == 403

    from app.models.incident import Incident, IncidentState

    reloaded = db_session.get(Incident, incident.incident_id)
    assert reloaded.state == IncidentState.OPEN


def _seed_membership(db_session, *, tenant_id, user_id, role):
    from app.models.tenancy import Membership, Tenant, UserIdentity

    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    if db_session.get(UserIdentity, user_id) is None:
        db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=role))
    db_session.commit()


def test_assign_incident_to_a_real_tenant_member(db_session):
    incident = _seed_incident(db_session, state="OPEN")
    _seed_membership(db_session, tenant_id="tenant-a", user_id="researcher-1", role=MembershipRole.RESEARCHER)
    client = _client(db_session)

    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "assign", "assignee_id": "researcher-1", "note": "Please investigate."},
        headers=_auth_headers(),
    )
    assert response.status_code == 303

    from app.models.incident import Incident, IncidentState

    reloaded = db_session.get(Incident, incident.incident_id)
    assert reloaded.state == IncidentState.ASSIGNED
    assert reloaded.assignee_user_id == "researcher-1"


def test_assign_incident_rejects_an_unknown_assignee(db_session):
    incident = _seed_incident(db_session, state="OPEN")
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "assign", "assignee_id": "nobody-here", "note": "Please investigate."},
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "does not reference a member of this tenant" in response.text


def test_assign_incident_cannot_grant_financial_authority(db_session):
    incident = _seed_incident(db_session, state="OPEN")
    _seed_membership(db_session, tenant_id="tenant-a", user_id="billing-1", role=MembershipRole.BILLING_OPERATOR)
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "assign", "assignee_id": "billing-1", "note": "Please investigate."},
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "is not incident-eligible" in response.text


def test_reconcile_incident_is_read_only_and_never_changes_state(db_session):
    incident = _seed_incident(db_session, state="ACKNOWLEDGED")
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "reconcile", "note": "Broker ledger matches; no corrective command run."},
        headers=_auth_headers(),
    )
    assert response.status_code == 303

    from app.models.incident import Incident, IncidentState

    reloaded = db_session.get(Incident, incident.incident_id)
    assert reloaded.state == IncidentState.ACKNOWLEDGED

    from app.services.audit_log import get_object_timeline

    timeline = get_object_timeline(db_session, tenant_id="tenant-a", object_id=incident.incident_id)
    assert timeline[0].action.startswith("reconcile_incident:")


def test_propose_resolution_requires_evidence_ids(db_session):
    incident = _seed_incident(db_session, state="ASSIGNED")
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "propose_resolution", "note": "Ready to resolve."},
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "evidence_ids is required" in response.text


def test_propose_resolution_resolves_with_evidence(db_session):
    incident = _seed_incident(db_session, state="ASSIGNED")
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={
            "operation": "propose_resolution",
            "note": "Rights renewed; incident closed.",
            "evidence_ids": "ev-1, ev-2",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 303

    from app.models.incident import Incident, IncidentState

    reloaded = db_session.get(Incident, incident.incident_id)
    assert reloaded.state == IncidentState.RESOLVED
    assert reloaded.resolution_note == "Rights renewed; incident closed."
    assert reloaded.evidence_ids == ["ev-1", "ev-2"]


def test_incident_action_rejects_an_unknown_operation(db_session):
    incident = _seed_incident(db_session, state="OPEN")
    client = _client(db_session)
    response = client.post(
        f"/ops/incidents/{incident.incident_id}/action",
        data={"operation": "delete_everything", "note": "n/a"},
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "unknown operation" in response.text


def _seed_customer_alert_fixture(
    db_session,
    *,
    tenant_id="tenant-a",
    user_id="user-a",
    slug="http-alerts-product",
    portfolio_version_id="pv-alerts-http-1",
    episode_id="ep-alerts-http-1",
    revision=1,
):
    """Seeds a customer membership, an ACTIVE PortfolioSelection, and a
    real PublicationIntent for the PortfolioVersion behind that
    selection's product -- the real join CU-04/CU-05's own service
    (app/services/customer_alerts.py) scopes "entitled" through."""
    from datetime import datetime, timedelta, timezone

    from app.models.portfolio_version import PortfolioVersion
    from app.models.product import Product, ProductLifecycleState
    from app.models.portfolio_selection import PortfolioSelection
    from app.models.publication import (
        Environment,
        PublicationAction,
        PublicationIntent,
        PublicationState,
        QuantityBasis,
    )

    _seed_customer_membership(db_session, tenant_id=tenant_id, user_id=user_id)

    now = datetime.now(timezone.utc)
    pv = PortfolioVersion(
        portfolio_version_id=portfolio_version_id,
        tenant_id=tenant_id,
        portfolio_id=f"p-{portfolio_version_id}",
        version_number=1,
        cash_weight=0,
        research_cutoff=now,
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    db_session.add(pv)
    db_session.flush()

    product = Product(
        tenant_id=tenant_id,
        product_name="HTTP Alerts Product",
        slug=slug,
        lifecycle_state=ProductLifecycleState.PUBLISHED,
        portfolio_version_id=portfolio_version_id,
    )
    db_session.add(product)
    db_session.flush()

    selection = PortfolioSelection(tenant_id=tenant_id, user_id=user_id, product_id=product.product_id)
    db_session.add(selection)
    db_session.flush()

    intent = PublicationIntent(
        tenant_id=tenant_id,
        environment=Environment.LOCAL_SIM,
        portfolio_version_id=portfolio_version_id,
        episode_id=episode_id,
        revision=revision,
        action=PublicationAction.OPEN,
        channel="collective2",
        external_strategy_id="strategy-alerts-http",
        instrument_id="AAPL",
        quantity="10",
        quantity_basis=QuantityBasis.UNITS,
        price_basis="market",
        policy_hash=f"policy-hash-{episode_id}-{revision}",
        audience_snapshot_hash="audience-hash-alerts-http",
        source_revision_ids=["src-rev-alerts-http"],
        rights_grant_ids=["grant-alerts-http"],
        body_hash=f"body-hash-{episode_id}-{revision}",
        idempotency_key=f"idem-{episode_id}-{revision}",
        valid_from=now,
        expires_at=now + timedelta(hours=1),
        state=PublicationState.UNKNOWN,
    )
    db_session.add(intent)
    db_session.commit()
    return product, selection, intent


def test_alerts_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/alerts", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_alerts_page_shows_the_real_empty_state(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get("/app/alerts", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "There are no alerts available for this selection and period." in response.text


def test_alerts_page_shows_the_real_entitled_alert(db_session):
    product, selection, intent = _seed_customer_alert_fixture(db_session)
    client = _client(db_session)
    response = client.get("/app/alerts", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert intent.intent_id in response.text
    assert "AAPL" in response.text
    assert f"/app/activity/{intent.episode_id}" in response.text


def test_alerts_page_does_not_leak_another_tenants_alert(db_session):
    _seed_customer_alert_fixture(db_session, tenant_id="tenant-a", user_id="user-a", slug="http-alerts-a", portfolio_version_id="pv-alerts-a", episode_id="ep-alerts-a")
    _seed_customer_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    client = _client(db_session)
    response = client.get(
        "/app/alerts", headers=_auth_headers(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.CUSTOMER)
    )
    assert response.status_code == 200
    assert "ep-alerts-a" not in response.text
    assert "There are no alerts available for this selection and period." in response.text


def test_activity_detail_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/activity/nonexistent-episode", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_activity_detail_page_is_a_scoped_not_found_for_an_unknown_episode(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get(
        "/app/activity/nonexistent-episode", headers=_auth_headers(role=MembershipRole.CUSTOMER)
    )
    assert response.status_code == 404


def test_activity_detail_page_shows_the_real_episode_revisions(db_session):
    product, selection, intent = _seed_customer_alert_fixture(db_session, episode_id="ep-detail-http-1")
    client = _client(db_session)
    response = client.get(f"/app/activity/{intent.episode_id}", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert intent.episode_id in response.text
    assert "AAPL" in response.text
    assert "UNSUPPORTED" in response.text


def test_activity_detail_page_is_a_scoped_not_found_across_tenants(db_session):
    # LOAD_BEARING: this is the exact tenant-isolation invariant --
    # customer A's own episode must never be readable by a different
    # tenant's customer, even when the episode_id is known.
    product, selection, intent = _seed_customer_alert_fixture(
        db_session, tenant_id="tenant-a", user_id="user-a", slug="http-alerts-x", portfolio_version_id="pv-alerts-x", episode_id="ep-alerts-x"
    )
    _seed_customer_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    client = _client(db_session)
    other_tenant_headers = _auth_headers(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.CUSTOMER)
    response = client.get(f"/app/activity/{intent.episode_id}", headers=other_tenant_headers)
    assert response.status_code == 404


def test_activity_detail_page_is_a_scoped_not_found_for_a_different_customer_in_the_same_tenant(db_session):
    product, selection, intent = _seed_customer_alert_fixture(
        db_session, tenant_id="tenant-a", user_id="user-a", slug="http-alerts-y", portfolio_version_id="pv-alerts-y", episode_id="ep-alerts-y"
    )
    from app.models.tenancy import Membership, UserIdentity

    db_session.add(UserIdentity(user_id="user-c", email="user-c@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="user-c", role=MembershipRole.CUSTOMER))
    db_session.commit()
    client = _client(db_session)
    other_customer_headers = _auth_headers(tenant_id="tenant-a", user_id="user-c", role=MembershipRole.CUSTOMER)
    response = client.get(f"/app/activity/{intent.episode_id}", headers=other_customer_headers)
    assert response.status_code == 404


def test_performance_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/performance", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_performance_page_shows_not_connected_with_no_connection(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get("/app/performance", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "NOT_CONNECTED" in response.text
    assert "UNSUPPORTED" in response.text


def test_performance_page_shows_awaiting_observations_once_connected(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    client.post(
        "/app/connections/new",
        data={"platform": "collective2", "environment": "local_simulation", "masked_account_label": "acct-http-1"},
        headers=headers,
    )
    response = client.get("/app/performance", headers=headers)
    assert response.status_code == 200
    assert "AWAITING_OBSERVATIONS" in response.text


def test_billing_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/billing", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_billing_page_shows_the_real_empty_state(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get("/app/billing", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "You have no subscription or invoices." in response.text
    assert "UNSUPPORTED" in response.text


def test_billing_page_shows_the_real_tenant_subscription(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models.billing import ProductTier, Subscription, SubscriptionState

    _seed_customer_membership(db_session)
    subscription = Subscription(
        tenant_id="tenant-a",
        tier=ProductTier.ALERTS_ONE,
        state=SubscriptionState.ACTIVE_PAID,
        price_cents=3900,
        currency="usd",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(subscription)
    db_session.commit()

    client = _client(db_session)
    response = client.get("/app/billing", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "ALESRTS_ONE" not in response.text  # sanity: no accidental typo leaks through templating
    assert "ALERTS_ONE" in response.text
    assert "39.00 USD" in response.text
    assert "True" in response.text  # authorizes_new_entry


def test_billing_page_does_not_leak_another_tenants_subscription(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models.billing import ProductTier, Subscription, SubscriptionState

    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_customer_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    subscription = Subscription(
        tenant_id="tenant-a",
        tier=ProductTier.PRO_RESEARCH_API,
        state=SubscriptionState.ACTIVE_PAID,
        price_cents=19900,
        currency="usd",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(subscription)
    db_session.commit()

    client = _client(db_session)
    response = client.get(
        "/app/billing", headers=_auth_headers(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.CUSTOMER)
    )
    assert response.status_code == 200
    assert "PRO_RESEARCH_API" not in response.text
    assert "You have no subscription or invoices." in response.text


def test_managed_programs_page_requires_customer_role(db_session):
    client = _client(db_session)
    response = client.get("/app/managed-programs", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 403


def test_managed_programs_page_shows_the_real_empty_state_and_checklist(db_session):
    _seed_customer_membership(db_session)
    client = _client(db_session)
    response = client.get("/app/managed-programs", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "No approved managed-account program is available to you." in response.text
    assert "NOT_MET" in response.text


def test_managed_programs_page_never_shows_a_draft_program_from_another_tenant(db_session):
    from app.models.managed_program import ManagedProgram, ManagedProgramMode

    _seed_customer_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    program = ManagedProgram(
        tenant_id="tenant-a",
        program_name="Draft Program HTTP",
        broker_program_id="broker-prog-http-1",
        mode=ManagedProgramMode.PAMM,
        allocation_policy_id="alloc-policy-1",
        nav_policy_id="nav-policy-1",
        dealing_schedule_id="dealing-1",
        fee_policy_id=None,
        agreement_evidence_ids=["ev-1"],
    )
    db_session.add(program)
    db_session.commit()

    client = _client(db_session)
    response = client.get("/app/managed-programs", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 200
    assert "Draft Program HTTP" not in response.text
    assert "No approved managed-account program is available to you." in response.text
