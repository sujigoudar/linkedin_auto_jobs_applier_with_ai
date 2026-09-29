"""E02: POST /sources/{source_name}/import-signals -- the owner-gated
history-import workflow around the real classify_batch primitive. Batch
review (classify-messages) never creates a signal; this endpoint is the
only real path that turns a selected, classified historical message into
a real Signal row, and it re-classifies server-side rather than trusting
whatever the client saw."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client, store


def test_imports_only_parsed_messages_as_real_signal_rows(client):
    test_client, store = client
    with test_client:
        response = test_client.post(
            "/sources/telegram_channel_1/import-signals",
            json={
                "texts": [
                    "BUY BTCUSDT @ 65000",
                    "DO NOT BUY AAPL 10",  # negated -> ignored, must not import
                    "just chatting about the market",  # no_match, must not import
                ],
                "batch_label": "test-batch-1",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["imported"]) == 1
    assert body["imported"][0]["symbol"] == "BTCUSDT"
    assert body["imported"][0]["import_batch"] == "test-batch-1"
    assert [s["outcome"] for s in body["skipped"]] == ["ignored", "no_match"]

    rows = store.list_recent_signals(limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["source"] == "telegram_channel_1"
    # The distinguishing marker this task adds: a real, non-null
    # import_batch column value only on a backfilled row.
    assert rows[0]["import_batch"] == "test-batch-1"


def test_never_imports_a_message_the_owner_did_not_select(client):
    """The endpoint only ever imports texts explicitly present in the
    request body -- nothing implicit, nothing from a previous call."""
    test_client, store = client
    with test_client:
        test_client.post(
            "/sources/telegram_channel_1/import-signals",
            json={"texts": ["BUY BTCUSDT @ 65000"]},
        )
        response = test_client.post(
            "/sources/telegram_channel_1/import-signals",
            json={"texts": ["SELL ETHUSDT @ 3000"]},
        )
    assert response.status_code == 200
    rows = store.list_recent_signals(limit=10)
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"BTCUSDT", "ETHUSDT"}


def test_auto_generates_a_batch_label_when_none_given(client):
    test_client, store = client
    with test_client:
        response = test_client.post(
            "/sources/telegram_channel_1/import-signals",
            json={"texts": ["BUY BTCUSDT @ 65000"]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["batch_label"]
    assert body["batch_label"].startswith("backfill:")
    rows = store.list_recent_signals(limit=10)
    assert rows[0]["import_batch"] == body["batch_label"]


def test_live_received_signals_have_no_import_batch(client):
    """A signal saved through the ordinary live-ingestion path
    (SignalStore.save_signal directly, same as a webhook/bot would use)
    must show import_batch=None -- the honest distinguishing marker this
    task adds must not appear on signals it didn't create."""
    from app.models import Signal, Side

    test_client, store = client
    store.save_signal(Signal(source="telegram_channel_1", symbol="AAPL", side=Side.BUY))
    rows = store.list_recent_signals(limit=10)
    assert rows[0]["import_batch"] is None


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).post(
            "/sources/telegram_channel_1/import-signals", json={"texts": ["BUY BTCUSDT"]}
        )
    assert response.status_code == 401


def test_disposition_mapping_is_load_bearing(client, monkeypatch):
    """Load-bearing verification: if the outcome mapping is broken so an
    unparseable message is (wrongly) reported/imported as parsed, this
    test must fail. See the module docstring in app/sources/text_parser.py
    for the real classify_batch this exercises."""
    test_client, store = client
    with test_client:
        response = test_client.post(
            "/sources/telegram_channel_1/import-signals",
            json={"texts": ["just chatting about the market"]},
        )
    body = response.json()
    assert body["imported"] == []
    assert body["skipped"][0]["outcome"] == "no_match"
    assert store.list_recent_signals(limit=10) == []
