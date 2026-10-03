"""Direct unit tests for app/risk.py's `size_for_account`/`symbol_for_account`.

Added as part of a mutation-testing pass (track39): neither function had a
dedicated unit test before this -- the only existing coverage was indirect,
through larger integration tests (test_tr09_tr12_trading_screens.py,
test_t17_certification.py, test_tr03_result_attribution.py) that assert on
end-to-end behavior rather than on these two functions' own boundary values.
Mutation testing against app/risk.py (scoped to those three files) found two
real survivors this file now kills:

- `size_for_account`'s `else 1.0` fallback (used when `signal.quantity is
  None`) mutated to `else 2.0` and nothing failed -- no test asserted the
  exact default-quantity value.
- `symbol_for_account`'s `account.symbol_map.get(signal.symbol, ...)` first
  argument mutated to `get(None, ...)` and nothing failed -- no test ever
  exercised a signal symbol that *is* present in `symbol_map` (only the
  pass-through/no-mapping case was ever indirectly covered), so silently
  dropping the real translation went unnoticed.
"""
from __future__ import annotations

import pytest

from app.models import DestinationAccount, Side, Signal
from app.risk import UnsizedEntryError, size_for_account, symbol_for_account


def _signal(**overrides) -> Signal:
    defaults = dict(source="test", symbol="BTCUSD", side=Side.BUY)
    defaults.update(overrides)
    return Signal(**defaults)


def _account(**overrides) -> DestinationAccount:
    defaults = dict(account_id="acct-1", broker="alpaca")
    defaults.update(overrides)
    return DestinationAccount(**defaults)


# -- size_for_account -------------------------------------------------------

def test_fixed_quantity_wins_outright_regardless_of_signal_quantity():
    account = _account(fixed_quantity=3.0, multiplier=5.0)
    signal = _signal(quantity=100.0)
    assert size_for_account(signal, account) == 3.0


def test_unsized_entry_raises_when_signal_has_no_quantity():
    # WP-01: with no fixed_quantity and no signal.quantity, size_for_account
    # must raise UnsizedEntryError, never default to 1.0.
    account = _account(multiplier=1.0)
    signal = _signal(quantity=None)
    with pytest.raises(UnsizedEntryError, match="no quantity"):
        size_for_account(signal, account)


def test_unsized_entry_raises_even_with_multiplier():
    # WP-01: with no fixed_quantity and no signal.quantity, size_for_account
    # raises UnsizedEntryError regardless of the multiplier value.
    account = _account(multiplier=4.0)
    signal = _signal(quantity=None)
    with pytest.raises(UnsizedEntryError):
        size_for_account(signal, account)


def test_signal_quantity_is_scaled_by_multiplier():
    account = _account(multiplier=2.0)
    signal = _signal(quantity=10.0)
    assert size_for_account(signal, account) == 20.0


def test_signal_quantity_with_multiplier_one_is_unchanged():
    account = _account(multiplier=1.0)
    signal = _signal(quantity=7.5)
    assert size_for_account(signal, account) == 7.5


# -- symbol_for_account ------------------------------------------------------

def test_symbol_translation_applies_when_symbol_is_mapped():
    # Kills the `.get(signal.symbol, ...)` -> `.get(None, ...)` mutant:
    # when the signal's symbol IS present in symbol_map, the translated
    # value must be returned, not a silent pass-through of the original.
    account = _account(symbol_map={"BTCUSD": "BTC/USDT"})
    signal = _signal(symbol="BTCUSD")
    assert symbol_for_account(signal, account) == "BTC/USDT"


def test_symbol_passes_through_unmapped_when_absent_from_symbol_map():
    account = _account(symbol_map={"ETHUSD": "ETH/USDT"})
    signal = _signal(symbol="BTCUSD")
    assert symbol_for_account(signal, account) == "BTCUSD"


def test_symbol_passes_through_when_symbol_map_empty():
    account = _account(symbol_map={})
    signal = _signal(symbol="EURUSD")
    assert symbol_for_account(signal, account) == "EURUSD"
