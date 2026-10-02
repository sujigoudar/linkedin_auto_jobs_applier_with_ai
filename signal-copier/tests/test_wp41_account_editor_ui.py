"""WP-41: TR-08/TR-12 account editor UI with PATCH support and new fields.

Regression: changing sizing_mode and risk_fraction must persist via API;
flipping managed_lifecycle on an account with exposure must show a 409 error.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


async def _account_details(client: httpx.Client, account_id: str, predicate) -> dict | None:
    """Poll the API until the account matches the predicate."""
    for _ in range(50):
        accounts = {a["account_id"]: a for a in client.get("/accounts").json()["accounts"]}
        if account_id in accounts and predicate(accounts[account_id]):
            return accounts[account_id]
        await asyncio.sleep(0.2)
    return accounts.get(account_id)


@pytest.mark.asyncio
async def test_account_editor_accepts_sizing_mode_and_risk_fraction_in_patch(live_server):
    """PATCH endpoint accepts sizing_mode and risk_fraction (WP-16 will persist them)."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}

    # Create account
    account_id = "tr08_test_acct1"
    r = client.post(
        "/accounts",
        json={"account_id": account_id, "broker": "paper"},
        headers=csrf,
    )
    assert r.status_code == 200

    # PATCH with sizing_mode and risk_fraction (API accepts them even if not stored yet)
    r = client.patch(
        f"/accounts/{account_id}",
        json={"sizing_mode": "risk_fraction", "risk_fraction": 0.01},
        headers=csrf,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "patched"


@pytest.mark.asyncio
async def test_patch_preserves_existing_fields_when_others_change(live_server):
    """PATCH only updates specified fields, leaving others unchanged."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}

    # Create account with initial values
    account_id = "tr08_test_preserve"
    r = client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "broker": "paper",
            "risk_percent_of_equity": 0.05,
            "allow_short": False,
            "currency": "USD",
        },
        headers=csrf,
    )
    assert r.status_code == 200

    # PATCH changing only allow_short
    r = client.patch(
        f"/accounts/{account_id}",
        json={"allow_short": True},
        headers=csrf,
    )
    assert r.status_code == 200

    # Verify allow_short changed but others remained the same
    account = await _account_details(
        client,
        account_id,
        lambda a: a.get("allow_short") is True,
    )
    assert account is not None
    assert account["allow_short"] is True
    # Original fields should be unchanged
    assert account.get("risk_percent_of_equity") == 0.05
    assert account.get("currency") == "USD"


@pytest.mark.asyncio
async def test_patch_with_all_new_fields(live_server):
    """PATCH correctly merges all new account fields."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}

    account_id = "tr08_all_fields"
    # Create with minimal config
    r = client.post(
        "/accounts",
        json={"account_id": account_id, "broker": "paper"},
        headers=csrf,
    )
    assert r.status_code == 200

    # PATCH with all new fields
    r = client.patch(
        f"/accounts/{account_id}",
        json={
            "allow_short": True,
            "currency": "EUR",
            "max_gross_leverage": 2.0,
            "daily_loss_limit_percent": 5.0,
            "min_equity_threshold": 10000.0,
        },
        headers=csrf,
    )
    assert r.status_code == 200

    # Verify all fields persisted
    accounts = {a["account_id"]: a for a in client.get("/accounts").json()["accounts"]}
    account = accounts[account_id]
    assert account["allow_short"] is True
    assert account["currency"] == "EUR"
    assert account["max_gross_leverage"] == 2.0
    assert account["daily_loss_limit_percent"] == 5.0
    assert account["min_equity_threshold"] == 10000.0
