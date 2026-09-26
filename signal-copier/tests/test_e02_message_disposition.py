"""E02 (bounded): app/sources/text_parser.py's classify_text_signal /
classify_batch -- every message gets a parsed/ignored/ambiguous/
missing_data/no_match disposition, not just a pass/fail.
"""
from app.models import Side
from app.sources.text_parser import DispositionOutcome, classify_batch, classify_text_signal


def test_parsed_disposition_carries_the_resolved_signal():
    disposition = classify_text_signal("BUY BTCUSDT @ 65000", source="test")
    assert disposition.outcome == DispositionOutcome.PARSED
    assert disposition.signal is not None
    assert disposition.signal.side == Side.BUY
    assert disposition.signal.symbol == "BTCUSDT"
    assert disposition.signal.price == 65000.0


def test_no_match_disposition_for_unrelated_text():
    disposition = classify_text_signal("just chatting, nothing to trade here", source="test")
    assert disposition.outcome == DispositionOutcome.NO_MATCH
    assert disposition.signal is None


def test_ignored_disposition_for_negated_commentary():
    disposition = classify_text_signal("DO NOT BUY AAPL 10", source="test")
    assert disposition.outcome == DispositionOutcome.IGNORED
    assert disposition.signal is None


def test_ignored_disposition_for_past_tense_reporting():
    disposition = classify_text_signal("Yesterday I said BUY AAPL 10", source="test")
    assert disposition.outcome == DispositionOutcome.IGNORED


def test_ambiguous_disposition_for_compound_instructions():
    disposition = classify_text_signal("BUY AAPL 10 and SELL MSFT 5", source="test")
    assert disposition.outcome == DispositionOutcome.AMBIGUOUS


def test_ambiguous_disposition_for_multiple_take_profit_levels():
    disposition = classify_text_signal("BUY AAPL 10 SL 95 TP1 105 TP2 110", source="test")
    assert disposition.outcome == DispositionOutcome.AMBIGUOUS


def test_missing_data_disposition_for_negative_quantity():
    disposition = classify_text_signal("BUY AAPL -10 shares", source="test")
    assert disposition.outcome == DispositionOutcome.MISSING_DATA
    assert "quantity" in disposition.detail


def test_classify_batch_preserves_order_and_classifies_each_independently():
    texts = [
        "BUY BTCUSDT",
        "just chatting, nothing to trade here",
        "DO NOT BUY AAPL 10",
        "BUY AAPL 10 and SELL MSFT 5",
    ]
    dispositions = classify_batch(texts, source="test")
    assert [d.outcome for d in dispositions] == [
        DispositionOutcome.PARSED,
        DispositionOutcome.NO_MATCH,
        DispositionOutcome.IGNORED,
        DispositionOutcome.AMBIGUOUS,
    ]
    assert [d.text for d in dispositions] == texts
