"""Track 24: the generic adapter boundary (app/sources/adapter_contract.py),
the SSRF guard (app/sources/url_safety.py), and the one real wired
example, `RssSourceAdapter` (app/sources/rss_source.py) -- plus
confirming the pre-existing "rss" connection-catalog entry (backed by
`app.sources.website.WebsiteSource`, not a placeholder) still drives the
TR-17 onboarding wizard's real three-call sequence end to end.

No live network access is used anywhere in this file -- every HTTP-ish
call is either a local fixture string or a monkeypatched stand-in.
"""
from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import Signal
from app.notification_bridge import ContentCompleteness
from app.sources.adapter_contract import (
    Observation,
    ObservationNormalizationError,
    PollBounds,
    ProbeResult,
    retry_with_backoff,
)
from app.sources.rss_source import RssConnectionConfig, RssSourceAdapter
from app.sources.url_safety import UnsafeFetchUrlError, validate_public_fetch_url

# --- adapter_contract.py: dataclasses + normalize rejection ---------------


def test_observation_defaults_are_honest_unknowns():
    obs = Observation(platform="rss", original_item_id="x")
    assert obs.completeness == "unknown"
    assert obs.purpose == "research"
    assert obs.eligibility_state == "not_eligible"
    assert obs.attachment_refs == []


def test_probe_result_fully_ready_requires_at_least_one_checked_dimension():
    assert ProbeResult(dependency_installed=None).fully_ready is False  # nothing checked -> honestly not ready
    assert ProbeResult(dependency_installed=True).fully_ready is True
    assert ProbeResult(dependency_installed=True, target_readable=False).fully_ready is False


def test_rss_normalize_rejects_malformed_input():
    with pytest.raises(ObservationNormalizationError):
        RssSourceAdapter.normalize({"not_an_entry_key": {}})
    with pytest.raises(ObservationNormalizationError):
        RssSourceAdapter.normalize({"entry": "not-a-dict"})
    with pytest.raises(ObservationNormalizationError):
        RssSourceAdapter.normalize({"entry": {"title": "no id or link at all"}})
    with pytest.raises(ObservationNormalizationError):
        RssSourceAdapter.normalize("not even a dict")


def test_rss_normalize_accepts_a_well_formed_entry():
    obs = RssSourceAdapter.normalize(
        {"entry": {"id": "abc", "link": "https://example.com/a", "title": "T", "summary": "S" * 50}}
    )
    assert obs.original_item_id == "abc"
    assert obs.completeness == ContentCompleteness.COMPLETE.value


@pytest.mark.asyncio
async def test_retry_with_backoff_reraises_after_exhausting_attempts():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await retry_with_backoff(flaky, attempts=3, base_delay=0.001, max_delay=0.001)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retry_with_backoff_succeeds_after_transient_failures():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    result = await retry_with_backoff(flaky, attempts=3, base_delay=0.001, max_delay=0.001)
    assert result == "ok"
    assert calls["n"] == 2


# --- url_safety.py ---------------------------------------------------------


def _fake_getaddrinfo(ip: str):
    def _impl(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return _impl


def test_validate_public_fetch_url_accepts_a_public_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert validate_public_fetch_url("https://example.com/article") == "https://example.com/article"


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.5", "169.254.169.254", "::1", "fc00::1"],
)
def test_validate_public_fetch_url_rejects_non_global_addresses(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(ip))
    with pytest.raises(UnsafeFetchUrlError):
        validate_public_fetch_url("http://internal-looking-host.example/x")


def test_validate_public_fetch_url_rejects_non_http_scheme():
    with pytest.raises(UnsafeFetchUrlError):
        validate_public_fetch_url("ftp://example.com/a")
    with pytest.raises(UnsafeFetchUrlError):
        validate_public_fetch_url("file:///etc/passwd")


def test_validate_public_fetch_url_rejects_embedded_userinfo(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    with pytest.raises(UnsafeFetchUrlError):
        validate_public_fetch_url("https://user:pass@example.com/a")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://localhost:8080/x",
        "http://metadata.google.internal/latest",
        "http://169.254.169.254/latest/meta-data",
        "http://machine.local/x",
        "http://box.internal/x",
        "http://host.lan/x",
    ],
)
def test_validate_public_fetch_url_rejects_known_metadata_and_internal_hostnames(url):
    with pytest.raises(UnsafeFetchUrlError):
        validate_public_fetch_url(url)


# --- RssSourceAdapter --------------------------------------------------


_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Fixture Feed</title>
<item>
  <title>Full article one</title>
  <link>https://example.com/articles/one</link>
  <guid>https://example.com/articles/one</guid>
  <description>This is a genuinely complete article body with plenty of real
  content describing a full market thesis, well past any short-pointer
  length threshold.</description>
</item>
<item>
  <title>Bare pointer two</title>
  <link>https://example.com/articles/two</link>
  <guid>https://example.com/articles/two</guid>
</item>
</channel></rss>
"""

_ACTIONABLE_TEXT = (
    "We are buying XYZ today right here at the pivot after it cleared a clean base "
    "on strong volume. This is a real trade we are putting on now, with a stop at $48.00 "
    "and a target of $62.00."
)

_FEED_WITH_SIGNAL_CANDIDATE_ENTRY = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Fixture Feed</title>
<item>
  <title>Trade alert</title>
  <link>https://example.com/articles/trade</link>
  <guid>https://example.com/articles/trade</guid>
  <description>{_ACTIONABLE_TEXT}</description>
</item>
</channel></rss>
"""


def _make_adapter(feed_text: str, *, store=None, is_signal_candidate_route: bool = False, on_signal=None):
    captured_signals: list[Signal] = []

    async def _on_signal(signal: Signal):
        captured_signals.append(signal)

    async def _fetch_text_fn(url: str) -> str:
        return feed_text

    adapter = RssSourceAdapter(
        on_signal or _on_signal,
        source_id="src_rss_1",
        feed_url="https://example.com/feed.xml",
        store=store,
        is_signal_candidate_route=is_signal_candidate_route,
        fetch_text_fn=_fetch_text_fn,
    )
    return adapter, captured_signals


@pytest.mark.asyncio
async def test_probe_reports_missing_dependency_honestly(monkeypatch):
    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES)

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "feedparser":
            raise ImportError("simulated: feedparser is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    connection = RssConnectionConfig(feed_url="https://example.com/feed.xml")
    result = await adapter.probe(connection)
    assert result.dependency_installed is False
    assert "feedparser" in (result.dependency_installed_reason or "")
    assert result.fully_ready is False


@pytest.mark.asyncio
async def test_probe_reports_ready_dimensions_when_feed_is_reachable():
    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES)
    connection = RssConnectionConfig(feed_url="https://example.com/feed.xml")
    result = await adapter.probe(connection)
    assert result.dependency_installed is True
    assert result.required_fields_available is True
    assert result.target_readable is True
    assert result.fully_ready is True
    # No credential/auth concept for a public feed -- honestly "not checked".
    assert result.credentials_configured is None
    assert result.authentication_verified is None


@pytest.mark.asyncio
async def test_poll_distinguishes_complete_from_pointer_only():
    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES)
    connection = RssConnectionConfig(feed_url="https://example.com/feed.xml")
    observations, checkpoint = await adapter.poll(connection, None, PollBounds(max_items=50, overlap_count=3))
    by_id = {o.original_item_id: o for o in observations}
    assert by_id["https://example.com/articles/one"].completeness == ContentCompleteness.COMPLETE.value
    assert by_id["https://example.com/articles/two"].completeness == ContentCompleteness.POINTER_ONLY.value
    assert checkpoint["order"][:2] == [
        "https://example.com/articles/one",
        "https://example.com/articles/two",
    ]


@pytest.mark.asyncio
async def test_poll_once_is_quiet_on_a_repeat_poll_with_no_new_items(tmp_path):
    store = SignalStore(tmp_path / "rss_quiet.db")
    store.register_provider(provider_id="prov_rss", display_name="RSS Prov")
    store.register_source(source_id="src_rss_1", provider_id="prov_rss", platform="rss")

    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES, store=store)

    first = await adapter.poll_once()
    assert len(first) == 2  # genuinely new items this cycle

    second = await adapter.poll_once()
    assert second == []  # a real, distinguishable "nothing new" result -- not fabricated


@pytest.mark.asyncio
async def test_research_purpose_observation_never_produces_a_signal(tmp_path):
    store = SignalStore(tmp_path / "rss_research.db")
    store.register_provider(provider_id="prov_research", display_name="Research Prov")
    store.register_source(source_id="src_rss_1", provider_id="prov_research", platform="rss")

    adapter, signals = _make_adapter(
        _FEED_WITH_SIGNAL_CANDIDATE_ENTRY, store=store, is_signal_candidate_route=False
    )
    observations = await adapter.poll_once()
    assert len(observations) == 1
    assert signals == []  # research purpose -- never eligible, never a Signal

    rows = store.get_source_observations_for_source("src_rss_1")
    assert len(rows) == 1
    assert rows[0]["purpose"] == "research"
    assert rows[0]["eligibility_state"] == "not_eligible"


@pytest.mark.asyncio
async def test_signal_candidate_purpose_with_complete_content_produces_a_signal(tmp_path):
    store = SignalStore(tmp_path / "rss_candidate.db")
    store.register_provider(provider_id="prov_candidate", display_name="Candidate Prov")
    store.register_source(source_id="src_rss_1", provider_id="prov_candidate", platform="rss")

    adapter, signals = _make_adapter(
        _FEED_WITH_SIGNAL_CANDIDATE_ENTRY, store=store, is_signal_candidate_route=True
    )
    observations = await adapter.poll_once()
    assert len(observations) == 1
    assert len(signals) == 1
    assert signals[0].symbol == "XYZ"
    # Track 29: this adapter genuinely knows its own Track 14 catalog
    # `sources.id` at emission time -- it must be carried onto the
    # emitted Signal, not left None.
    assert signals[0].source_catalog_id == "src_rss_1"

    rows = store.get_source_observations_for_source("src_rss_1")
    assert rows[0]["purpose"] == "signal_candidate"
    assert rows[0]["eligibility_state"] == "eligible"


@pytest.mark.asyncio
async def test_signal_candidate_purpose_with_pointer_only_content_is_recorded_but_not_eligible(tmp_path):
    store = SignalStore(tmp_path / "rss_pointer.db")
    store.register_provider(provider_id="prov_pointer", display_name="Pointer Prov")
    store.register_source(source_id="src_rss_1", provider_id="prov_pointer", platform="rss")

    adapter, signals = _make_adapter(
        _FEED_WITH_COMPLETE_AND_POINTER_ENTRIES, store=store, is_signal_candidate_route=True
    )
    await adapter.poll_once()
    assert signals == []  # the COMPLETE entry doesn't classify as a real trade; the pointer-only one can't be eligible

    rows = {r["original_item_id"]: r for r in store.get_source_observations_for_source("src_rss_1")}
    pointer_row = rows["https://example.com/articles/two"]
    assert pointer_row["eligibility_state"] == "not_eligible"
    assert "pointer_only" in (pointer_row["rejection_reason"] or "")


@pytest.mark.asyncio
async def test_poll_once_persists_checkpoint_on_the_source_row(tmp_path):
    store = SignalStore(tmp_path / "rss_checkpoint.db")
    store.register_provider(provider_id="prov_cp", display_name="Checkpoint Prov")
    store.register_source(source_id="src_rss_1", provider_id="prov_cp", platform="rss")

    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES, store=store)
    await adapter.poll_once()

    row = store.get_source("src_rss_1")
    assert row["acquisition_checkpoint"] is not None
    assert "https://example.com/articles/one" in row["acquisition_checkpoint"]["order"]


@pytest.mark.asyncio
async def test_fetch_linked_article_text_goes_through_url_safety_first(monkeypatch):
    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES)
    with pytest.raises(UnsafeFetchUrlError):
        await adapter.fetch_linked_article_text("http://169.254.169.254/latest/meta-data")


@pytest.mark.asyncio
async def test_fetch_linked_article_text_allows_a_safe_public_url(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES)
    text = await adapter.fetch_linked_article_text("https://example.com/articles/one")
    assert text == _FEED_WITH_COMPLETE_AND_POINTER_ENTRIES


@pytest.mark.asyncio
async def test_fetch_single_item_by_reference(tmp_path):
    adapter, _ = _make_adapter(_FEED_WITH_COMPLETE_AND_POINTER_ENTRIES)
    connection = RssConnectionConfig(feed_url="https://example.com/feed.xml")
    obs = await adapter.fetch(connection, "https://example.com/articles/one")
    assert obs.original_item_id == "https://example.com/articles/one"
    assert obs.completeness == ContentCompleteness.COMPLETE.value

    with pytest.raises(KeyError):
        await adapter.fetch(connection, "https://example.com/articles/does-not-exist")


# --- the pre-existing "rss" connection-catalog entry + the TR-17 wizard ---


@pytest.fixture
def wizard_client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    test_store = SignalStore(tmp_path / "t24_wizard.db")
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module.engine, "store", test_store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_rss_catalog_entry_is_backed_by_a_real_adapter_not_a_placeholder():
    """Confirms the architecture-mapping claim that an "rss" entry
    already exists and is marked implemented -- backed by the
    pre-existing `app.sources.website.WebsiteSource` (its FEED mode),
    NOT a placeholder, and NOT this track's own `RssSourceAdapter`
    (a separate, parallel adapter -- see app/sources/rss_source.py's own
    module docstring for why no catalog change was needed)."""
    from app.connection_catalog import get_connection_catalog_entry

    entry = get_connection_catalog_entry("rss")
    assert entry is not None
    assert entry["status"] == "implemented"
    assert "app/sources/website.py" in entry["verified_against"]


def test_full_wizard_sequence_for_rss_connection_type(wizard_client):
    """The exact three-call sequence tr17.js drives for its Advanced Add
    flow (see tests/test_track21_onboarding_wizard.py's identical-shape
    test for telegram/webhook), exercised against the "rss" catalog
    entry/setup-fields this track's brief asked to confirm actually
    works end to end with zero wizard JS changes."""
    with wizard_client as client:
        fields_resp = client.get("/connections/catalog/rss/setup-fields")
        assert fields_resp.status_code == 200
        fields_body = fields_resp.json()
        assert fields_body["status"] == "implemented"
        field_names = {f["name"] for f in fields_body["fields"]}
        assert "feed_url" in field_names

        p = client.post("/providers", json={"provider_id": "rss_provider", "display_name": "RSS Provider"})
        assert p.status_code == 200, p.text

        c = client.post(
            "/connections",
            json={
                "connection_id": "rss_connection",
                "connection_type": "rss",
                "display_name": "Example feed",
            },
        )
        assert c.status_code == 200, c.text

        s = client.post(
            "/sources",
            json={
                "source_id": "rss_source",
                "provider_id": "rss_provider",
                "platform": "rss",
                "connection_id": "rss_connection",
                "url_or_reference": "https://example.com/feed.xml",
            },
        )
        assert s.status_code == 200, s.text
        assert s.json()["connection_id"] == "rss_connection"
        assert s.json()["url_or_reference"] == "https://example.com/feed.xml"
