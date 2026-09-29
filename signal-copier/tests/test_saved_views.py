"""TR-02 Saved views: real, persisted named filter sets for a routed screen
(app/db.py's `saved_views` table + app/main.py's GET/POST/DELETE
/saved-views). Covers:

- a saved view actually surviving as a row a fresh SignalStore/process can
  read back (the "reload the page" guarantee, exercised at the store layer
  directly since that's what the endpoints call into),
- name-uniqueness (this engine is single-owner, so `name` alone is the
  real identity a saved view is looked up/overwritten by),
- the three real HTTP endpoints, owner-gated like every other mutating
  endpoint in this codebase,
- and, per this project's backfill-migration discipline, that a
  pre-existing on-disk database missing `saved_views` upgrades cleanly.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SCHEMA, SignalStore
from app.models import Side, Signal


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _login(client, monkeypatch, main_module, app_config):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]


# --- Store-layer round trip: proves a saved view survives past this process ---

def test_saved_view_persists_and_round_trips_at_the_store_layer(store):
    view_id = store.save_saved_view(
        name="Long AAPL",
        screen="positions",
        filters={"account_id": "acct-1", "symbol": "AAPL", "side": "long"},
        created_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
    )

    # A brand-new SignalStore instance against the SAME file -- simulates a
    # fresh process (e.g. a page reload hitting a server that restarted) --
    # must see the exact same persisted view, not an in-memory artifact.
    reopened = SignalStore(store.db_path)
    listed = reopened.list_saved_views(screen="positions")
    assert len(listed) == 1
    assert listed[0]["id"] == view_id
    assert listed[0]["name"] == "Long AAPL"
    assert listed[0]["filters"] == {"account_id": "acct-1", "symbol": "AAPL", "side": "long"}


def test_list_saved_views_orders_most_recent_first_and_filters_by_screen(store):
    store.save_saved_view(
        name="v1", screen="positions", filters={}, created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    store.save_saved_view(
        name="v2", screen="positions", filters={}, created_at=datetime(2024, 2, 1, tzinfo=timezone.utc)
    )
    store.save_saved_view(
        name="v3-other-screen", screen="orders", filters={}, created_at=datetime(2024, 3, 1, tzinfo=timezone.utc)
    )
    listed = store.list_saved_views(screen="positions")
    assert [v["name"] for v in listed] == ["v2", "v1"]

    listed_all = store.list_saved_views()
    assert len(listed_all) == 3


def test_saved_view_name_must_be_unique(store):
    store.save_saved_view(name="dup", screen="positions", filters={}, created_at=datetime.now(timezone.utc))
    with pytest.raises(sqlite3.IntegrityError):
        store.save_saved_view(name="dup", screen="positions", filters={}, created_at=datetime.now(timezone.utc))


def test_delete_saved_view_returns_whether_a_row_existed(store):
    view_id = store.save_saved_view(name="to-delete", screen="positions", filters={}, created_at=datetime.now(timezone.utc))
    assert store.delete_saved_view(view_id) is True
    assert store.list_saved_views(screen="positions") == []
    assert store.delete_saved_view(view_id) is False  # already gone -- not a real row anymore


# --- Backfill migration: a pre-existing on-disk DB missing saved_views ---

def test_pre_existing_database_without_saved_views_upgrades_cleanly(tmp_path):
    """A database file created before this table existed (schema minus
    saved_views, no alembic_version table at all -- exactly what an
    operator's real, already-deployed database looks like right now) must
    open cleanly, gain the new table, and still work end to end -- never
    require a manual migration step or lose existing data."""
    db_path = tmp_path / "pre_existing.db"
    legacy_schema = SCHEMA.split("-- TR-02 Saved views")[0]
    assert "saved_views" not in legacy_schema  # sanity: the split actually removed it

    conn = sqlite3.connect(db_path)
    conn.executescript(legacy_schema)
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    conn.execute(
        "INSERT INTO signals (id, source, symbol, side, asset_class, received_at, raw) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (signal.id, "test", "AAPL", "buy", "crypto", signal.received_at.isoformat(), "{}"),
    )
    conn.commit()
    conn.close()

    # Opening with the current SignalStore must add saved_views (CREATE
    # TABLE IF NOT EXISTS) without touching the pre-existing signal row.
    upgraded = SignalStore(db_path)

    view_id = upgraded.save_saved_view(
        name="new-view", screen="positions", filters={"symbol": "AAPL"}, created_at=datetime.now(timezone.utc)
    )
    assert upgraded.list_saved_views(screen="positions")[0]["id"] == view_id

    rows = upgraded.list_signals_in_range(source="test")
    assert len(rows) == 1  # the pre-existing signal survived untouched


# --- HTTP endpoints ---

def test_saved_views_endpoints_create_list_and_delete(store, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(main_module, "store", store)

    client = TestClient(main_module.app)
    with client:
        _login(client, monkeypatch, main_module, app_config)

        create = client.post(
            "/saved-views",
            json={"name": "Longs on acct-1", "screen": "positions", "filters": {"account_id": "acct-1", "symbol": "", "side": "long"}},
        )
        assert create.status_code == 200
        view_id = create.json()["id"]

        listed = client.get("/saved-views?screen=positions")
        assert listed.status_code == 200
        views = listed.json()["saved_views"]
        assert any(v["id"] == view_id and v["name"] == "Longs on acct-1" for v in views)

        deleted = client.delete(f"/saved-views/{view_id}")
        assert deleted.status_code == 200

        listed_after = client.get("/saved-views?screen=positions").json()["saved_views"]
        assert all(v["id"] != view_id for v in listed_after)

        missing_delete = client.delete(f"/saved-views/{view_id}")
        assert missing_delete.status_code == 404


def test_saved_views_endpoint_refuses_duplicate_name(store, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(main_module, "store", store)

    client = TestClient(main_module.app)
    with client:
        _login(client, monkeypatch, main_module, app_config)
        payload = {"name": "same-name", "screen": "positions", "filters": {}}
        first = client.post("/saved-views", json=payload)
        assert first.status_code == 200
        second = client.post("/saved-views", json=payload)
        assert second.status_code == 409


def test_saved_views_endpoints_are_owner_gated(store, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    client = TestClient(main_module.app)
    with client:
        # No login/session at all -- every mutating and reading route here
        # must refuse an unauthenticated caller, same as every other
        # owner-gated endpoint in this codebase.
        assert client.get("/saved-views").status_code in (401, 403)
        assert client.post("/saved-views", json={"name": "x", "screen": "positions", "filters": {}}).status_code in (401, 403)
        assert client.delete("/saved-views/1").status_code in (401, 403)
