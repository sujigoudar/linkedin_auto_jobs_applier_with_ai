"""app/main.py's startup guard against booting ENVIRONMENT=COMMERCIAL_LIVE
with any of app/config.py's repo-committed placeholder secrets
(LOCAL_JWT_SECRET, RELAY_SIGNING_SECRET, CATALOG_FIT_SIM_SIGNING_SECRET,
STRIPE_WEBHOOK_SECRET) still in effect. Real HTTP-level tests -- the
guard only actually runs when the ASGI lifespan is entered, which
`with TestClient(app) as client:` does and a bare `TestClient(app)`
(every other test file's own convention here) deliberately does not; see
starlette's own `TestClient._portal_factory`."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
from app.api.dependencies import get_db_session


def _reload_config_with_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config_module)


@pytest.fixture(autouse=True)
def _restore_config_after_test(monkeypatch):
    """Every test in this file reloads the process-wide `app.config`
    module against mutated environment variables -- undo the env
    mutation and reload `app.config` back to its real (test-default)
    state afterwards so later test files/modules see the normal
    LOCAL_SIM defaults, never whatever COMMERCIAL_LIVE/placeholder
    env this file's own tests set up."""
    yield
    monkeypatch.undo()
    importlib.reload(config_module)


def test_commercial_live_refuses_to_start_with_default_placeholder_secrets(db_session, monkeypatch):
    _reload_config_with_env(monkeypatch, ENVIRONMENT="COMMERCIAL_LIVE")
    # Re-import create_app AFTER the reload so app.main's own
    # `from app import config` sees the just-reloaded module object
    # (reload mutates that object in place, so this is actually
    # unnecessary for correctness, but keeps the import order honest).
    from app.main import create_app

    app = create_app()

    def _override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    with pytest.raises(RuntimeError) as exc_info:
        with TestClient(app):
            pass

    message = str(exc_info.value)
    assert "COMMERCIAL_LIVE" in message
    for placeholder_secret_name in (
        "LOCAL_JWT_SECRET",
        "RELAY_SIGNING_SECRET",
        "CATALOG_FIT_SIM_SIGNING_SECRET",
        "STRIPE_WEBHOOK_SECRET",
    ):
        assert placeholder_secret_name in message


def test_commercial_live_starts_fine_with_real_looking_secret_overrides(db_session, monkeypatch):
    _reload_config_with_env(
        monkeypatch,
        ENVIRONMENT="COMMERCIAL_LIVE",
        LOCAL_JWT_SECRET="a-real-rotated-jwt-secret-from-a-secrets-manager",
        RELAY_SIGNING_SECRET="a-real-rotated-relay-secret-from-a-secrets-manager",
        CATALOG_FIT_SIM_SIGNING_SECRET="a-real-rotated-catalog-fit-sim-secret-from-a-secrets-manager",
        STRIPE_WEBHOOK_SECRET="whsec_a_real_secret_issued_by_stripes_dashboard",
    )
    from app.main import create_app

    app = create_app()

    def _override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_non_commercial_live_environments_are_unaffected_by_placeholder_secrets(db_session, monkeypatch):
    """The guard is a hard gate on COMMERCIAL_LIVE only -- every other
    environment value (the real default, LOCAL_SIM, among them) must
    boot normally even with every placeholder secret still in place."""
    _reload_config_with_env(monkeypatch, ENVIRONMENT="LOCAL_SIM")
    from app.main import create_app

    app = create_app()

    def _override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200


def test_placeholder_secrets_in_use_reports_exactly_the_defaulted_ones(monkeypatch):
    _reload_config_with_env(
        monkeypatch,
        LOCAL_JWT_SECRET="a-real-rotated-jwt-secret-from-a-secrets-manager",
    )
    still_default = config_module.placeholder_secrets_in_use()
    assert "LOCAL_JWT_SECRET" not in still_default
    assert "RELAY_SIGNING_SECRET" in still_default
    assert "CATALOG_FIT_SIM_SIGNING_SECRET" in still_default
    assert "STRIPE_WEBHOOK_SECRET" in still_default
