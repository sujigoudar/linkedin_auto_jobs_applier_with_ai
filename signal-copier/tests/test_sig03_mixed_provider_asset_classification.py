"""SIG-03: every text source (Telegram/Discord/Slack/SMS/Twitter) defaults
to (or is configured with) ONE fixed asset_class for every message it ever
produces. A "mixed" channel where different analysts post about different
markets used to have every message tagged with that one default
regardless of what the symbol actually named -- a bare "BUY AAPL 10" in a
channel defaulted to CRYPTO came out tagged CRYPTO, which can pass the
broker asset-class gate for the wrong reason instead of being caught.

Reproduces the audit's exact three cases (test_adapter_research_audit.py's
test_mixed_asset_text_not_silently_classified_crypto) plus the "option
contract digits interpreted as quantity" impact it separately called out.
"""
import pytest

from app.errors import SignalValidationError
from app.models import AssetClass
from app.sources.text_parser import parse_text_signal


@pytest.mark.parametrize(
    "text,expected_symbol,expected_asset_class",
    [
        ("BUY AAPL 10", "AAPL", AssetClass.EQUITY),
        ("BUY EURUSD 1", "EURUSD", AssetClass.FOREX),
        ("BUY AAPL 260918C00200000 1", "AAPL260918C00200000", AssetClass.OPTION),
    ],
)
def test_audits_three_reproduced_cases_are_not_silently_classified_crypto(
    text, expected_symbol, expected_asset_class
):
    """Exact audit assertion: given the DEFAULT asset_class (CRYPTO, since
    no override is passed), the result must not come out CRYPTO."""
    result = parse_text_signal(text, source="mixed-provider")
    assert result.asset_class != AssetClass.CRYPTO
    assert result.symbol == expected_symbol
    assert result.asset_class == expected_asset_class


def test_option_contract_digits_are_not_misread_as_a_huge_quantity():
    """The audit's own stated impact: 'option contract digits can also be
    interpreted as quantity.' Before the fix, 'BUY AAPL 260918C00200000 1'
    parsed as symbol=AAPL, quantity=260918 (the option code's date prefix)
    -- a wildly wrong, unbounded order size derived from a strike/date
    code, not what the trader typed."""
    result = parse_text_signal("BUY AAPL 260918C00200000 1", source="mixed-provider")
    assert result.quantity == 1.0


def test_actual_crypto_symbol_still_classifies_as_crypto():
    result = parse_text_signal("BUY BTCUSDT 0.1", source="mixed-provider")
    assert result.asset_class == AssetClass.CRYPTO
    assert result.symbol == "BTCUSDT"


def test_slash_delimited_crypto_pair_still_classifies_as_crypto():
    result = parse_text_signal("BUY BTC/USDT 0.1", source="mixed-provider")
    assert result.asset_class == AssetClass.CRYPTO


def test_explicit_source_default_still_applies_when_symbol_shape_is_ambiguous():
    """A symbol shape this classifier can't confidently place (too short,
    non-alphabetic, etc.) falls back to whatever asset_class the source was
    actually configured with -- not every source needs its default
    second-guessed, only ones a symbol confidently contradicts."""
    result = parse_text_signal("BUY XYZ123456789 1", source="mixed-provider", asset_class=AssetClass.FOREX)
    assert result.asset_class == AssetClass.FOREX


def test_source_configured_for_forex_still_gets_a_confidently_shaped_equity_overridden():
    """The reverse direction: a channel misconfigured (or genuinely mixed)
    with a FOREX default must not force an obviously-equity-shaped symbol
    into FOREX either -- the shape wins when it's confident, regardless of
    which direction the configured default was wrong in."""
    result = parse_text_signal("BUY AAPL 10", source="mixed-provider", asset_class=AssetClass.FOREX)
    assert result.asset_class == AssetClass.EQUITY


def test_negation_check_still_works_after_option_symbol_merging():
    with pytest.raises(SignalValidationError):
        parse_text_signal("DO NOT BUY AAPL 260918C00200000 1", source="mixed-provider")
