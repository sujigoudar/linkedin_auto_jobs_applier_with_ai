"""Track 19: connection catalog (AVAILABLE types), capability defaults,
connection health computation, checkpoint-status diagnostics, and the
connection cost-event ledger -- app/connection_catalog.py,
app/connections.py's `default_capabilities_for_connection_type`/
`compute_connection_health`, `SignalStore`'s new connection-health/
checkpoint/cost methods (app/db.py), and the new `/connections/...`
REST routes (app/main.py).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.connection_catalog import get_connection_catalog_entry, list_connection_catalog_types
from app.connections import (
    CONNECTION_CAPABILITY_KEYS,
    CONNECTION_HEARTBEAT_STALE_SECONDS,
    ConnectionHealthState,
    compute_connection_health,
    default_capabilities,
    default_capabilities_for_connection_type,
)
from app.db import SignalStore


# --- app/connection_catalog.py: the static AVAILABLE-types registry ---


def test_catalog_has_no_duplicate_connection_types():
    types = [entry["connection_type"] for entry in list_connection_catalog_types()]
    assert len(types) == len(set(types))


def test_catalog_status_is_always_implemented_or_not_implemented():
    for entry in list_connection_catalog_types():
        assert entry["status"] in ("implemented", "not_implemented")


def test_catalog_implemented_entries_cite_a_real_verified_module_and_capabilities():
    for entry in list_connection_catalog_types():
        if entry["status"] == "implemented":
            assert entry["verified_against"], f"{entry['connection_type']} claims implemented with no evidence cited"
            assert entry["capabilities"] is not None
        else:
            # Never a guessed capability shape for an adapter that doesn't exist.
            assert entry["capabilities"] is None


def test_catalog_known_implemented_types():
    types = {e["connection_type"]: e["status"] for e in list_connection_catalog_types()}
    for implemented in ("telegram_bot", "telegram_user", "webhook", "email_imap", "sms_twilio", "whatsapp_business", "android_notification"):
        assert types[implemented] == "implemented"


def test_catalog_known_not_implemented_types():
    types = {e["connection_type"]: e["status"] for e in list_connection_catalog_types()}
    for not_implemented in ("make", "zapier", "pipedream", "n8n", "microsoft_365", "sms_api"):
        assert types[not_implemented] == "not_implemented"


def test_get_connection_catalog_entry_missing_returns_none():
    assert get_connection_catalog_entry("does-not-exist") is None


def test_get_connection_catalog_entry_found():
    entry = get_connection_catalog_entry("telegram_bot")
    assert entry is not None
    assert entry["display_name"] == "Telegram Bot"


# --- app/connections.py: default_capabilities_for_connection_type honesty ---


def test_default_capabilities_for_unknown_type_is_all_false():
    caps = default_capabilities_for_connection_type("some-brand-new-thing-nobody-registered")
    assert caps == default_capabilities()
    assert all(v is False for v in caps.values())


def test_default_capabilities_always_covers_every_known_key():
    for connection_type in ("telegram_bot", "telegram_user", "webhook", "android_active_retrieval", "unknown-type"):
        caps = default_capabilities_for_connection_type(connection_type)
        assert set(caps.keys()) == set(CONNECTION_CAPABILITY_KEYS)


def test_default_capabilities_never_fabricates_unverified_telegram_bot_attachments():
    # telegram.py's own docstring/CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED
    # shows a media-only Telegram Bot API message is NOT handled -- this must
    # never be reported as a supported capability.
    caps = default_capabilities_for_connection_type("telegram_bot")
    assert caps["attachments"] is False
    assert caps["images"] is False
    # Telegram's Bot API has no delete-notification update at all.
    assert caps["deletions"] is False
    # But message edits and stable sequential message ids are real.
    assert caps["message_edits"] is True
    assert caps["stable_ids"] is True


def test_default_capabilities_android_active_retrieval_has_no_fabricated_event_stream_claims():
    caps = default_capabilities_for_connection_type("android_active_retrieval")
    assert caps["active_retrieval"] is True
    assert caps["realtime_events"] is False
    assert caps["push"] is False
    assert caps["poll"] is False


def test_default_capabilities_android_notification_has_no_history_backfill():
    caps = default_capabilities_for_connection_type("android_notification")
    assert caps["push"] is True
    assert caps["health_check"] is True
    assert caps["history"] is False
    assert caps["backfill"] is False


# --- app/connections.py: compute_connection_health across all states ---


def _base_connection(**overrides):
    conn = {
        "id": "c1",
        "connection_type": "telegram_bot",
        "connection_state": "unconfigured",
        "authorization_state": "unauthorized",
        "last_heartbeat_at": None,
        "last_successful_event_at": None,
        "last_error_at": None,
        "last_error_detail": None,
    }
    conn.update(overrides)
    return conn


def test_health_never_connected_for_fresh_unconfigured_row():
    result = compute_connection_health(_base_connection())
    assert result["state"] == ConnectionHealthState.NEVER_CONNECTED.value


def test_health_insufficient_data_when_connected_but_no_activity_recorded():
    result = compute_connection_health(_base_connection(connection_state="connected", authorization_state="authorized"))
    assert result["state"] == ConnectionHealthState.INSUFFICIENT_DATA.value


def test_health_offline_for_error_connection_state():
    result = compute_connection_health(_base_connection(connection_state="error"))
    assert result["state"] == ConnectionHealthState.OFFLINE.value


def test_health_offline_for_revoked_authorization():
    result = compute_connection_health(
        _base_connection(connection_state="connected", authorization_state="revoked")
    )
    assert result["state"] == ConnectionHealthState.OFFLINE.value


def test_health_healthy_for_recent_heartbeat():
    now = datetime.now(timezone.utc)
    result = compute_connection_health(
        _base_connection(
            connection_state="connected",
            authorization_state="authorized",
            last_heartbeat_at=(now - timedelta(seconds=10)).isoformat(),
        ),
        now=now,
    )
    assert result["state"] == ConnectionHealthState.HEALTHY.value
    assert result["age_seconds"] < 60


def test_health_degraded_for_stale_heartbeat():
    now = datetime.now(timezone.utc)
    result = compute_connection_health(
        _base_connection(
            connection_state="connected",
            authorization_state="authorized",
            last_heartbeat_at=(now - timedelta(seconds=CONNECTION_HEARTBEAT_STALE_SECONDS + 60)).isoformat(),
        ),
        now=now,
    )
    assert result["state"] == ConnectionHealthState.DEGRADED.value


def test_health_degraded_when_error_more_recent_than_last_good_activity():
    now = datetime.now(timezone.utc)
    result = compute_connection_health(
        _base_connection(
            connection_state="connected",
            authorization_state="authorized",
            last_heartbeat_at=(now - timedelta(minutes=5)).isoformat(),
            last_error_at=(now - timedelta(minutes=1)).isoformat(),
            last_error_detail="rate limited",
        ),
        now=now,
    )
    assert result["state"] == ConnectionHealthState.DEGRADED.value
    assert any("rate limited" in r for r in result["reasons"])


def test_health_unknown_for_unrecognized_state_value():
    result = compute_connection_health(_base_connection(connection_state="not-a-real-state"))
    assert result["state"] == ConnectionHealthState.UNKNOWN.value


# --- SignalStore: register_connection wires real capability defaults ---


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "t19.db")


def test_register_connection_without_explicit_capabilities_gets_real_default(store):
    conn = store.register_connection(connection_id="tg1", connection_type="telegram_bot", credential_reference="TG_TOK")
    assert conn["capabilities"]["message_edits"] is True
    assert conn["capabilities"]["deletions"] is False


def test_register_connection_explicit_capabilities_override_is_respected(store):
    conn = store.register_connection(
        connection_id="tg2",
        connection_type="telegram_bot",
        credential_reference="TG_TOK",
        capabilities={"realtime_events": True, "history": True},
    )
    assert conn["capabilities"] == {"realtime_events": True, "history": True}


def test_register_connection_unknown_type_defaults_all_false(store):
    conn = store.register_connection(connection_id="c1", connection_type="brand-new-type")
    assert all(v is False for v in conn["capabilities"].values())


# --- SignalStore: connection health methods ---


def test_get_connection_health_missing_returns_none(store):
    assert store.get_connection_health("nope") is None


def test_get_connection_health_for_fresh_connection_is_never_connected(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    health = store.get_connection_health("c1")
    assert health["state"] == ConnectionHealthState.NEVER_CONNECTED.value


def test_get_connection_health_summary_counts_real_states(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    store.register_connection(connection_id="c2", connection_type="telegram_bot")
    store.update_connection_health("c2", connection_state="error")
    summary = store.get_connection_health_summary()
    assert summary["total"] == 2
    assert summary["counts"]["never_connected"] == 1
    assert summary["counts"]["offline"] == 1


def test_get_connection_health_summary_empty_store_has_zero_counts(store):
    summary = store.get_connection_health_summary()
    assert summary["total"] == 0
    assert all(count == 0 for count in summary["counts"].values())


# --- SignalStore: checkpoint-status diagnostics ---


def test_checkpoint_status_missing_connection_raises(store):
    with pytest.raises(KeyError):
        store.get_connection_checkpoint_status("nope")


def test_checkpoint_status_reports_not_tracked_honestly(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    status = store.get_connection_checkpoint_status("c1")
    assert status["checkpoint_tracking"] == "not_tracked"
    assert status["gaps"] == "not_tracked"
    assert status["unrecoverable_gaps"] == "not_tracked"
    assert status["sources"] == []


def test_checkpoint_status_surfaces_real_source_freshness(store):
    store.register_connection(connection_id="c1", connection_type="telegram_bot")
    store.register_provider(provider_id="p1", display_name="P1")
    store.register_source(source_id="s1", provider_id="p1", platform="telegram", connection_id="c1")
    store.update_source_health("s1", "healthy", last_event_at=datetime.now(timezone.utc))
    status = store.get_connection_checkpoint_status("c1")
    assert len(status["sources"]) == 1
    assert status["sources"][0]["freshness"] == "caught_up"


def test_checkpoint_status_marks_stale_source_freshness(store):
    store.register_connection(connection_id="c1", connection_type="telegram_bot")
    store.register_provider(provider_id="p1", display_name="P1")
    store.register_source(source_id="s1", provider_id="p1", platform="telegram", connection_id="c1")
    old = datetime.now(timezone.utc) - timedelta(seconds=CONNECTION_HEARTBEAT_STALE_SECONDS + 100)
    store.update_source_health("s1", "healthy", last_event_at=old)
    status = store.get_connection_checkpoint_status("c1")
    assert status["sources"][0]["freshness"] == "stale"


def test_checkpoint_status_marks_no_activity_source_insufficient_data(store):
    store.register_connection(connection_id="c1", connection_type="telegram_bot")
    store.register_provider(provider_id="p1", display_name="P1")
    store.register_source(source_id="s1", provider_id="p1", platform="telegram", connection_id="c1")
    status = store.get_connection_checkpoint_status("c1")
    assert status["sources"][0]["freshness"] == "insufficient_data"


# --- SignalStore: cost-event recording/aggregation ---


def test_record_connection_cost_event_missing_connection_raises(store):
    with pytest.raises(KeyError):
        store.record_connection_cost_event("nope", amount=1.0, category="api")


def test_record_connection_cost_event_rejects_negative_amount(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    with pytest.raises(ValueError):
        store.record_connection_cost_event("c1", amount=-1.0, category="api")


def test_record_connection_cost_event_rejects_empty_category(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    with pytest.raises(ValueError):
        store.record_connection_cost_event("c1", amount=1.0, category="  ")


def test_cost_summary_insufficient_data_when_no_events(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    summary = store.get_connection_cost_summary("c1")
    assert summary["status"] == "insufficient_data"
    assert summary["total_amount"] is None
    assert summary["total_events"] == 0


def test_cost_summary_aggregates_real_recorded_events(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    now = datetime.now(timezone.utc)
    store.record_connection_cost_event("c1", amount=2.0, category="api", event_count=10, occurred_at=now)
    store.record_connection_cost_event(
        "c1", amount=3.0, category="ai", event_count=5, ai_calls=4, tokens=1000, occurred_at=now
    )
    summary = store.get_connection_cost_summary("c1", since=now - timedelta(days=1))
    assert summary["status"] == "ok"
    assert summary["total_amount"] == pytest.approx(5.0)
    assert summary["total_events"] == 15
    assert summary["cost_per_event"] == pytest.approx(5.0 / 15)
    assert summary["ai_calls"] == 4
    assert summary["tokens"] == 1000
    assert summary["browser_minutes"] is None


def test_cost_summary_excludes_events_before_since(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    now = datetime.now(timezone.utc)
    store.record_connection_cost_event("c1", amount=2.0, category="api", occurred_at=now - timedelta(days=40))
    summary = store.get_connection_cost_summary("c1", since=now - timedelta(days=1))
    assert summary["status"] == "insufficient_data"


def test_cost_summary_default_since_is_start_of_this_month(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    now = datetime.now(timezone.utc)
    store.record_connection_cost_event("c1", amount=1.0, category="api", occurred_at=now)
    summary = store.get_connection_cost_summary("c1")
    since = datetime.fromisoformat(summary["since"])
    assert since.day == 1 and since.hour == 0


def test_cost_summary_mixed_currency_is_flagged_not_silently_summed_wrong(store):
    store.register_connection(connection_id="c1", connection_type="webhook")
    now = datetime.now(timezone.utc)
    store.record_connection_cost_event("c1", amount=1.0, currency="USD", category="api", occurred_at=now)
    store.record_connection_cost_event("c1", amount=1.0, currency="EUR", category="api", occurred_at=now)
    summary = store.get_connection_cost_summary("c1", since=now - timedelta(days=1))
    assert summary["status"] == "mixed_currencies"
    assert summary["currency"] == ["EUR", "USD"]


# --- REST routes: /connections/... ---


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    test_store = SignalStore(tmp_path / "t19_api.db")
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module.engine, "store", test_store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_unauthenticated_connections_requests_are_rejected():
    with TestClient(main_module.app) as anon_client:
        assert anon_client.get("/connections/catalog").status_code in (401, 403, 503)
        assert anon_client.get("/connections/health-summary").status_code in (401, 403, 503)
        assert anon_client.post("/connections/c1/cost-events", json={"amount": 1.0, "category": "api"}).status_code in (
            401,
            403,
            503,
        )


def test_get_connections_catalog_route(client):
    with client:
        resp = client.get("/connections/catalog")
        assert resp.status_code == 200
        types = {t["connection_type"] for t in resp.json()["types"]}
        assert "telegram_bot" in types
        assert "make" in types


def test_get_connections_catalog_entry_route(client):
    with client:
        resp = client.get("/connections/catalog/telegram_bot")
        assert resp.status_code == 200
        assert resp.json()["status"] == "implemented"
        assert client.get("/connections/catalog/does-not-exist").status_code == 404


def test_health_summary_and_per_connection_health_routes(client):
    with client:
        test_store = main_module.store
        test_store.register_connection(connection_id="c1", connection_type="telegram_bot")

        summary_resp = client.get("/connections/health-summary")
        assert summary_resp.status_code == 200
        assert summary_resp.json()["total"] == 1

        health_resp = client.get("/connections/c1/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["state"] == ConnectionHealthState.NEVER_CONNECTED.value

        assert client.get("/connections/nope/health").status_code == 404


def test_checkpoint_status_route(client):
    with client:
        test_store = main_module.store
        test_store.register_connection(connection_id="c1", connection_type="webhook")

        resp = client.get("/connections/c1/checkpoint-status")
        assert resp.status_code == 200
        assert resp.json()["checkpoint_tracking"] == "not_tracked"

        assert client.get("/connections/nope/checkpoint-status").status_code == 404


def test_cost_summary_and_record_cost_event_routes(client):
    with client:
        test_store = main_module.store
        test_store.register_connection(connection_id="c1", connection_type="webhook")

        empty_summary = client.get("/connections/c1/cost-summary")
        assert empty_summary.status_code == 200
        assert empty_summary.json()["status"] == "insufficient_data"

        record_resp = client.post(
            "/connections/c1/cost-events",
            json={"amount": 2.5, "category": "api", "event_count": 3},
        )
        assert record_resp.status_code == 200
        assert record_resp.json()["amount"] == 2.5

        summary_resp = client.get("/connections/c1/cost-summary")
        assert summary_resp.status_code == 200
        body = summary_resp.json()
        assert body["status"] == "ok"
        assert body["total_amount"] == pytest.approx(2.5)
        assert body["total_events"] == 3

        assert client.get("/connections/nope/cost-summary").status_code == 404

        bad_record = client.post("/connections/nope/cost-events", json={"amount": 1.0, "category": "api"})
        assert bad_record.status_code == 404

        invalid_amount = client.post("/connections/c1/cost-events", json={"amount": -1.0, "category": "api"})
        assert invalid_amount.status_code == 422


def test_cost_summary_route_rejects_a_malformed_since_param_not_500(client):
    """Track 40 (fault-injection fuzzing -- found by extending
    tests/test_c30_schemathesis_api_fuzzing.py's coverage to this exact
    route): `since` is a plain query-param string, never pydantic-
    validated, so `datetime.fromisoformat(since)` used to raise an
    unhandled `ValueError` straight out of the route as a real 500 for
    ANY malformed value -- reproduced by Schemathesis with the generated
    value `"Subject"`. Now a clean 400, same RISK-01 discipline every
    other parsed-input call site in app/main.py already follows."""
    with client:
        test_store = main_module.store
        test_store.register_connection(connection_id="c1", connection_type="webhook")

        resp = client.get("/connections/c1/cost-summary", params={"since": "not-a-real-timestamp"})
        assert resp.status_code == 400
        assert resp.status_code < 500


def test_record_cost_event_route_rejects_a_malformed_occurred_at_not_500(client):
    """Same bug, same fix, the other call site: `occurred_at` on
    `POST /connections/{connection_id}/cost-events` was parsed with
    `datetime.fromisoformat` BEFORE entering the route's own
    `try/except ValueError: raise HTTPException(422, ...)` block, so
    that existing except clause could never actually catch it -- an
    unhandled 500 on a malformed value reached straight past a guard
    that looked, at a glance, like it already covered this."""
    with client:
        test_store = main_module.store
        test_store.register_connection(connection_id="c1", connection_type="webhook")

        resp = client.post(
            "/connections/c1/cost-events",
            json={"amount": 1.0, "category": "api", "occurred_at": "not-a-real-timestamp"},
        )
        assert resp.status_code == 422
        assert resp.status_code < 500
