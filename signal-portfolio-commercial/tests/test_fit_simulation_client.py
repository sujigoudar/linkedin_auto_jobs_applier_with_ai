"""app/services/fit_simulation_client.py -- the server-side caller of
signal-copier's own `POST /catalog/providers/{source}/fit-simulation`.
Every cross-service HTTP call is mocked at the boundary (an injected
`http_post`), the same way signal-copier's own relay worker tests mock
its outbound POST (tests/test_relay_routes.py there)."""
import json

import pytest

from app import config
from app.services.fit_simulation_client import (
    FitSimUnavailableReason,
    get_fit_sim_availability,
    run_fit_simulation,
)


@pytest.fixture(autouse=True)
def _reset_config(monkeypatch):
    monkeypatch.setattr(config, "SIGNAL_COPIER_BASE_URL", "http://signal-copier.internal")
    monkeypatch.setattr(config, "CATALOG_FIT_SIM_SIGNING_SECRET", "test-catalog-fit-sim-secret")
    monkeypatch.setattr(config, "FIT_SIM_CATALOG_CONFIG_JSON", "{}")


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _configure_mapping(monkeypatch, slug="detail-product", source="alerts_guy", csv_paths=None):
    mapping = {slug: {"source": source, "csv_paths": csv_paths if csv_paths is not None else {"AAPL": "/data/AAPL.csv"}}}
    monkeypatch.setattr(config, "FIT_SIM_CATALOG_CONFIG_JSON", json.dumps(mapping))


# --- honest unavailability, never a fabricated result ---


def test_unavailable_when_signal_copier_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "SIGNAL_COPIER_BASE_URL", "")
    outcome = get_fit_sim_availability("detail-product")
    assert outcome is not None
    assert outcome.reason == FitSimUnavailableReason.NOT_CONFIGURED


def test_unavailable_when_no_slug_mapping_exists():
    outcome = get_fit_sim_availability("detail-product")
    assert outcome is not None
    assert outcome.reason == FitSimUnavailableReason.NO_SOURCE_MAPPED


def test_unavailable_when_mapped_but_no_real_csv_price_data(monkeypatch):
    _configure_mapping(monkeypatch, csv_paths={})
    outcome = get_fit_sim_availability("detail-product")
    assert outcome is not None
    assert outcome.reason == FitSimUnavailableReason.NO_PRICE_DATA


def test_a_malformed_config_json_is_treated_as_empty_not_raised(monkeypatch):
    monkeypatch.setattr(config, "FIT_SIM_CATALOG_CONFIG_JSON", "{not valid json")
    outcome = get_fit_sim_availability("detail-product")
    assert outcome is not None
    assert outcome.reason == FitSimUnavailableReason.NO_SOURCE_MAPPED


def test_run_fit_simulation_never_calls_out_when_unavailable(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("should never be called when unavailable")

    outcome = run_fit_simulation("detail-product", account_size=1000.0, max_per_trade=100.0, http_post=fake_post)
    assert outcome.available is False
    assert calls == []


# --- the real, wired call, mocked at the HTTP boundary ---


def test_run_fit_simulation_returns_a_real_report_when_everything_is_configured(monkeypatch):
    _configure_mapping(monkeypatch)
    signal_copier_body = {
        "summary": {
            "source": "alerts_guy",
            "account_size": 25000.0,
            "max_per_trade": 2500.0,
            "fit_count": 1,
            "fit_percentage": 1.0,
            "simulated_pnl_at_your_size": 250.0,
            "worst_drawdown_at_your_size": 0.0,
            "total_signals": 1,
        },
        "equity_curve": [{"time": "2024-01-02T00:00:00+00:00", "cumulative_pnl": 250.0}],
        "trades": [{"signal_id": "s1", "symbol": "AAPL", "fits": True}],
    }
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(content)
        return _FakeResponse(200, signal_copier_body)

    outcome = run_fit_simulation("detail-product", account_size=25000.0, max_per_trade=2500.0, http_post=fake_post)

    assert outcome.available is True
    assert outcome.report == signal_copier_body
    assert captured["url"] == "http://signal-copier.internal/catalog/providers/alerts_guy/fit-simulation"
    assert "x-catalog-fit-sim-signature" in captured["headers"]
    assert captured["body"]["source"] == "alerts_guy"
    assert captured["body"]["account_size"] == 25000.0
    assert captured["body"]["csv_paths"] == {"AAPL": "/data/AAPL.csv"}


def test_run_fit_simulation_reports_unreachable_honestly_not_fabricated(monkeypatch):
    import httpx

    _configure_mapping(monkeypatch)

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    outcome = run_fit_simulation("detail-product", account_size=1000.0, max_per_trade=100.0, http_post=fake_post)
    assert outcome.available is False
    assert outcome.reason == FitSimUnavailableReason.SERVICE_UNREACHABLE


def test_run_fit_simulation_reports_a_non_200_honestly(monkeypatch):
    _configure_mapping(monkeypatch)

    def fake_post(*args, **kwargs):
        return _FakeResponse(401, {"detail": "bad signature"})

    outcome = run_fit_simulation("detail-product", account_size=1000.0, max_per_trade=100.0, http_post=fake_post)
    assert outcome.available is False
    assert outcome.reason == FitSimUnavailableReason.SIMULATION_FAILED
