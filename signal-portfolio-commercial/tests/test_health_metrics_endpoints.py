"""Track 11 -- GET /health (public, truthful liveness/readiness,
mirroring signal-copier's own GET /health pattern) and GET /metrics
(owner/publisher_operator-gated Prometheus text), real HTTP tests, same
shape as tests/test_platform_performance_route.py."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.db import set_tenant_scope
from app.main import create_app
from app.models.operating_cost import OperatingCostCategory
from app.models.tenancy import MembershipRole
from app.services.auth import issue_token
from app.services.operating_cost import create_operating_cost
from app.services.service_health import record_health_sample

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


def test_health_is_public_and_truthful(db_session):
    client = _client(db_session)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database_ok"] is True
    assert body["status"] == "ok"


def test_metrics_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/metrics")
    assert response.status_code == 401


def test_metrics_denies_a_customer(db_session):
    client = _client(db_session)
    response = client.get("/metrics", headers=_auth_headers(role=MembershipRole.CUSTOMER))
    assert response.status_code == 403


def test_metrics_returns_owner_readable_prometheus_text(db_session):
    for i in range(6):
        record_health_sample(
            db_session, service_name="commercial", success=True, latency_ms=15,
            sampled_at=_NOW - timedelta(minutes=i),
        )
    db_session.commit()
    set_tenant_scope(db_session, "tenant-a")
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=1000, period_start=_NOW - timedelta(days=1), period_end=_NOW,
    )
    db_session.commit()

    client = _client(db_session)
    response = client.get("/metrics", headers=_auth_headers())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "commercial_service_health_sample_age_seconds" in body
    assert "commercial_service_uptime_ratio_24h" in body
    assert 'service_name="commercial"' in body
    assert "commercial_operating_cost_rows" in body
