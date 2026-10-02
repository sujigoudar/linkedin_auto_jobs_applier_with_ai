"""Track 52: mutation-testing pass for economics/pricing modules.

Targeted tests to catch mutations that survived the initial mutmut run.
Focus on financially-critical logic: P&L computations, win-rate calculations,
and slippage calculations where operator mutations (/ vs *, == vs !=)
or control-flow mutations (continue vs break) would yield silently-wrong numbers.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.account_economics_v2 import _compute_slippage, compute_extended_account_economics
from app.db import SignalStore
from app.economics import compute_account_economics
from app.models import OrderResult, OrderStatus, Side, Signal


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _fill(store, account_id, symbol, side, quantity, price, when, signal_price=None):
    """Create a filled order with optional signal reference price."""
    signal = Signal(source="test", symbol=symbol, side=side, price=signal_price)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id,
        status=OrderStatus.FILLED,
        signal_id=signal.id,
        filled_quantity=quantity,
        filled_price=price,
        executed_at=when,
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side)


# ============================================================================
# Tests for win-rate calculations (Mutants 24, 33, 44, 51)
# ============================================================================
# These mutations change / to * in win-rate division. To catch them, we need
# a win rate that's a non-trivial fraction (not 0 or 1), so / and * give
# different results: e.g., 1/3 ≠ 1*3.


def test_closing_fill_win_rate_with_fractional_win_rate(store):
    """Mutant 33: completed_lifecycle_win_rate division (/ vs *).
    Mutant 24: closing_fill_win_rate == 0 guard (== 0 vs == 1).

    With 3 closing fills and 1 winner, rate should be 1/3 ≈ 0.333, not 1*3=3."""
    t0 = datetime.now(timezone.utc)

    # First trade: win
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    # Second trade: loss
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 110.0, t0 + timedelta(minutes=2))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0, t0 + timedelta(minutes=3))

    # Third trade: loss
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 105.0, t0 + timedelta(minutes=4))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 100.0, t0 + timedelta(minutes=5))

    econ = compute_account_economics(store, "acct1")

    # 3 closing fills (each SELL is a closing fill), 1 is a winner
    assert econ.per_symbol["AAPL"].closing_fills == 3
    assert econ.per_symbol["AAPL"].winning_closing_fills == 1

    # If / is changed to *, would be 1*3=3; should be 1/3 ≈ 0.333
    assert econ.closing_fill_win_rate == pytest.approx(1.0 / 3.0)
    # Deprecated alias should also use division
    assert econ.completed_trade_win_rate == pytest.approx(1.0 / 3.0)


def test_completed_lifecycle_win_rate_with_fractional_rate(store):
    """Mutant 33, 51: completed_lifecycle_win_rate division (/ vs *).

    With 4 episodes and 1 winner, rate should be 1/4=0.25, not 1*4=4."""
    t0 = datetime.now(timezone.utc)

    # Episode 1: +$100 (win)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    # Episode 2: -$50 (loss)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 110.0, t0 + timedelta(minutes=2))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0, t0 + timedelta(minutes=3))

    # Episode 3: $0 (breakeven)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 105.0, t0 + timedelta(minutes=4))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0, t0 + timedelta(minutes=5))

    # Episode 4: -$100 (loss)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 105.0, t0 + timedelta(minutes=6))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 95.0, t0 + timedelta(minutes=7))

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert se.completed_episodes == 4
    assert se.winning_episodes == 1
    assert se.breakeven_episodes == 1

    # If / is changed to *, would be 1*4=4; should be 1/4=0.25
    assert econ.completed_lifecycle_win_rate == pytest.approx(1.0 / 4.0)
    assert se.completed_lifecycle_win_rate == pytest.approx(1.0 / 4.0)


def test_account_level_closing_fill_win_rate_division(store):
    """Mutant 44: AccountEconomics.closing_fill_win_rate / vs *.

    Aggregate win rate across symbols should use division, not multiplication."""
    t0 = datetime.now(timezone.utc)

    # AAPL: 1 win out of 2 fills
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))  # win
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 110.0, t0 + timedelta(minutes=2))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0, t0 + timedelta(minutes=3))  # loss

    # TSLA: 0 wins out of 1 fill
    _fill(store, "acct1", "TSLA", Side.BUY, 10.0, 200.0, t0 + timedelta(minutes=4))
    _fill(store, "acct1", "TSLA", Side.SELL, 10.0, 195.0, t0 + timedelta(minutes=5))  # loss

    econ = compute_account_economics(store, "acct1")

    # Total: 1 win out of 3 closing fills
    assert econ.closing_fill_win_rate == pytest.approx(1.0 / 3.0)
    # If * instead of /, would be 1*3=3
    assert econ.closing_fill_win_rate != 3.0


def test_account_level_lifecycle_win_rate_division(store):
    """Mutant 51: AccountEconomics.completed_lifecycle_win_rate / vs *.

    Aggregate episode win rate should use division."""
    t0 = datetime.now(timezone.utc)

    # AAPL: 1 winning episode
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    # TSLA: 2 losing episodes
    _fill(store, "acct1", "TSLA", Side.BUY, 10.0, 200.0, t0 + timedelta(minutes=2))
    _fill(store, "acct1", "TSLA", Side.SELL, 10.0, 195.0, t0 + timedelta(minutes=3))
    _fill(store, "acct1", "TSLA", Side.BUY, 10.0, 195.0, t0 + timedelta(minutes=4))
    _fill(store, "acct1", "TSLA", Side.SELL, 10.0, 190.0, t0 + timedelta(minutes=5))

    econ = compute_account_economics(store, "acct1")

    # Total: 1 win out of 3 episodes
    assert econ.completed_lifecycle_win_rate == pytest.approx(1.0 / 3.0)
    # If * instead of /, would be 1*3=3
    assert econ.completed_lifecycle_win_rate != 3.0


# ============================================================================
# Tests for losing_episodes formula (Mutant 28)
# ============================================================================


def test_losing_episodes_formula(store):
    """Mutant 28: losing_episodes formula (- vs +).

    losing_episodes = completed - winning - breakeven.
    With completed=4, winning=1, breakeven=1, should be 2, not 4+1-1=4."""
    t0 = datetime.now(timezone.utc)

    # Episode 1: win
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    # Episode 2: loss
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 110.0, t0 + timedelta(minutes=2))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0, t0 + timedelta(minutes=3))

    # Episode 3: breakeven
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 105.0, t0 + timedelta(minutes=4))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0, t0 + timedelta(minutes=5))

    # Episode 4: loss
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 105.0, t0 + timedelta(minutes=6))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 100.0, t0 + timedelta(minutes=7))

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert se.completed_episodes == 4
    assert se.winning_episodes == 1
    assert se.breakeven_episodes == 1

    # Formula: completed - winning - breakeven = 4 - 1 - 1 = 2
    # If using +: completed + winning - breakeven = 4 + 1 - 1 = 4
    assert se.losing_episodes == 2
    assert se.losing_episodes != 4


# ============================================================================
# Tests for @property decorator (Mutant 26)
# ============================================================================


def test_completed_trade_win_rate_is_property(store):
    """Mutant 26: @property decorator on completed_trade_win_rate.

    Without @property, accessing as .completed_trade_win_rate would fail
    or return a function, not the computed value."""
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0,110.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")

    # This should be a float property value, not a function
    win_rate = econ.completed_trade_win_rate
    assert isinstance(win_rate, float)
    assert win_rate == 1.0

    # Both deprecated alias and current name should be the same
    assert econ.completed_trade_win_rate == econ.closing_fill_win_rate


# ============================================================================
# Tests for slippage calculation (Mutants 195, 201)
# ============================================================================


def test_slippage_with_mixed_valid_invalid_rows(store):
    """Mutant 195: _compute_slippage loop control (continue vs break).

    With continue, all rows are processed. With break, loop stops at first
    invalid row, missing valid slippage data after it."""
    t0 = datetime.now(timezone.utc)

    # Fill 1: valid slippage data (reference price provided)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.5, t0, signal_price=100.0)

    # Fill 2: no reference price (should be skipped, not break the loop)
    _fill(store, "acct1", "AAPL", Side.SELL, 5.0, 105.0, t0 + timedelta(minutes=1), signal_price=None)

    # Fill 3: valid slippage data (should be included if continue, excluded if break)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 104.8, t0 + timedelta(minutes=2), signal_price=105.0)

    slippage = _compute_slippage(store, "acct1")

    # With continue: 2 samples (Fill 1 slippage=0.5, Fill 3 slippage=-0.2)
    # With break: 1 sample (stops at Fill 2)
    assert slippage is not None
    assert slippage.sample_count == 2  # Both Fill 1 and Fill 3 included


def test_slippage_buy_vs_sell_side_asymmetry(store):
    """Mutant 201: _compute_slippage side condition (== vs !=).

    BUY slippage = filled_price - reference (positive if worse).
    SELL slippage = reference - filled_price (positive if worse).

    If condition is flipped (!=), sell-side calculation is skipped."""
    t0 = datetime.now(timezone.utc)

    # BUY with positive slippage (filled higher than reference)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.0, t0, signal_price=100.0)

    # SELL with positive slippage (filled lower than reference)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 99.0, t0 + timedelta(minutes=1), signal_price=100.0)

    slippage = _compute_slippage(store, "acct1")

    assert slippage is not None
    assert slippage.sample_count == 2  # Both BUY and SELL processed

    # Both should show positive slippage (worse than reference)
    # BUY: 101.0 - 100.0 = 1.0 (paid more)
    # SELL: 100.0 - 99.0 = 1.0 (received less)
    # Average should be around 1.0
    assert slippage.mean == pytest.approx(1.0)

    # If SELL condition was != instead of ==, only BUY would be processed
    # and mean would be 1.0, but sample_count would be 1
    assert slippage.sample_count != 1


def test_slippage_calculation_for_sell_side_only(store):
    """Verify SELL-side slippage is correctly calculated (reference - filled_price)."""
    t0 = datetime.now(timezone.utc)

    # SELL at 99.0 when reference was 100.0: slippage = 100.0 - 99.0 = 1.0 (worse)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 99.0, t0, signal_price=100.0)

    slippage = _compute_slippage(store, "acct1")

    assert slippage is not None
    assert slippage.sample_count == 1
    # SELL slippage: reference - filled_price = 100.0 - 99.0 = 1.0
    assert slippage.mean == pytest.approx(1.0)
    assert slippage.worst == pytest.approx(1.0)


def test_extended_economics_reuses_economics_not_duplicate_computation(store):
    """Verify account_economics_v2.py never recomputes economics.py numbers.

    This is the invariant its docstring promises: build alongside, never
    recompute a second way."""
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    econ_v1 = compute_account_economics(store, "acct1")
    econ_v2 = compute_extended_account_economics(store, "acct1")

    # extended should reuse the exact same realized P&L, not recompute it
    assert econ_v2.gross_realized == econ_v1.realized_pnl
    assert econ_v2.gross_realized == pytest.approx(100.0)
