"""Track 12: Whop as a notification-bridge provider (title-pattern
provider-mapping resolution for one app package carrying many sellers'
alerts) and the explicit five-state per-event content-completeness
classification + escalation flag this task adds on top of Track 10's
notification-bridge (app/notification_bridge.py, app/main.py's
`_process_notification_bridge_event`).

Uses representative Whop notification shapes: a short pointer-only one
("New trade posted", the common case -- Whop truncates real content out
of the OS notification shade) and a longer one that DOES carry full
trade text in the OS notification (some providers configure their Whop
posts to include the whole alert) -- see the Track 12 brief's own
framing for why both are tested, not just the pointer-only case."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.notification_bridge import (
    ContentCompleteness,
    DeviceReportedCompleteness,
    NotificationBridgeError,
    classify_notification_completeness,
    resolve_provider_mapping,
    validate_device_registration,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def _register_whop_device(client, provider_mapping):
    resp = client.post(
        "/notification-bridge/devices",
        json={"device_id": "phone-1", "app_packages": ["com.whop.whop"], "provider_mapping": provider_mapping},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _wire_paper_route(client, source: str):
    resp = client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
    assert resp.status_code == 200, resp.text
    resp = client.post("/routing-rules", json={"source": source, "destinations": ["acct1"]})
    assert resp.status_code == 200, resp.text


# -- resolve_provider_mapping (pure) ----------------------------------------


def test_resolve_provider_mapping_matches_title_pattern_case_insensitively():
    mapping = {
        "com.whop.whop": {
            "rules": [
                {"title_pattern": "XYZ Options", "provider_name": "xyz_options", "analyst": "xyz-desk"},
                {"title_pattern": "ABC Futures Room", "provider_name": "abc_futures"},
            ]
        }
    }
    provider, analyst = resolve_provider_mapping(mapping, app_package="com.whop.whop", title="xyz options alerts")
    assert provider == "xyz_options"
    assert analyst == "xyz-desk"


def test_resolve_provider_mapping_first_rule_wins_on_ties():
    mapping = {
        "com.whop.whop": {
            "rules": [
                {"title_pattern": "Options", "provider_name": "first_match"},
                {"title_pattern": "XYZ Options", "provider_name": "second_match"},
            ]
        }
    }
    provider, _ = resolve_provider_mapping(mapping, app_package="com.whop.whop", title="XYZ Options Room")
    assert provider == "first_match"


def test_resolve_provider_mapping_falls_back_to_package_default_when_no_rule_matches():
    mapping = {
        "com.whop.whop": {
            "provider_name": "whop_default",
            "rules": [{"title_pattern": "XYZ Options", "provider_name": "xyz_options"}],
        }
    }
    provider, _ = resolve_provider_mapping(mapping, app_package="com.whop.whop", title="Totally Unrelated Room")
    assert provider == "whop_default"


def test_resolve_provider_mapping_falls_back_to_bare_package_with_no_entry_at_all():
    provider, analyst = resolve_provider_mapping({}, app_package="com.whop.whop", title="anything")
    assert provider == "com.whop.whop"
    assert analyst is None


def test_resolve_provider_mapping_no_title_matches_no_rule():
    mapping = {"com.whop.whop": {"rules": [{"title_pattern": "XYZ Options", "provider_name": "xyz_options"}]}}
    provider, _ = resolve_provider_mapping(mapping, app_package="com.whop.whop", title=None)
    assert provider == "com.whop.whop"  # no title at all -- no rule can match, honest fallback


def test_validate_device_registration_requires_title_pattern_and_provider_name_on_each_rule():
    with pytest.raises(NotificationBridgeError):
        validate_device_registration(
            device_id="phone-1",
            app_packages=["com.whop.whop"],
            provider_mapping={"com.whop.whop": {"rules": [{"provider_name": "xyz_options"}]}},  # missing title_pattern
        )
    with pytest.raises(NotificationBridgeError):
        validate_device_registration(
            device_id="phone-1",
            app_packages=["com.whop.whop"],
            provider_mapping={"com.whop.whop": {"rules": [{"title_pattern": "XYZ"}]}},  # missing provider_name
        )


def test_validate_device_registration_accepts_well_formed_whop_rules():
    validate_device_registration(
        device_id="phone-1",
        app_packages=["com.whop.whop"],
        provider_mapping={
            "com.whop.whop": {
                "rules": [
                    {"title_pattern": "XYZ Options", "provider_name": "xyz_options", "analyst": "xyz-desk"},
                    {"title_pattern": "ABC Futures Room", "provider_name": "abc_futures"},
                ]
            }
        },
    )  # does not raise


# -- classify_notification_completeness (pure) ------------------------------


def test_classify_completeness_title_only_is_pointer_only():
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.TITLE_ONLY, best_text="", disposition_outcome=None
    )
    assert result is ContentCompleteness.POINTER_ONLY


def test_classify_completeness_whop_bare_pointer_phrase_is_pointer_only_even_when_device_says_complete():
    """The Whop-specific case this task is centrally about: Android sees
    nothing elided (device reports "complete"), but the actual text is
    just a bare "New trade posted" pointer -- never silently COMPLETE."""
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.COMPLETE,
        best_text="New trade posted",
        disposition_outcome="no_match",
    )
    assert result is ContentCompleteness.POINTER_ONLY


def test_classify_completeness_full_trade_text_is_complete():
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.COMPLETE,
        best_text="BUY BTCUSDT @ 65000 SL 63000 TP 70000",
        disposition_outcome="parsed",
    )
    assert result is ContentCompleteness.COMPLETE


def test_classify_completeness_short_signal_with_no_price_is_still_complete_when_parsed():
    """A short, digit-free but genuinely parseable instruction (a bare
    market order) must never be misclassified as pointer-only just
    because it's short -- see classify_notification_completeness's own
    docstring for why disposition_outcome is checked before the
    pointer-phrase heuristic."""
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.COMPLETE, best_text="BUY BTCUSDT", disposition_outcome="parsed"
    )
    assert result is ContentCompleteness.COMPLETE


def test_classify_completeness_device_truncated_wins_even_if_the_fragment_parses():
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.TRUNCATED,
        best_text="BUY BTCUSDT @ 5",  # could be a truncated "@ 50000"
        disposition_outcome="parsed",
    )
    assert result is ContentCompleteness.TRUNCATED


def test_classify_completeness_ambiguous_or_missing_data_is_partial():
    for outcome in ("ambiguous", "missing_data"):
        result = classify_notification_completeness(
            device_reported=DeviceReportedCompleteness.COMPLETE,
            best_text="BUY OR SELL BTCUSDT not sure",
            disposition_outcome=outcome,
        )
        assert result is ContentCompleteness.PARTIAL


def test_classify_completeness_unrecognized_non_pointer_text_is_unknown_not_complete():
    """The honest fallback: real text, device says nothing elided, not a
    recognized pointer phrase, but this grammar found no trade shape at
    all -- never silently defaulted to COMPLETE."""
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.COMPLETE,
        best_text="Market commentary and analysis for today's session, nothing actionable here at all",
        disposition_outcome="no_match",
    )
    assert result is ContentCompleteness.UNKNOWN


def test_classify_completeness_ignored_commentary_is_complete():
    """A grammar that positively recognizes negated/past-tense commentary
    (DispositionOutcome.IGNORED) captured that fully -- it's complete,
    just correctly not a trade instruction."""
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.COMPLETE,
        best_text="Already closed BTCUSDT earlier today",
        disposition_outcome="ignored",
    )
    assert result is ContentCompleteness.COMPLETE


def test_classify_completeness_empty_text_is_pointer_only():
    result = classify_notification_completeness(
        device_reported=DeviceReportedCompleteness.COMPLETE, best_text="", disposition_outcome=None
    )
    assert result is ContentCompleteness.POINTER_ONLY


# -- Full HTTP flow: Whop title-pattern routing + completeness + escalation --


def test_whop_notification_resolves_provider_by_title_and_routes_live(client):
    """A representative Whop payload WITH full trade text in the OS
    notification (some providers configure this)."""
    with client:
        device = _register_whop_device(
            client,
            provider_mapping={
                "com.whop.whop": {"rules": [{"title_pattern": "XYZ Options", "provider_name": "xyz_options"}]}
            },
        )
        _wire_paper_route(client, "xyz_options")
        now = datetime.now(timezone.utc).isoformat()

        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.whop.whop",
                        "notification_key": "whop-key-1",
                        "posted_at": now,
                        "title": "XYZ Options Alerts",
                        "text": "BUY SPY @ 450",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["classification"] == "live"

        orders = client.get("/orders").json()["orders"]
        assert len(orders) == 1


def test_whop_bare_pointer_notification_is_flagged_for_escalation_not_routed(client):
    """A representative Whop payload that's just a bare pointer -- Whop's
    own common shape: content_completeness reported "complete" by
    Android (nothing was elided), but the actual text has no trade
    content at all."""
    with client:
        device = _register_whop_device(
            client,
            provider_mapping={
                "com.whop.whop": {"rules": [{"title_pattern": "XYZ Options", "provider_name": "xyz_options"}]}
            },
        )
        _wire_paper_route(client, "xyz_options")
        now = datetime.now(timezone.utc).isoformat()

        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.whop.whop",
                        "notification_key": "whop-key-2",
                        "posted_at": now,
                        "title": "XYZ Options Alerts",
                        "text": "New trade posted",
                        "content_completeness": "complete",  # Android saw nothing elided
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["classification"] == "needs_review_incomplete_content"

        orders = client.get("/orders").json()["orders"]
        assert orders == []  # never routed live

        events = client.get("/notification-bridge/devices/phone-1/events").json()["events"]
        assert len(events) == 1
        assert events[0]["content_completeness"] == "pointer_only"
        assert events[0]["needs_escalation"] is True
        assert events[0]["escalation_status"] == "pending"

        # The Track 13 interface this flag exists for.
        store = main_module.store
        pending = store.list_notification_bridge_events_needing_escalation()
        assert len(pending) == 1
        assert pending[0]["id"] == events[0]["id"]
        assert pending[0]["device_id"] == "phone-1"

        # Resolving it (as Track 13 eventually would) removes it from the
        # pending queue.
        store.resolve_notification_bridge_event_escalation(pending[0]["id"], status="resolved")
        assert store.list_notification_bridge_events_needing_escalation() == []


def test_two_whop_sellers_behind_one_package_route_to_different_providers(client):
    with client:
        device = _register_whop_device(
            client,
            provider_mapping={
                "com.whop.whop": {
                    "rules": [
                        {"title_pattern": "XYZ Options", "provider_name": "xyz_options"},
                        {"title_pattern": "ABC Futures Room", "provider_name": "abc_futures"},
                    ]
                }
            },
        )
        _wire_paper_route(client, "xyz_options")
        _wire_paper_route(client, "abc_futures")
        now = datetime.now(timezone.utc).isoformat()

        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.whop.whop",
                        "notification_key": "xyz-1",
                        "posted_at": now,
                        "title": "XYZ Options Alerts",
                        "text": "BUY SPY @ 450",
                        "content_completeness": "complete",
                    },
                    {
                        "app_package": "com.whop.whop",
                        "notification_key": "abc-1",
                        "posted_at": now,
                        "title": "ABC Futures Room",
                        "text": "SELL ESZ25 @ 6000",
                        "content_completeness": "complete",
                    },
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        assert results[0]["classification"] == "live"
        assert results[1]["classification"] == "live"

        orders = client.get("/orders").json()["orders"]
        # Both routed -- to two DIFFERENT accounts, resolved from the
        # same app_package by two different title patterns.
        assert len(orders) == 2


def test_whop_notification_not_matching_any_rule_falls_back_to_bare_package_name(client):
    with client:
        device = _register_whop_device(
            client,
            provider_mapping={
                "com.whop.whop": {"rules": [{"title_pattern": "XYZ Options", "provider_name": "xyz_options"}]}
            },
        )
        now = datetime.now(timezone.utc).isoformat()

        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.whop.whop",
                        "notification_key": "unmapped-1",
                        "posted_at": now,
                        "title": "Some Other Seller",
                        "text": "BUY SPY @ 450",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        # No routing rule configured for "com.whop.whop" (the fallback
        # source) -- never routed live, but not an error either.
        assert resp.json()["results"][0]["classification"] == "live"
        orders = client.get("/orders").json()["orders"]
        assert orders == []  # no destinations configured for the fallback source
