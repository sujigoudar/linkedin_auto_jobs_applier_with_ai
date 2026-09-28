"""app/api/dashboard_routes.py's GET /api/v1/ops/platform-performance
-- real HTTP tests, same shape as tests/test_integration_status.py's
own route coverage."""
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.ledger import Book, EvidenceClass, Side
from app.models.tenancy import MembershipRole
from app.services.auth import issue_token
from app.services.ledger import append_entry
from datetime import datetime, timedelta, timezone

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False)


def _auth_headers(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER):
    token = issue_token(tenant_id, user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_platform_performance_endpoint_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/api/v1/ops/platform-performance")
    assert response.status_code == 401


def test_platform_performance_endpoint_denies_a_customer(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    response = client.get("/api/v1/ops/platform-performance", headers=headers)
    assert response.status_code == 403


def test_platform_performance_endpoint_returns_an_empty_report_with_no_data(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    response = client.get("/api/v1/ops/platform-performance", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"realized_pnl": "0", "per_instrument": []}


def test_platform_performance_endpoint_returns_real_computed_pnl(db_session):
    append_entry(
        db_session, tenant_id="tenant-a", book=Book.PLATFORM, instrument="AAPL", side=Side.BUY,
        quantity=Decimal(10), price=Decimal(100), currency="USD", event_time=_T0, source_authority="test",
        evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
    )
    append_entry(
        db_session, tenant_id="tenant-a", book=Book.PLATFORM, instrument="AAPL", side=Side.SELL,
        quantity=Decimal(10), price=Decimal(110), currency="USD", event_time=_T0 + timedelta(minutes=1),
        source_authority="test", evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
    )
    db_session.commit()

    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    response = client.get("/api/v1/ops/platform-performance", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["realized_pnl"]) == Decimal(100)
    assert len(body["per_instrument"]) == 1
    assert body["per_instrument"][0]["instrument"] == "AAPL"
    assert Decimal(body["per_instrument"][0]["realized_pnl"]) == Decimal(100)
    assert body["per_instrument"][0]["closing_fills"] == 1


def test_platform_performance_endpoint_allows_researcher(db_session):
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.RESEARCHER)
    response = client.get("/api/v1/ops/platform-performance", headers=headers)
    assert response.status_code == 200
