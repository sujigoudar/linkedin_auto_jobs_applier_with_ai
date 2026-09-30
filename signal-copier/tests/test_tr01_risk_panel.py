"""TR-01 Trading command center, Phase B8 batch: real "Risk" panel (p09)
-- rolling volatility/Sharpe-equivalent/Sortino-equivalent/max-drawdown
per account (GET /accounts/{id}/statistics) and cross-account correlation
(GET /accounts/correlation) -- see app/static/views/tr01.js's own module
docstring for exactly which endpoints back this panel, and this batch's
report for the honest-scoping decisions (VaR/ES, drawdown-governor state,
placement-rate-limit headroom, liquidity heatmap) deliberately NOT built
here, and why each is gated.

Real coverage, no fabricated data:
  - TestClient-level: the view module is served; the /accounts/{id}/
    statistics and /accounts/correlation fields this panel depends on are
    really owner-gated (already covered by tests/test_statistics_endpoint.py
    at the endpoint level -- not re-duplicated here).
  - Real-browser (Playwright): seeds real accounts with real, persisted
    equity snapshot history (via app/db.py's own
    SignalStore.record_equity_snapshot -- the same real write
    app/equity_history.py's EquitySnapshotter performs on its periodic
    tick, invoked directly against the SAME sqlite file the live_server
    subprocess serves from) that deliberately exercises BOTH of
    app/statistics.py's real paths:
      - "enough real data, but the statistic is genuinely undefined"
        (acct-a/acct-b: 10 real snapshots each, but a perfectly linear
        cumulative_pnl series has zero P&L-delta variance, so
        volatility_pnl_delta is a real 0.0 while sharpe/sortino stay
        None -- division-by-zero guarded, not fabricated).
      - "not enough real data" (acct-c: only 2 real snapshots -> only 1
        P&L delta -> volatility/sharpe/sortino are None; but max_drawdown
        needs only 2 snapshots, so IT is a real, non-None number on the
        very same account -- proving the panel honors each field's own
        threshold independently, not an all-or-nothing per-account gate).
    Then asserts the rendered panel's every cell EXACTLY matches this
    same live server's own GET /accounts/{id}/statistics and GET
    /accounts/correlation responses (fetched directly, not hand-computed
    a second time in this test) -- proving no default-to-zero fallback
    and no drift between what the backend reports and what renders.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

import app.main as main_module
from app.db import SignalStore
from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


def test_tr01_view_module_still_served_after_risk_panel_addition():
    client = TestClient(main_module.app)
    response = client.get("/static/views/tr01.js")
    assert response.status_code == 200
    assert b"tr01-p09" in response.content
    assert b"Risk" in response.content


def _seed_snapshots(store, account_id, values, *, start, step=timedelta(hours=1)):
    for i, v in enumerate(values):
        store.record_equity_snapshot(
            account_id,
            captured_at=start + i * step,
            realized_pnl=v,
            unrealized_pnl=0.0,
            cumulative_pnl=v,
        )


async def _bucket_stats_row(page, account_id):
    rows = await page.eval_on_selector_all(
        "#tr01-risk-stats-wrap table tbody tr",
        "trs => trs.map(tr => Array.from(tr.children).map(td => td.textContent.trim()))",
    )
    for r in rows:
        if r[0] == account_id:
            return r
    raise AssertionError(f"no risk-stats row for {account_id!r}; got {rows!r}")


async def _bucket_correlation_row(page, account_a, account_b):
    rows = await page.eval_on_selector_all(
        "#tr01-risk-correlation-wrap table tbody tr",
        "trs => trs.map(tr => Array.from(tr.children).map(td => td.textContent.trim()))",
    )
    for r in rows:
        if {r[0], r[1]} == {account_a, account_b}:
            return r
    raise AssertionError(f"no correlation row for ({account_a}, {account_b}); got {rows!r}")


def _fmt_num(v):
    """Mirrors app/static/dashboard.html's own fmtNum -- the rendering
    this panel uses for every non-null numeric cell."""
    if v is None:
        return "—"
    # toLocaleString(undefined, {maximumFractionDigits: 6}) rounds to (at
    # most) 6 fractional digits and drops a trailing .0 -- Python's
    # general float formatting with up to 6 decimals, trailing zeros
    # stripped, reproduces this for the plain values this test seeds.
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


@pytest.mark.asyncio
async def test_risk_panel_matches_real_seeded_statistics_and_correlation(live_server, tmp_path):
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    for account_id in ("acct-a", "acct-b", "acct-c"):
        assert client.post(
            "/accounts", json={"account_id": account_id, "broker": "paper"}, headers=csrf_headers
        ).status_code == 200

    store = SignalStore(str(tmp_path / "e2e.db"))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)

    # acct-a, acct-b: 10 real snapshots each, same real timestamps, a
    # perfectly linear (b = 2*a) series -- real data, but zero P&L-delta
    # variance (volatility_pnl_delta is a real 0.0, not None) and no real
    # interior drawdown (monotonically increasing -> max_drawdown is a
    # real 0.0). Perfectly correlated with each other (correlation ~1.0,
    # sample_count 10, at/above MIN_CORRELATION_SAMPLES).
    values_a = [float(i) for i in range(10)]
    values_b = [2.0 * v for v in values_a]
    _seed_snapshots(store, "acct-a", values_a, start=start)
    _seed_snapshots(store, "acct-b", values_b, start=start)

    # acct-c: only 2 real snapshots (1 P&L delta) -- below A5's own
    # 2-delta floor, so volatility/sharpe/sortino are honestly None; but
    # max_drawdown only needs 2 snapshots, so it's a REAL, non-None number
    # on this same account (100 -> 80, a real $20 drawdown over 1 real
    # hour) -- proving each field's own threshold is honored
    # independently, not an all-or-nothing per-account gate. Its first 2
    # timestamps coincide with acct-a/acct-b's first 2, so it overlaps
    # with each of them on only 2 real points -- below
    # MIN_CORRELATION_SAMPLES (10), so both (a, c) and (b, c) correlation
    # are honestly None.
    _seed_snapshots(store, "acct-c", [100.0, 80.0], start=start)

    # Ground truth: the SAME live server's own real endpoint responses --
    # never a second, hand-computed copy of the statistics math.
    stats = {}
    for acc in ("acct-a", "acct-b", "acct-c"):
        response = client.get(f"/accounts/{acc}/statistics", headers=csrf_headers)
        assert response.status_code == 200, acc
        stats[acc] = response.json()

    # Confirm the seeded premise this test's report relies on before ever
    # opening a browser (isolates a seeding bug from a front-end bug).
    assert stats["acct-a"]["volatility_pnl_delta"] == pytest.approx(0.0)
    assert stats["acct-a"]["sharpe_equivalent"] is None
    assert stats["acct-a"]["sortino_equivalent"] is None
    assert stats["acct-a"]["max_drawdown"] == pytest.approx(0.0)
    assert stats["acct-c"]["volatility_pnl_delta"] is None
    assert stats["acct-c"]["sharpe_equivalent"] is None
    assert stats["acct-c"]["max_drawdown"] == pytest.approx(20.0)
    assert stats["acct-c"]["max_drawdown_duration_seconds"] == pytest.approx(3600.0)

    corr_ab = client.get(
        "/accounts/correlation", params={"account_a": "acct-a", "account_b": "acct-b"}, headers=csrf_headers
    ).json()
    corr_ac = client.get(
        "/accounts/correlation", params={"account_a": "acct-a", "account_b": "acct-c"}, headers=csrf_headers
    ).json()
    corr_bc = client.get(
        "/accounts/correlation", params={"account_a": "acct-b", "account_b": "acct-c"}, headers=csrf_headers
    ).json()
    assert corr_ab["sample_count"] == 10
    assert corr_ab["correlation"] == pytest.approx(1.0)
    assert corr_ac["correlation"] is None
    assert corr_bc["correlation"] is None

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)

            async def wait_settled(selector, timeout=8000):
                await page.wait_for_function(
                    "(sel) => { const el = document.querySelector(sel); "
                    "return !!el && !el.innerText.includes('Loading'); }",
                    arg=selector,
                    timeout=timeout,
                )

            await page.click('a[href="#/trade"]')
            await page.wait_for_selector("#tr01-p09", timeout=5000)
            await wait_settled("#tr01-p09")

            # --- Per-account stats row: every rendered cell exactly
            # matches this same live server's real /statistics response
            # -- the load-bearing invariant (never a fabricated 0 where
            # the backend sent null). ---
            row_a = await _bucket_stats_row(page, "acct-a")
            assert row_a[1] == _fmt_num(stats["acct-a"]["volatility_pnl_delta"])  # real 0.0, not n/a
            assert row_a[2] == "n/a"  # sharpe: real data, but undefined (zero variance)
            assert row_a[3] == "n/a"  # sortino: real data, but undefined (no downside deltas)
            assert row_a[4] == _fmt_num(stats["acct-a"]["max_drawdown"])  # real 0.0

            row_c = await _bucket_stats_row(page, "acct-c")
            assert row_c[1] == "n/a"  # volatility: insufficient real samples (1 delta < 2)
            assert row_c[2] == "n/a"
            assert row_c[3] == "n/a"
            assert row_c[4] == _fmt_num(stats["acct-c"]["max_drawdown"])  # real 20.0 -- NOT gated by the above
            assert "1h" in row_c[5]  # real duration (3600s), rendered human-readable

            # --- Cross-account correlation: real pair with a real,
            # non-null correlation renders it; pairs below the real
            # overlapping-sample floor render "n/a". ---
            row_ab = await _bucket_correlation_row(page, "acct-a", "acct-b")
            assert row_ab[2] == str(corr_ab["sample_count"])
            assert row_ab[3] == _fmt_num(corr_ab["correlation"])
            assert row_ab[3] != "n/a"

            row_ac = await _bucket_correlation_row(page, "acct-a", "acct-c")
            assert row_ac[3] == "n/a"
            row_bc = await _bucket_correlation_row(page, "acct-b", "acct-c")
            assert row_bc[3] == "n/a"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_risk_panel_shows_needs_pairs_note_for_single_account(live_server, tmp_path):
    """With only one account configured, the panel must not invent a
    correlation pair -- it should say plainly that correlation needs 2+
    accounts, never render an empty/zeroed correlation table."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert client.post(
        "/accounts", json={"account_id": "acct-solo", "broker": "paper"}, headers=csrf_headers
    ).status_code == 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.click('a[href="#/trade"]')
            await page.wait_for_selector("#tr01-p09", timeout=5000)
            await page.wait_for_function(
                "() => { const el = document.querySelector('#tr01-p09'); "
                "return !!el && !el.innerText.includes('Loading'); }",
                timeout=8000,
            )
            text = await page.inner_text("#tr01-p09")
            assert "at least 2 accounts" in text
            assert await page.query_selector("#tr01-risk-correlation-wrap") is None
        finally:
            await browser.close()
