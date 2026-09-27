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
