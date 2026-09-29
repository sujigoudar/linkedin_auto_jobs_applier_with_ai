"""TR-03 deepening: the "Result attribution" panel (#tr03-p08) on the
Position and protection detail screen (`#/trade/positions/:account_id/:symbol`).

A design review's single biggest remaining idea was the profitability-
attribution framework "provider theoretical result - latency - admission/
risk filtering - missed/unfilled trades - slippage - fees +- exit-
management effect = copied result". A sibling batch already built the real,
AGGREGATE, per-provider version of the joinable part of this on tr09.js's
"Signal vs. execution" panel (#tr09-p10, see
tests/test_tr09_provider_scorecards_correlation.py's own
`test_tr09_signal_vs_execution_gap_...` test). This file is the same real
join and the SAME sign convention, applied per-position, via
app/static/views/tr03.js's `computeResultAttribution`.

This test is deliberately load-bearing for the sizing-gap step (the
riskiest piece per this batch's brief: `signal.quantity - requested_quantity`,
app/risk.py's `size_for_account`) -- see
`test_sizing_gap_sign_convention_is_load_bearing_broken_branch_would_be_caught`
for the required "break it on purpose, confirm the regression test fails,
restore, confirm green again" verification. That manual step is documented
in the batch's own commit/PR description; this file is the regression test
it exists to validate against.
"""
from __future__ import annotations

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


@pytest.mark.asyncio
async def test_tr03_result_attribution_pure_function_matrix(live_server):
    """Exercises the real, unmodified `computeResultAttribution` function in
    the browser (via `window.Views.tr03._internal`) against a matrix of
    real-shaped inputs -- no DOM, no fetch, just the pure decision logic."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.wait_for_function("() => !!(window.Views && window.Views.tr03 && window.Views.tr03._internal)")

            result = await page.evaluate(
                """() => {
                    const compute = window.Views.tr03._internal.computeResultAttribution;

                    // Case A: a real multiplier<1 sizing decision (signal
                    // asked for 10, the account's own multiplier sized the
                    // request DOWN to 6), a real full fill, real adverse
                    // BUY slippage, a real paper fee, a closed position
                    // with NO stop-tightening/target-hit event.
                    const a = compute({
                        entrySignal: { price: 100.0, stop_loss: 90.0, take_profit: 120.0, quantity: 10.0, side: 'buy' },
                        entryOrder: { id: 1, signal_id: 's1', requested_quantity: 6.0, filled_quantity: 6.0, filled_price: 101.0, status: 'filled' },
                        symbolOrders: [
                            { id: 1, signal_id: 's1', requested_quantity: 6.0, filled_quantity: 6.0, filled_price: 101.0, status: 'filled' },
                            { id: 2, purpose: 'close', status: 'filled', filled_price: 105.0, filled_quantity: 6.0 },
                        ],
                        broker: { name: 'paper', fee_per_fill: 0.5 },
                        symbolEconomics: { realized_pnl: 23.0 },
                        lifecycle: { owned_quantity: 0 },
                        position: { net_quantity: 0 },
                        entrySide: 'buy',
                        stopPlaced: [{ at: '2026-01-01T00:00:00Z', price: 90.0 }],
                        stopTightened: [],
                        targetHits: [],
                        exitFills: [{ filled_price: 105.0, filled_quantity: 6.0 }],
                    });

                    // Case B: a SELL (short) signal, favorable slippage,
                    // no matching broker (not_tracked fee), still open
                    // (not_tracked exit-management).
                    const b = compute({
                        entrySignal: { price: 50.0, stop_loss: 55.0, take_profit: 40.0, quantity: 4.0, side: 'sell' },
                        entryOrder: { id: 3, signal_id: 's2', requested_quantity: 4.0, filled_quantity: 4.0, filled_price: 49.0, status: 'filled' },
                        symbolOrders: [{ id: 3, signal_id: 's2', requested_quantity: 4.0, filled_quantity: 4.0, filled_price: 49.0, status: 'filled' }],
                        broker: null,
                        symbolEconomics: { realized_pnl: 0.0 },
                        lifecycle: { owned_quantity: 4 },
                        position: { net_quantity: -4 },
                        entrySide: 'sell',
                        stopPlaced: [],
                        stopTightened: [],
                        targetHits: [],
                        exitFills: [],
                    });

                    return { a, b };
                }"""
            )

            a = result["a"]
            b = result["b"]

            # --- Case A assertions ---
            sizing_step = next(s for s in a["steps"] if s["id"] == "quantity_gap")
            assert sizing_step["kind"] == "info"
            # signal.quantity(10) - requested_quantity(6) = 4 -- a real,
            # positive "sized DOWN from the signal" figure. THE load-bearing
            # sign convention (see the dedicated test below).
            assert "4" in sizing_step["rows"][0][1]
            assert "sized DOWN" in sizing_step["rows"][0][1]

            slippage_step = next(s for s in a["steps"] if s["id"] == "slippage")
            assert slippage_step["kind"] == "real"
            # BUY: filled_price(101) - signal.price(100) = +1 (adverse).
            assert slippage_step["value"] == pytest.approx(1.0)
            assert slippage_step["dollar"] == pytest.approx(6.0)  # 1.0 * 6 filled

            fees_step = next(s for s in a["steps"] if s["id"] == "fees")
            assert fees_step["kind"] == "real"
            # 0.5/fill * 2 real filled orders (entry + close) = 1.0.
            assert fees_step["value"] == pytest.approx(1.0)

            latency_step = next(s for s in a["steps"] if s["id"] == "latency_cost")
            assert latency_step["kind"] == "capability"
            assert latency_step["capability"]["status"] == "not_tracked"
            assert "decision" in latency_step["capability"]["reason"].lower()

            exit_mgmt_step = next(s for s in a["steps"] if s["id"] == "exit_management")
            # Closed, but zero real STOP_TIGHTENED/TARGET_HIT events --
            # real $0, distinguished from not_tracked.
            assert exit_mgmt_step["kind"] == "real"
            assert exit_mgmt_step["value"] == 0
            assert "no real stop_tightened or target_hit event" in exit_mgmt_step["detail"].lower()

            net_step = next(s for s in a["steps"] if s["id"] == "net_result")
            assert net_step["value"] == pytest.approx(23.0)

            recon = a["reconciliation"]
            # slippage(-1*6=-6) + fees(-1.0) + exit_mgmt(+0) = -7.0 accounted.
            assert recon["accountedFor"] == pytest.approx(-7.0)
            assert recon["total"] == pytest.approx(23.0)
            assert "not_tracked" in recon["note"] or "untracked" in recon["note"].lower()

            # --- Case B assertions ---
            slippage_b = next(s for s in b["steps"] if s["id"] == "slippage")
            # SELL: signal.price(50) - filled_price(49) = +1 (adverse... wait,
            # received LESS than specified would be adverse; here filled
            # price 49 < signal 50, so the trader received LESS on a sell,
            # i.e. adverse, matching tr09's own convention exactly).
            assert slippage_b["value"] == pytest.approx(1.0)

            fees_b = next(s for s in b["steps"] if s["id"] == "fees")
            assert fees_b["kind"] == "capability"
            assert fees_b["capability"]["status"] == "not_tracked"

            exit_mgmt_b = next(s for s in b["steps"] if s["id"] == "exit_management")
            assert exit_mgmt_b["kind"] == "capability"
            assert exit_mgmt_b["capability"]["status"] == "not_tracked"
            assert "still open" in exit_mgmt_b["capability"]["reason"].lower()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_sizing_gap_sign_convention_is_load_bearing_broken_branch_would_be_caught(live_server):
    """LOAD-BEARING per this batch's own brief: a sign or subtraction-order
    bug in the requested-vs-filled quantity gap step could show sizing
    going the wrong direction (e.g. claim the account sized UP when it
    actually sized DOWN). This test locks in the correct real direction:
    `signal.quantity - requested_quantity` (app/risk.py's own
    `size_for_account` convention, matching tr09.js's
    `computeSignalExecutionGap`'s `sizingGaps.push(sig.quantity -
    o.requested_quantity)`)."""
    base_url = live_server
    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        try:
            await page.goto(base_url)
            await page.fill("#login-password", "test-owner-pw")
            await page.click("#login-form button[type=submit]")
            await page.wait_for_selector("#app:not([hidden])", timeout=5000)
            await page.wait_for_function("() => !!(window.Views && window.Views.tr03 && window.Views.tr03._internal)")

            result = await page.evaluate(
                """() => {
                    const compute = window.Views.tr03._internal.computeResultAttribution;
                    // A real seeded scenario: signal asked for 10, the
                    // account's own real multiplier (0.5) sized the
                    // request DOWN to 5 (app/risk.py's size_for_account:
                    // base_quantity * account.multiplier).
                    return compute({
                        entrySignal: { price: 10.0, quantity: 10.0, side: 'buy' },
                        entryOrder: { signal_id: 's1', requested_quantity: 5.0, filled_quantity: 5.0, filled_price: 10.0, status: 'filled' },
                        symbolOrders: [{ signal_id: 's1', requested_quantity: 5.0, filled_quantity: 5.0, filled_price: 10.0, status: 'filled' }],
                        broker: null,
                        symbolEconomics: { realized_pnl: 0.0 },
                        lifecycle: null,
                        position: { net_quantity: 5 },
                        entrySide: 'buy',
                        stopPlaced: [], stopTightened: [], targetHits: [], exitFills: [],
                    });
                }"""
            )
            sizing_step = next(s for s in result["steps"] if s["id"] == "quantity_gap")
            row_text = sizing_step["rows"][0][1]
            # THE real, correct direction: 10 - 5 = +5, "sized DOWN".
            # A broken/inverted implementation (requested - signal, or the
            # opposite label branch) would instead claim "sized UP" here --
            # this assertion is the one this test is written to catch.
            assert "5" in row_text
            assert "sized DOWN" in row_text
            assert "sized UP" not in row_text
        finally:
            await browser.close()
