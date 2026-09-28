"""app/api/dashboard_routes.py's GET /api/v1/ops/source-coverage --
real HTTP tests, same shape as tests/test_platform_performance_route.py's
own route coverage."""
from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.tenancy import MembershipRole
from app.services.auth import issue_token
from app.services.integration_inbox import ingest_export_event, register_export_stream
from tests.test_integration_inbox import _source_receipt_envelope


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False)


def _auth_headers(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER):
    token = issue_token(tenant_id, user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_source_coverage_endpoint_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/api/v1/ops/source-coverage")
    assert response.status_code == 401


def test_source_coverage_endpoint_denies_a_customer(db_session):
    client = _client(db_session)
    response = client.get("/api/v1/ops/source-coverage", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 403


def test_source_coverage_endpoint_returns_an_empty_report_with_no_data(db_session):
    client = _client(db_session)
    response = client.get("/api/v1/ops/source-coverage", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert response.json() == {
        "total_count": 0, "ledger_recorded_count": 0, "parked_count": 0,
        "received_no_ledger_entry_count": 0, "rows": [],
    }


def test_source_coverage_endpoint_returns_real_disposition_data(db_session):
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()
    envelope = _source_receipt_envelope(quantity="10", price="150.00")
    ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    client = _client(db_session)
    response = client.get("/api/v1/ops/source-coverage", headers=_auth_headers(role=MembershipRole.OWNER))

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["ledger_recorded_count"] == 1
    assert body["rows"][0]["disposition"] == "ledger_recorded"
    assert body["rows"][0]["ledger_entry_id"] is not None


def test_source_coverage_endpoint_allows_researcher(db_session):
    client = _client(db_session)
    response = client.get("/api/v1/ops/source-coverage", headers=_auth_headers(role=MembershipRole.RESEARCHER))
    assert response.status_code == 200
