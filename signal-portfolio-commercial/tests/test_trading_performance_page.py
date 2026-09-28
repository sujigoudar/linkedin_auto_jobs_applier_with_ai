"""app/api/dashboard_routes.py's GET /ops/trading -- the rendered
owner workspace page for Integration Status + Platform Performance,
plus AD-01's own conditional link to it. Real HTTP tests, same shape
as tests/test_dashboard_routes.py's own AD-01 coverage."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.ledger import Book, EvidenceClass, Side
from app.models.tenancy import MembershipRole
from app.services.auth import issue_token
from app.services.integration_inbox import register_export_stream
from app.services.ledger import append_entry

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


def test_trading_page_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/ops/trading")
    assert response.status_code == 401


def test_trading_page_denies_a_customer(db_session):
    client = _client(db_session)
    response = client.get("/ops/trading", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 403


def test_trading_page_shows_the_real_empty_state(db_session):
    client = _client(db_session)
    response = client.get("/ops/trading", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "No export streams are registered" in response.text
    assert "No Book.PLATFORM ledger entries exist" in response.text


def test_trading_page_renders_real_stream_and_performance_data(db_session):
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
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
    response = client.get("/ops/trading", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert "signal-copier:acct1" in response.text
    assert "AAPL" in response.text


def test_ad01_page_links_to_trading_page_for_owner(db_session):
    client = _client(db_session)
    response = client.get("/ops", headers=_auth_headers(role=MembershipRole.OWNER))
    assert response.status_code == 200
    assert '/ops/trading' in response.text


def test_ad01_page_does_not_link_to_trading_page_for_support_readonly(db_session):
    client = _client(db_session)
    response = client.get("/ops", headers=_auth_headers(role=MembershipRole.SUPPORT_READONLY))
    assert response.status_code == 200
    assert '/ops/trading' not in response.text
