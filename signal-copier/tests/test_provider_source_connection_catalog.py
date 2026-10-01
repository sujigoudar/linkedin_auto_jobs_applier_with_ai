"""Track 14: the Provider/Source/Connection data model -- app/provider_
catalog.py, app/connections.py, `SignalStore`'s new `providers`/
`sources`/`connections` CRUD methods (app/db.py), the migration backfill
(alembic/versions/0028_add_provider_source_connection_tables.py), and
the new read-only `/provider-catalog/...` REST routes (app/main.py).
"""
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.connections import ConnectionError_, looks_like_raw_credential, validate_connection_registration
from app.db import SCHEMA, _ALEMBIC_DIR, SignalStore
from app.provider_catalog import ProviderCatalogError, validate_provider_registration, validate_source_registration


# --- app/provider_catalog.py / app/connections.py: vocabulary validation ---


def test_validate_provider_registration_rejects_empty_id_and_bad_status():
    with pytest.raises(ProviderCatalogError):
        validate_provider_registration(provider_id="", display_name="Foo")
    with pytest.raises(ProviderCatalogError):
        validate_provider_registration(provider_id="p1", display_name="Foo", status="not-a-real-status")


def test_validate_provider_registration_accepts_real_values():
    validate_provider_registration(
        provider_id="p1",
        display_name="Foo",
        status="live",
        classification="signal_provider",
        account_ownership="personal",
        execution_eligibility="paper",
        certification_state="certified",
    )


def test_validate_source_registration_rejects_bad_role():
    with pytest.raises(ProviderCatalogError):
        validate_source_registration(source_id="s1", provider_id="p1", platform="telegram", role="NOT_A_ROLE")


def test_looks_like_raw_credential_heuristic():
    assert looks_like_raw_credential("PROVIDER_API_KEY") is False
    assert looks_like_raw_credential(None) is False
    assert looks_like_raw_credential("x" * 100) is True
    assert looks_like_raw_credential("sk live abcdef 123") is True


def test_validate_connection_registration_rejects_raw_credential_value():
    with pytest.raises(ConnectionError_):
        validate_connection_registration(
            connection_id="c1", connection_type="telegram_bot", credential_reference="x" * 100
        )
    # A real env-var name is fine.
    validate_connection_registration(
        connection_id="c1", connection_type="telegram_bot", credential_reference="TELEGRAM_BOT_TOKEN"
    )


# --- SignalStore CRUD: providers ---


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "catalog.db")


def test_register_get_list_update_provider(store):
    created = store.register_provider(
        provider_id="acme-signals",
        display_name="Acme Signals",
        aliases=["acme", "acme-trading"],
        status="onboarding",
        classification="signal_provider",
        asset_classes=["stocks", "options"],
        execution_eligibility="disabled",
    )
    assert created["id"] == "acme-signals"
    assert created["aliases"] == ["acme", "acme-trading"]
    assert created["asset_classes"] == ["stocks", "options"]
    assert created["status"] == "onboarding"
    assert created["certification_state"] == "uncertified"

    fetched = store.get_provider_catalog_entry("acme-signals")
    assert fetched == created

    assert store.get_provider_catalog_entry("does-not-exist") is None

    listed = store.list_provider_catalog_entries()
    assert [p["id"] for p in listed] == ["acme-signals"]
    assert store.list_provider_catalog_entries(status="live") == []
    assert [p["id"] for p in store.list_provider_catalog_entries(status="onboarding")] == ["acme-signals"]

    # Re-registering preserves created_at, replaces other fields.
    first_created_at = created["created_at"]
    updated = store.register_provider(provider_id="acme-signals", display_name="Acme Signals LLC", status="live")
    assert updated["display_name"] == "Acme Signals LLC"
    assert updated["status"] == "live"
    assert updated["created_at"] == first_created_at

    patched = store.update_provider_catalog_entry("acme-signals", {"operator_notes": "looks solid", "status": "certified"})
    assert patched["operator_notes"] == "looks solid"
    assert patched["status"] == "certified"

    with pytest.raises(KeyError):
        store.update_provider_catalog_entry("nope", {"status": "live"})
    with pytest.raises(KeyError):
        store.update_provider_catalog_entry("acme-signals", {"not_a_real_column": 1})


def test_register_provider_rejects_bad_vocabulary(store):
    with pytest.raises(ProviderCatalogError):
        store.register_provider(provider_id="p1", display_name="", status="onboarding")


# --- SignalStore CRUD: connections ---


def test_register_get_list_update_connection(store):
    conn_row = store.register_connection(
        connection_id="telegram-bot-1",
        connection_type="telegram_bot",
        display_name="Main Telegram bot",
        credential_reference="TELEGRAM_BOT_TOKEN",
        connection_state="connected",
        authorization_state="authorized",
        capabilities={"realtime_events": True, "history": True},
    )
    assert conn_row["credential_reference"] == "TELEGRAM_BOT_TOKEN"
    assert conn_row["capabilities"] == {"realtime_events": True, "history": True}

    assert store.get_connection("telegram-bot-1") == conn_row
    assert store.get_connection("nope") is None
    assert [c["id"] for c in store.list_connections()] == ["telegram-bot-1"]
    assert [c["id"] for c in store.list_connections(connection_type="telegram_bot")] == ["telegram-bot-1"]
    assert store.list_connections(connection_type="gmail") == []

    store.update_connection_health(
        "telegram-bot-1",
        connection_state="degraded",
        last_heartbeat_at=datetime.now(timezone.utc),
        health_score=0.4,
    )
    refreshed = store.get_connection("telegram-bot-1")
    assert refreshed["connection_state"] == "degraded"
    assert refreshed["health_score"] == 0.4
    assert refreshed["authorization_state"] == "authorized"  # untouched fields preserved

    with pytest.raises(KeyError):
        store.update_connection_health("nope", connection_state="error")


def test_register_connection_never_persists_raw_credential(store):
    with pytest.raises(ConnectionError_):
        store.register_connection(connection_id="c1", connection_type="gmail", credential_reference="a raw secret / value:123")


# --- SignalStore CRUD: sources ---


def test_register_get_list_source_requires_existing_provider_and_connection(store):
    store.register_provider(provider_id="acme", display_name="Acme")
    store.register_connection(connection_id="conn-1", connection_type="telegram_bot", credential_reference="ACME_BOT_TOKEN")

    with pytest.raises(KeyError):
        store.register_source(source_id="s1", provider_id="does-not-exist", platform="telegram")
    with pytest.raises(KeyError):
        store.register_source(source_id="s1", provider_id="acme", platform="telegram", connection_id="no-such-conn")

    created = store.register_source(
        source_id="s1",
        provider_id="acme",
        platform="telegram",
        source_native_id="-100123",
        role="PRIMARY",
        connection_id="conn-1",
        asset_classes=["stocks"],
    )
    assert created["provider_id"] == "acme"
    assert created["role"] == "PRIMARY"
    assert created["connection_id"] == "conn-1"
    assert created["health_state"] == "unqualified"

    assert store.get_source("s1") == created
    assert store.get_source("nope") is None
    assert [s["id"] for s in store.list_sources(provider_id="acme")] == ["s1"]
    assert [s["id"] for s in store.list_sources(connection_id="conn-1")] == ["s1"]
    assert store.list_sources(provider_id="does-not-exist") == []

    store.update_source_health("s1", "healthy", last_success_at=datetime.now(timezone.utc))
    refreshed = store.get_source("s1")
    assert refreshed["health_state"] == "healthy"
    assert refreshed["last_success_at"] is not None

    with pytest.raises(KeyError):
        store.update_source_health("nope", "healthy")


def test_register_source_warns_on_duplicate_feed_url_but_still_registers(store):
    """Fix (Track 33): two `sources` rows registered against the identical
    `url_or_reference` (e.g. an RSS feed_url) are NOT silently allowed to
    proceed invisibly -- the second registration's returned dict carries a
    `duplicate_url_warning` naming the earlier enabled source(s) sharing
    that URL. This is a soft, surfaced warning, not a hard rejection --
    see `register_source`'s own docstring for why (a `research`-purpose
    route and a `signal_candidate`-purpose route legitimately sharing one
    feed is a real, intentional use case)."""
    store.register_provider(provider_id="acme", display_name="Acme")

    first = store.register_source(
        source_id="acme-rss-research",
        provider_id="acme",
        platform="rss",
        url_or_reference="https://acme.example.com/feed.xml",
        role="PRIMARY",
    )
    assert "duplicate_url_warning" not in first

    second = store.register_source(
        source_id="acme-rss-candidate",
        provider_id="acme",
        platform="rss",
        url_or_reference="https://acme.example.com/feed.xml",
        role="SECONDARY",
    )
    assert "duplicate_url_warning" in second
    assert "acme-rss-research" in second["duplicate_url_warning"]
    assert "https://acme.example.com/feed.xml" in second["duplicate_url_warning"]

    # The duplicate is still registered -- a warning, not a rejection.
    assert store.get_source("acme-rss-candidate") is not None
    assert {s["id"] for s in store.list_sources(provider_id="acme")} == {
        "acme-rss-research",
        "acme-rss-candidate",
    }

    # Re-registering (idempotent re-describe) the SAME source id against the
    # SAME url is not "another" source and must not warn against itself.
    redescribed = store.register_source(
        source_id="acme-rss-research",
        provider_id="acme",
        platform="rss",
        url_or_reference="https://acme.example.com/feed.xml",
        role="PRIMARY",
        display_name="renamed",
    )
    assert "duplicate_url_warning" in redescribed  # the OTHER source still shares this URL
    assert "acme-rss-candidate" in redescribed["duplicate_url_warning"]


def test_register_source_no_warning_for_distinct_feed_urls(store):
    """The legitimate, common case -- two different sources with two
    different feed_urls -- must register cleanly with no warning at all."""
    store.register_provider(provider_id="acme", display_name="Acme")

    first = store.register_source(
        source_id="acme-rss-a",
        provider_id="acme",
        platform="rss",
        url_or_reference="https://acme.example.com/feed-a.xml",
    )
    second = store.register_source(
        source_id="acme-rss-b",
        provider_id="acme",
        platform="rss",
        url_or_reference="https://acme.example.com/feed-b.xml",
    )
    assert "duplicate_url_warning" not in first
    assert "duplicate_url_warning" not in second

    # A source with no url_or_reference at all (e.g. telegram) never
    # triggers duplicate detection, even against another NULL one.
    third = store.register_source(source_id="acme-telegram-1", provider_id="acme", platform="telegram")
    fourth = store.register_source(source_id="acme-telegram-2", provider_id="acme", platform="telegram")
    assert "duplicate_url_warning" not in third
    assert "duplicate_url_warning" not in fourth


def test_one_provider_many_sources_across_many_connections(store):
    """The single most important structural change in this track: one
    provider, N sources, each with its own role, across N connections."""
    store.register_provider(provider_id="whop-seller-x", display_name="Whop Seller X")
    store.register_connection(connection_id="whop-notif-device-1", connection_type="android_notification")
    store.register_connection(connection_id="telegram-bot-shared", connection_type="telegram_bot", credential_reference="SHARED_BOT_TOKEN")
    store.register_connection(connection_id="gmail-shared", connection_type="gmail", credential_reference="GMAIL_APP_PASSWORD")

    store.register_source(
        source_id="whop-seller-x-telegram", provider_id="whop-seller-x", platform="telegram",
        role="PRIMARY", connection_id="telegram-bot-shared",
    )
    store.register_source(
        source_id="whop-seller-x-whop", provider_id="whop-seller-x", platform="notification_bridge",
        role="FALLBACK", connection_id="whop-notif-device-1",
    )
    store.register_source(
        source_id="whop-seller-x-email", provider_id="whop-seller-x", platform="email",
        role="SECONDARY", connection_id="gmail-shared",
    )

    sources = store.list_sources(provider_id="whop-seller-x")
    assert {s["id"] for s in sources} == {"whop-seller-x-telegram", "whop-seller-x-whop", "whop-seller-x-email"}
    assert {s["role"] for s in sources} == {"PRIMARY", "FALLBACK", "SECONDARY"}

    # gmail-shared could equally serve another, unrelated provider -- the
    # connection is reusable, not owned by whop-seller-x.
    store.register_provider(provider_id="another-provider", display_name="Another Provider")
    store.register_source(
        source_id="another-provider-email", provider_id="another-provider", platform="email",
        role="PRIMARY", connection_id="gmail-shared",
    )
    assert {s["id"] for s in store.list_sources(connection_id="gmail-shared")} == {
        "whop-seller-x-email", "another-provider-email"
    }


# --- Migration backfill: fresh pre-Track-14 DB -> upgrade -> verify ---


def _run_full_bootstrap_then_downgrade_to(db_path, target_revision: str) -> None:
    """Builds a DB the OLD way (raw SCHEMA + real collector/notification-
    bridge rows, no providers/sources/connections tables at all), the way
    a database from before this track would genuinely look."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("DROP TABLE providers")
    conn.execute("DROP TABLE sources")
    conn.execute("DROP TABLE connections")
    now = datetime.now(timezone.utc).isoformat()

    # Two telegram_collectors-style rows migrated onto `collectors`
    # (Track 8 shape) -- one healthy, one not.
    conn.execute(
        """INSERT INTO collectors
               (id, kind, provider, identity_ref, credential_env_var, provider_name, allowed_uses,
                health_state, provider_config, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, '["private_trading"]', ?, '{}', ?, ?)""",
        ("tg-1", "telegram", "swingtrader-joe", "user123", "TG_JOE_SESSION", "Swing Trader Joe",
         "healthy_qualified", now, now),
    )
    conn.execute(
        """INSERT INTO collectors
               (id, kind, provider, identity_ref, credential_env_var, provider_name, allowed_uses,
                health_state, provider_config, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, '["private_trading"]', ?, '{}', ?, ?)""",
        ("email-1", "email", "options-flow-daily", None, "OPTIONS_FLOW_IMAP_PW", "Options Flow Daily",
         "unqualified", now, now),
    )

    # A notification_bridge_devices row with the Track 12 Whop shape: one
    # app_package, two providers disambiguated by title-pattern rules,
    # plus one bare fallback package with no mapping at all.
    provider_mapping = {
        "com.whop.whop": {
            "rules": [
                {"title_pattern": "Seller Alpha", "provider_name": "whop-seller-alpha"},
                {"title_pattern": "Seller Beta", "provider_name": "whop-seller-beta"},
            ]
        }
    }
    conn.execute(
        """INSERT INTO notification_bridge_devices
               (device_id, pairing_token_hash, app_packages, provider_mapping, last_heartbeat_at,
                recent_completeness, health_state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?)""",
        (
            "device-1",
            "argon2idhash...",
            json.dumps(["com.whop.whop", "com.unmapped.app"]),
            json.dumps(provider_mapping),
            now,
            "healthy_qualified",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    # Stamp this DB at the revision right before ours so `upgrade` below
    # only replays our own migration.
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.stamp(cfg, target_revision)


def test_migration_backfills_providers_sources_connections_from_existing_data(tmp_path):
    db_path = tmp_path / "pre_track14.db"
    _run_full_bootstrap_then_downgrade_to(db_path, "0027")

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0028")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    providers = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM providers")}
    sources = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sources")}
    connections = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM connections")}
    conn.close()

    # --- collectors backfill ---
    assert "swingtrader-joe" in providers
    assert providers["swingtrader-joe"]["display_name"] == "Swing Trader Joe"
    assert "options-flow-daily" in providers

    tg_source = sources["legacy-collector-tg-1"]
    assert tg_source["provider_id"] == "swingtrader-joe"
    assert tg_source["platform"] == "telegram"
    assert tg_source["role"] == "PRIMARY"
    assert tg_source["health_state"] == "healthy"
    assert tg_source["connection_id"] == "legacy-collector-tg-1"

    tg_connection = connections["legacy-collector-tg-1"]
    assert tg_connection["connection_type"] == "legacy_telegram"
    assert tg_connection["credential_reference"] == "TG_JOE_SESSION"

    email_source = sources["legacy-collector-email-1"]
    assert email_source["provider_id"] == "options-flow-daily"
    assert email_source["health_state"] == "unqualified"
    email_connection = connections["legacy-collector-email-1"]
    assert email_connection["credential_reference"] == "OPTIONS_FLOW_IMAP_PW"

    # --- notification_bridge_devices backfill: one connection per device ---
    nb_connection = connections["legacy-notification-bridge-device-1"]
    assert nb_connection["connection_type"] == "android_notification"
    assert nb_connection["credential_reference"] == "hash:argon2idhash..."
    caps = json.loads(nb_connection["capabilities"])
    assert caps["push"] is True
    assert caps["active_retrieval"] is False

    # Two providers disambiguated by the Whop title-pattern rules, both
    # got their own FALLBACK source row on the SAME connection.
    assert "whop-seller-alpha" in providers
    assert "whop-seller-beta" in providers
    alpha_sources = [s for s in sources.values() if s["provider_id"] == "whop-seller-alpha"]
    beta_sources = [s for s in sources.values() if s["provider_id"] == "whop-seller-beta"]
    assert len(alpha_sources) == 1
    assert len(beta_sources) == 1
    assert alpha_sources[0]["role"] == "FALLBACK"
    assert alpha_sources[0]["connection_id"] == "legacy-notification-bridge-device-1"
    assert beta_sources[0]["connection_id"] == "legacy-notification-bridge-device-1"

    # The unmapped package falls back to itself as the provider identity.
    assert "com.unmapped.app" in providers
    unmapped_sources = [s for s in sources.values() if s["provider_id"] == "com.unmapped.app"]
    assert len(unmapped_sources) == 1
    assert unmapped_sources[0]["role"] == "FALLBACK"

    # Nothing fabricated: providers created purely from a bare identity
    # string carry no invented classification/asset classes.
    assert providers["com.unmapped.app"]["classification"] is None
    assert providers["com.unmapped.app"]["asset_classes"] == "[]"

    # Old tables untouched, still present with their original rows.
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM collectors").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM notification_bridge_devices").fetchone()[0] == 1
    conn.close()


def test_migration_backfill_is_idempotent_on_conflict(tmp_path):
    """Running the backfill inserts (ON CONFLICT DO NOTHING everywhere) --
    a provider referenced by both a collectors row and a notification-
    bridge rule sharing the same identity string is not duplicated."""
    db_path = tmp_path / "pre_track14_dup.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("DROP TABLE providers")
    conn.execute("DROP TABLE sources")
    conn.execute("DROP TABLE connections")
    now = datetime.now(timezone.utc).isoformat()
    # Two collectors rows sharing the exact same `provider` string.
    for cid in ("c-1", "c-2"):
        conn.execute(
            """INSERT INTO collectors
                   (id, kind, provider, identity_ref, credential_env_var, provider_name, allowed_uses,
                    health_state, provider_config, created_at, updated_at)
               VALUES (?, 'telegram', 'shared-provider', NULL, NULL, 'Shared Provider', '["private_trading"]',
                       'unqualified', '{}', ?, ?)""",
            (cid, now, now),
        )
    conn.commit()
    conn.close()

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.stamp(cfg, "0027")
    command.upgrade(cfg, "0028")

    conn = sqlite3.connect(db_path)
    provider_count = conn.execute("SELECT COUNT(*) FROM providers WHERE id = 'shared-provider'").fetchone()[0]
    source_count = conn.execute("SELECT COUNT(*) FROM sources WHERE provider_id = 'shared-provider'").fetchone()[0]
    conn.close()
    assert provider_count == 1  # one providers row, not two
    assert source_count == 2  # but each collector still gets its own source row


# --- REST routes: /provider-catalog/... ---


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    test_store = SignalStore(tmp_path / "catalog_api.db")
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module.engine, "store", test_store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_unauthenticated_catalog_requests_are_rejected():
    with TestClient(main_module.app) as anon_client:
        assert anon_client.get("/provider-catalog/providers").status_code in (401, 403, 503)
        assert anon_client.get("/provider-catalog/sources").status_code in (401, 403, 503)
        assert anon_client.get("/provider-catalog/connections").status_code in (401, 403, 503)


def test_catalog_routes_list_and_inspect(client):
    with client:
        test_store = main_module.store
        test_store.register_provider(provider_id="acme", display_name="Acme", status="live")
        test_store.register_connection(connection_id="conn-1", connection_type="telegram_bot")
        test_store.register_source(
            source_id="s1", provider_id="acme", platform="telegram", role="PRIMARY", connection_id="conn-1"
        )

        providers_resp = client.get("/provider-catalog/providers")
        assert providers_resp.status_code == 200
        assert [p["id"] for p in providers_resp.json()["providers"]] == ["acme"]

        provider_resp = client.get("/provider-catalog/providers/acme")
        assert provider_resp.status_code == 200
        assert provider_resp.json()["display_name"] == "Acme"

        missing_provider = client.get("/provider-catalog/providers/does-not-exist")
        assert missing_provider.status_code == 404

        provider_sources_resp = client.get("/provider-catalog/providers/acme/sources")
        assert provider_sources_resp.status_code == 200
        assert [s["id"] for s in provider_sources_resp.json()["sources"]] == ["s1"]

        missing_provider_sources = client.get("/provider-catalog/providers/does-not-exist/sources")
        assert missing_provider_sources.status_code == 404

        sources_resp = client.get("/provider-catalog/sources")
        assert sources_resp.status_code == 200
        assert [s["id"] for s in sources_resp.json()["sources"]] == ["s1"]

        source_resp = client.get("/provider-catalog/sources/s1")
        assert source_resp.status_code == 200
        assert source_resp.json()["provider_id"] == "acme"
        assert client.get("/provider-catalog/sources/nope").status_code == 404

        connections_resp = client.get("/provider-catalog/connections")
        assert connections_resp.status_code == 200
        assert [c["id"] for c in connections_resp.json()["connections"]] == ["conn-1"]

        connection_resp = client.get("/provider-catalog/connections/conn-1")
        assert connection_resp.status_code == 200
        assert connection_resp.json()["connection_type"] == "telegram_bot"
        assert client.get("/provider-catalog/connections/nope").status_code == 404
