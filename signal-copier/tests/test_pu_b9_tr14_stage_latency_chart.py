"""PU-B9: real, additive per-stage latency Chart.js chart on TR-14 ("Trading
performance and execution quality"), sourced from app/execution_quality.py's
`stage_latencies` field (PU-A2) -- one grouped-bar dataset per named stage
(`receipt_to_submission`, `submission_to_fill`, `fill_to_protection`), one
bar-group per (account, symbol).

Seeding strategy: two real orders are placed through the real
webhook -> app/engine.py -> broker path (not fabricated OrderResult rows) --
one to a managed_lifecycle account (reaches all 3 stages, including
protection acknowledgement) and one to a plain account (never reaches
`fill_to_protection` at all -- see app/execution_quality.py's own docstring
on why that stage is entry-only and managed_lifecycle-only). Because a
same-process paper-broker fill happens within microseconds of submission,
this test then directly updates that SAME real order/signal row's own
timestamp columns to real, widely-separated values (2s/3s/4s apart) so the
resulting stage-latency numbers are large, deterministic, and never subject
to wall-clock flakiness -- this mirrors tests/test_pu_a2_execution_stage_
timestamps.py's own use of explicit, controlled timestamps for the exact
same reason. The rows being adjusted are the real ones the real engine
created; nothing here calls compute_execution_quality or fabricates a
response -- the test asserts the chart against whatever
GET /accounts/{id}/execution-quality (the real, unmodified endpoint)
actually returns for these rows.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


def _set_order_stage_timestamps(
    db_path: Path,
    account_id: str,
    symbol: str,
    *,
    received_at: datetime,
    submitted_at: datetime,
    executed_at: datetime,
    protection_confirmed_at: datetime | None,
) -> None:
    """Directly rewrites the real, already-created signal/order rows for
    `(account_id, symbol)`'s one filled order to real, controlled,
    widely-separated timestamps -- see this module's docstring for why."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE signals SET received_at = ? WHERE id = ("
            "  SELECT signal_id FROM orders WHERE account_id = ? AND symbol = ? AND status = 'filled'"
            ")",
            (received_at.isoformat(), account_id, symbol),
        )
        conn.execute(
            "UPDATE orders SET submitted_at = ?, executed_at = ?, protection_confirmed_at = ? "
            "WHERE account_id = ? AND symbol = ? AND status = 'filled'",
            (
                submitted_at.isoformat(),
                executed_at.isoformat(),
                protection_confirmed_at.isoformat() if protection_confirmed_at else None,
                account_id,
                symbol,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_tr14_stage_latency_chart_matches_real_backend_stage_latencies(live_server, tmp_path):
    base_url = live_server
    db_path = tmp_path / "e2e.db"

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    webhook_headers = {"X-Webhook-Secret": "test-webhook-secret"}

    # acct1: managed_lifecycle -- can reach all 3 real stages.
    assert client.post(
        "/accounts", json={"account_id": "acct1", "broker": "paper", "managed_lifecycle": True}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tv-managed", "destinations": ["acct1"]}, headers=csrf_headers
    ).status_code == 200
    entry = client.post(
        "/webhook/tv-managed",
        json={"symbol": "AAPL", "side": "buy", "quantity": 5.0, "stop_loss": 90.0},
        headers=webhook_headers,
    )
    assert entry.status_code == 200
    assert entry.json()["orders"][0]["status"] == "filled"

    # acct2: plain (not managed_lifecycle) -- never reaches
    # fill_to_protection, only receipt_to_submission/submission_to_fill.
    assert client.post(
        "/accounts", json={"account_id": "acct2", "broker": "paper", "managed_lifecycle": False}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tv-plain", "destinations": ["acct2"]}, headers=csrf_headers
    ).status_code == 200
    entry2 = client.post(
        "/webhook/tv-plain",
        json={"symbol": "AAPL", "side": "buy", "quantity": 3.0},
        headers=webhook_headers,
    )
    assert entry2.status_code == 200
    assert entry2.json()["orders"][0]["status"] == "filled"

    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    _set_order_stage_timestamps(
        db_path,
        "acct1",
        "AAPL",
        received_at=t0,
        submitted_at=t0 + timedelta(seconds=2),
        executed_at=t0 + timedelta(seconds=5),
        protection_confirmed_at=t0 + timedelta(seconds=9),
    )
    t1 = datetime(2025, 1, 2, tzinfo=timezone.utc)
    _set_order_stage_timestamps(
        db_path,
        "acct2",
        "AAPL",
        received_at=t1,
        submitted_at=t1 + timedelta(seconds=2),
        executed_at=t1 + timedelta(seconds=6),
        protection_confirmed_at=None,
    )

    # The real, unmodified backend endpoint -- source of truth the chart
    # must match exactly, never a value this test hardcodes independently.
    quality1 = client.get("/accounts/acct1/execution-quality").json()
    quality2 = client.get("/accounts/acct2/execution-quality").json()
    stages1 = {s["stage"]: s["mean_seconds"] for s in quality1["stage_latencies"]["AAPL"]}
    stages2 = {s["stage"]: s["mean_seconds"] for s in quality2["stage_latencies"].get("AAPL", [])}
    assert stages1 == {"receipt_to_submission": 2.0, "submission_to_fill": 3.0, "fill_to_protection": 4.0}
    assert stages2 == {"receipt_to_submission": 2.0, "submission_to_fill": 4.0}
    assert "fill_to_protection" not in stages2

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            await page.click('a[href="#/trade/performance"]')
            await page.wait_for_selector("#tr14-stage-latency-chart", timeout=8000)
            await page.wait_for_function(
                "() => { const c = Chart.getChart(document.getElementById('tr14-stage-latency-chart')); "
                "return !!c && c.data.labels.length === 2; }",
                timeout=8000,
            )

            chart_data = await page.evaluate(
                "() => { const c = Chart.getChart(document.getElementById('tr14-stage-latency-chart')); "
                "return { labels: c.data.labels, "
                "datasets: c.data.datasets.map(d => ({ label: d.label, data: d.data })) }; }"
            )

            labels = chart_data["labels"]
            assert "acct1/AAPL" in labels
            assert "acct2/AAPL" in labels
            idx1 = labels.index("acct1/AAPL")
            idx2 = labels.index("acct2/AAPL")

            datasets_by_stage = {}
            stage_order = ["receipt_to_submission", "submission_to_fill", "fill_to_protection"]
            for dataset, stage in zip(chart_data["datasets"], stage_order, strict=True):
                datasets_by_stage[stage] = dataset["data"]

            # acct1 (managed_lifecycle) reaches all 3 stages -- chart values
            # must exactly equal the real backend mean_seconds above.
            assert datasets_by_stage["receipt_to_submission"][idx1] == 2.0
            assert datasets_by_stage["submission_to_fill"][idx1] == 3.0
            assert datasets_by_stage["fill_to_protection"][idx1] == 4.0

            # acct2 (plain) has real values for the first two stages...
            assert datasets_by_stage["receipt_to_submission"][idx2] == 2.0
            assert datasets_by_stage["submission_to_fill"][idx2] == 4.0

            # ...and LOAD-BEARING: fill_to_protection is honestly absent for
            # acct2 (the real backend response never included it at all) --
            # the chart must render that as `null` (Chart.js draws no bar),
            # never a fabricated zero that would look like a real, verified
            # zero-latency protection stage.
            assert datasets_by_stage["fill_to_protection"][idx2] is None
        finally:
            await browser.close()
