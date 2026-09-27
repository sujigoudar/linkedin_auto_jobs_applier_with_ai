"""C05 (bounded): OWNER_PASSWORD_HASH, an argon2id alternative to the
legacy plain OWNER_PASSWORD (via pwdlib) -- see app/config.py's docstring
for the generation command and app/auth.py's module docstring for why
this is a genuine improvement, not just an extra option: OWNER_PASSWORD's
actual value is directly usable by anything that can read this process's
environment (a log dump, a leaked .env, a config export); an argon2id
hash is deliberately not.

Mutually exclusive by design (see auth_configured's
_misconfigured_both_credentials_set check) -- an operator with both set
would have no reliable way to know which one governs, so this fails
closed rather than silently picking one.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

import app.main as main_module
from app import config as app_config
from app.auth import _credential_epoch, auth_configured, verify_password
from app.db import SignalStore

_HASHER = PasswordHash.recommended()
_PASSWORD = "correct-horse-battery-staple"
_HASH = _HASHER.hash(_PASSWORD)


@pytest.fixture(autouse=True)
def _clean_owner_password_env(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "OWNER_PASSWORD_HASH", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")


def test_correct_password_verifies_against_the_hash(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD_HASH", _HASH)
    assert verify_password(_PASSWORD) is True


def test_wrong_password_is_rejected_against_the_hash(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD_HASH", _HASH)
    assert verify_password("wrong-password") is False


def test_both_password_and_hash_set_fails_closed_not_ambiguous(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", _PASSWORD)
    monkeypatch.setattr(app_config, "OWNER_PASSWORD_HASH", _HASH)

    assert auth_configured() is False
    assert verify_password(_PASSWORD) is False  # not merely unconfigured -- the actual correct password too


def test_malformed_hash_fails_closed_rather_than_raising(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD_HASH", "not-a-real-argon2-hash")
    assert verify_password(_PASSWORD) is False


def test_credential_epoch_differs_between_plain_and_hash_forms_of_the_same_password(monkeypatch):
    """Switching an operator from OWNER_PASSWORD to OWNER_PASSWORD_HASH (or
    back) must revoke every session issued under the other form -- proven
    here at the epoch-fingerprint level (test_owner_auth.py's
    test_session_with_stale_credential_epoch_is_actually_deleted_from_the_store
    proves what happens to a session once the epoch no longer matches)."""
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", _PASSWORD)
    plain_epoch = _credential_epoch()

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "OWNER_PASSWORD_HASH", _HASH)
    hash_epoch = _credential_epoch()

    assert plain_epoch != hash_epoch


def test_full_login_flow_works_end_to_end_with_only_the_hash_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD_HASH", _HASH)
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    client = TestClient(main_module.app)

    wrong = client.post("/auth/login", json={"password": "wrong-password"})
    assert wrong.status_code == 401

    right = client.post("/auth/login", json={"password": _PASSWORD})
    assert right.status_code == 200
    assert "csrf_token" in right.json()
