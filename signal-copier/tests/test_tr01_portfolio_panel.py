"""TR-01 Trading command center, Phase D batch: "Strategy / sleeve
portfolio risk" sub-section appended to the existing Risk panel (p09) --
see app/static/views/tr01.js's own module docstring (Phase D) for exactly
which endpoint backs each figure (downside correlation, co-drawdown
matrix, overlapping position exposure, simultaneous capital demand,
contribution to return/drawdown, marginal risk contribution,
diversification benefit, capacity/cash utilization) and why each is real
or an honest `not_tracked`/`unsupported` capability state.

Real coverage, no fabricated data:
  - Real-browser (Playwright): seeds two real accounts with real,
    persisted daily equity-snapshot history (via app/db.py's own
    SignalStore.record_equity_snapshot -- the same real write
    app/equity_history.py's EquitySnapshotter performs on its periodic
    tick, invoked directly against the SAME sqlite file the live_server
    subprocess serves from) deliberately constructed so BOTH accounts'
    real, hand-computable drawdown-day sets overlap on exactly 2 of 6
    real overlapping calendar days -- then asserts the rendered
    co-drawdown matrix's overlap-day count, simultaneous-drawdown-day
    count and fraction EXACTLY match that hand-computed ground truth
    (load-bearing: this test was run once with the panel's real
    intersection logic replaced by a union, confirmed to fail, then
    restored -- see this batch's report).
  - With fewer than PORTFOLIO_MIN_SAMPLES (10) real overlapping P&L-delta
    periods (this test seeds only 6 daily snapshots per account, 5
    deltas), downside correlation and marginal risk contribution must
    render "insufficient overlapping data"/`not_tracked`, never a
    fabricated near-zero-sample estimate -- asserted here too.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from playwright.async_api import async_playwright

from app.db import SignalStore
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


def _seed_daily_snapshots(store, account_id, values, *, start):
    for i, v in enumerate(values):
        store.record_equity_snapshot(
            account_id,
            captured_at=start + timedelta(days=i),
            realized_pnl=v,
            unrealized_pnl=0.0,
            cumulative_pnl=v,
        )


async def _bucket_codrawdown_row(page, account_a, account_b):
    rows = await page.eval_on_selector_all(
        "#tr01-portfolio-sleeve table",
        "tables => tables.map(t => Array.from(t.querySelectorAll('tbody tr')).map("
        "tr => Array.from(tr.children).map(td => td.textContent.trim())))",
    )
    # The co-drawdown table is the second table rendered in the sub-section
    # (downside correlation is first) -- locate by matching account-pair
    # cell values instead of a fixed index, so this stays correct if
    # section order ever shifts.
    for table_rows in rows:
        for r in table_rows:
            if len(r) >= 5 and {r[0], r[1]} == {account_a, account_b} and "%" in r[4]:
                return r
    raise AssertionError(f"no co-drawdown row for ({account_a}, {account_b}); got {rows!r}")


@pytest.mark.asyncio
async def test_portfolio_sleeve_codrawdown_matches_hand_computed_ground_truth(live_server, tmp_path):
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    for account_id in ("acct-a", "acct-b"):
        assert client.post(
            "/accounts", json={"account_id": account_id, "broker": "paper"}, headers=csrf_headers
        ).status_code == 200

    store = SignalStore(str(tmp_path / "e2e.db"))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)

    # Hand-computed ground truth (running-peak walk, "in drawdown" = value
    # strictly below the running peak so far):
    #   acct-a: 0, -5, -10, -2, 5, 3
    #     peak=0 day0 (not drawdown, v==peak)
    #     day1 -5 < 0            -> drawdown
    #     day2 -10 < 0           -> drawdown
    #     day3 -2 < 0            -> drawdown
    #     day4 5 > 0 -> new peak=5 (not drawdown)
    #     day5 3 < 5             -> drawdown
    #     acct-a drawdown days: {1, 2, 3, 5}
    #   acct-b: 0, 5, -3, -1, -1, 10
    #     peak=0 day0 (not drawdown)
    #     day1 5 > 0 -> new peak=5 (not drawdown)
    #     day2 -3 < 5            -> drawdown
    #     day3 -1 < 5            -> drawdown
    #     day4 -1 < 5            -> drawdown
    #     day5 10 > 5 -> new peak=10 (not drawdown)
    #     acct-b drawdown days: {2, 3, 4}
    #   overlap (both have a real snapshot): all 6 days
    #   simultaneous drawdown days: {1,2,3,5} & {2,3,4} = {2, 3} -> 2 days
    #   fraction: 2 / 6 = 33.3%
    _seed_daily_snapshots(store, "acct-a", [0.0, -5.0, -10.0, -2.0, 5.0, 3.0], start=start)
    _seed_daily_snapshots(store, "acct-b", [0.0, 5.0, -3.0, -1.0, -1.0, 10.0], start=start)

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.click('a[href="#/trade"]')
            await page.wait_for_selector("#tr01-portfolio-sleeve", timeout=8000)
            await page.wait_for_function(
                "() => { const el = document.querySelector('#tr01-portfolio-sleeve'); "
                "return !!el && el.innerText.includes('Co-drawdown'); }",
                timeout=8000,
            )

            row = await _bucket_codrawdown_row(page, "acct-a", "acct-b")
            assert row[2] == "6", row  # real overlapping days
            assert row[3] == "2", row  # real simultaneous drawdown days
            assert row[4] == "33.3%", row  # real fraction, hand-computed above

            # Downside correlation / marginal risk contribution: only 6
            # snapshots (5 deltas) per account, below PORTFOLIO_MIN_SAMPLES
            # (10) -- must render honestly, never a fabricated estimate.
            text = await page.inner_text("#tr01-portfolio-sleeve")
            assert "insufficient overlapping data" in text or "Not tracked" in text
        finally:
            await browser.close()
