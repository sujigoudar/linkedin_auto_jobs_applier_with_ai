"""app/services/integration_status.py -- Signal Platform Integration
Correction Pack's own INTEGRATION_DECISION.md S11 "Integration Status
panel", the bounded slice of it this build can honestly compute
(mapped streams, received/applied watermarks, real PLATFORM-book
ledger entry count). Plus real HTTP tests for its own
GET /api/v1/ops/integration-status route."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.tenancy import MembershipRole, Tenant
from app.services.auth import issue_token
from app.services.integration_inbox import ingest_export_event, register_export_stream
from app.services.integration_status import get_integration_status

from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    ExecutionAppliedPayload,
    InstrumentIdentity,
    PrivateAccountIdentity,
    build_subject,
    compute_payload_hash,
)


def _seed_tenant(db_session, tenant_id="tenant-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.commit()


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(*, event_id, export_sequence=0, source_stream="signal-copier:acct1", schema_version=None):
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument(), side="buy",
        filled_quantity="10", filled_price="150.00", fee=None, broker="paper", broker_order_id=f"paper-{event_id}",
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    kwargs = dict(
        event_type=EventType.EXECUTION_APPLIED, event_id=event_id, producer_id="signal-copier-instance-1",
        source_stream=source_stream, export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument()),
        event_time=now, effective_time=now, availability_time=now, receipt_time=now,
        environment=Environment.LOCAL_SIM, evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict), payload=payload_dict,
    )
    if schema_version is not None:
        kwargs["schema_version"] = schema_version
    return EventEnvelope(**kwargs)


def test_a_tenant_with_no_registered_streams_reports_an_empty_report(db_session):
    _seed_tenant(db_session)
    report = get_integration_status(db_session, tenant_id="tenant-a")
    assert report.streams == []
    assert report.platform_ledger_entries_count == 0


def test_a_registered_stream_with_no_events_yet_reports_real_zero_counts(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    report = get_integration_status(db_session, tenant_id="tenant-a")
    assert len(report.streams) == 1
    stream = report.streams[0]
    assert stream.received_count == 0
    assert stream.applied_count == 0
    assert stream.unapplied_count == 0
    assert stream.latest_received_at is None


def test_ingested_events_are_reflected_in_real_counts_and_watermarks(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    for sequence, event_id in enumerate(("evt-1", "evt-2", "evt-3")):
        envelope = _execution_envelope(event_id=event_id, export_sequence=sequence)
        ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    report = get_integration_status(db_session, tenant_id="tenant-a")
    stream = report.streams[0]
    assert stream.received_count == 3
    assert stream.applied_count == 3
    assert stream.unapplied_count == 0
    assert stream.latest_received_at is not None
    assert stream.latest_applied_at is not None
    assert report.platform_ledger_entries_count == 3


def test_a_schema_incompatible_event_is_reported_as_visible_incompatibility_not_a_silent_gap(db_session):
    """INT-007 "Unsupported schema version": the status report must
    surface the incompatibility and its cutoff sequence, never just an
    unexplained unapplied count that looks like an ordinary ordering
    gap."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    ingest_export_event(
        db_session,
        _execution_envelope(event_id="evt-0", export_sequence=0, schema_version="99.0.0").model_dump_json(),
    )
    db_session.commit()

    report = get_integration_status(db_session, tenant_id="tenant-a")
    stream = report.streams[0]
    assert stream.received_count == 1
    assert stream.applied_count == 0
    assert stream.unapplied_count == 1
    assert stream.schema_incompatible_count == 1
    assert stream.earliest_schema_incompatible_sequence == 0


def test_a_different_tenants_streams_and_ledger_entries_never_leak_in(db_session):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct-a", environment="LOCAL_SIM")
    db_session.commit()
    ingest_export_event(db_session, _execution_envelope(event_id="evt-a", source_stream="signal-copier:acct-a").model_dump_json())
    db_session.commit()

    report = get_integration_status(db_session, tenant_id="tenant-b")
    assert report.streams == []
    assert report.platform_ledger_entries_count == 0


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False)


def _auth_headers(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER):
    token = issue_token(tenant_id, user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_integration_status_endpoint_requires_authentication(db_session):
    client = _client(db_session)
    response = client.get("/api/v1/ops/integration-status")
    assert response.status_code == 401


def test_integration_status_endpoint_denies_a_customer(db_session):
    _seed_tenant(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.CUSTOMER)
    response = client.get("/api/v1/ops/integration-status", headers=headers)
    assert response.status_code == 403


def test_integration_status_endpoint_denies_support_readonly(db_session):
    """S8's own narrower grant than AD-01's general ops overview --
    SUPPORT_READONLY can see AD-01 but must NOT see this private
    trading telemetry."""
    _seed_tenant(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.SUPPORT_READONLY)
    response = client.get("/api/v1/ops/integration-status", headers=headers)
    assert response.status_code == 403


def test_integration_status_endpoint_returns_real_data_for_owner(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()
    ingest_export_event(db_session, _execution_envelope(event_id="evt-http-1").model_dump_json())
    db_session.commit()

    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.OWNER)
    response = client.get("/api/v1/ops/integration-status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["platform_ledger_entries_count"] == 1
    assert len(body["streams"]) == 1
    assert body["streams"][0]["source_stream"] == "signal-copier:acct1"
    assert body["streams"][0]["received_count"] == 1
    assert body["streams"][0]["applied_count"] == 1


def test_integration_status_endpoint_allows_researcher(db_session):
    _seed_tenant(db_session)
    client = _client(db_session)
    headers = _auth_headers(role=MembershipRole.RESEARCHER)
    response = client.get("/api/v1/ops/integration-status", headers=headers)
    assert response.status_code == 200
