"""Track 9: article classification (app/sources/article_classifier.py) --
one fixture per category, a deliberately ambiguous fixture that must
land on NEEDS_HUMAN_REVIEW (never a guess), and multi-leg options
preservation: a fixture spread with one unresolved leg must stay
unresolved, never partially executed."""
from __future__ import annotations

from app.models import Side
from app.sources.article_classifier import (
    ArticleClassification,
    build_trade_candidate,
    classify_article,
)

_ACTIONABLE_TEXT = (
    "We are buying XYZ today right here at the pivot after it cleared a clean base "
    "on strong volume. This is a real trade we are putting on now, with a stop at $48.00 "
    "and a target of $62.00."
)

_CONDITIONAL_TEXT = (
    "XYZ is still below its ideal entry, but if it breaks above $55 on strong volume, "
    "consider buying the breakout. Watch for a move above the pivot before acting."
)

_EDUCATIONAL_TEXT = (
    "For example, let's say a stock breaks out of a cup-with-handle base on heavy volume. "
    "In this example, a trader might consider the breakout point as a reference for a buy point, "
    "but this is purely for instruction, not a live signal."
)

_HISTORICAL_TEXT = (
    "We bought XYZ last week near its pivot point. In the past, similar breakouts from this "
    "base pattern have worked well for our portfolio, and looking back, this trade played out "
    "largely as expected."
)

_ADJUSTMENT_TEXT = (
    "With XYZ up sharply from our entry, we are raising our stop to lock in gains and taking "
    "partial profits into strength. We are trimming the position by a third here."
)

_GENERAL_COMMENTARY_TEXT = (
    "The broader market continued to show distribution this week, with the major indexes "
    "struggling to hold recent gains amid mixed economic data and rising rates."
)

# Deliberately ambiguous: both a present-tense action phrase AND a
# past-tense historical phrase are present, with nothing to prioritize
# one story over the other -- this must NOT be guessed either way.
_AMBIGUOUS_TEXT = (
    "We are buying XYZ today, much like we bought ABC last week which had a similar setup. "
    "In the past, this pattern has been a reliable one for the desk."
)


def test_actionable_recommendation_classified_correctly():
    result = classify_article(_ACTIONABLE_TEXT)
    assert result.classification is ArticleClassification.ACTIONABLE_RECOMMENDATION
    assert result.matched_phrases


def test_conditional_setup_classified_correctly():
    result = classify_article(_CONDITIONAL_TEXT)
    assert result.classification is ArticleClassification.CONDITIONAL_SETUP


def test_educational_example_classified_correctly():
    result = classify_article(_EDUCATIONAL_TEXT)
    assert result.classification is ArticleClassification.EDUCATIONAL_EXAMPLE


def test_historical_recap_classified_correctly():
    result = classify_article(_HISTORICAL_TEXT)
    assert result.classification is ArticleClassification.HISTORICAL_RECAP


def test_adjustment_or_exit_classified_correctly():
    result = classify_article(_ADJUSTMENT_TEXT)
    assert result.classification is ArticleClassification.ADJUSTMENT_OR_EXIT


def test_general_commentary_classified_correctly():
    result = classify_article(_GENERAL_COMMENTARY_TEXT)
    assert result.classification is ArticleClassification.GENERAL_COMMENTARY


def test_ambiguous_article_holds_for_human_review_never_guessed():
    result = classify_article(_AMBIGUOUS_TEXT)
    assert result.classification is ArticleClassification.NEEDS_HUMAN_REVIEW


def test_empty_text_holds_for_human_review():
    result = classify_article("")
    assert result.classification is ArticleClassification.NEEDS_HUMAN_REVIEW


def test_actionable_equity_trade_builds_a_resolved_candidate():
    classification = classify_article(_ACTIONABLE_TEXT)
    candidate = build_trade_candidate(
        _ACTIONABLE_TEXT,
        channel_id="site:example",
        message_id="https://example.com/articles/we-are-buying-xyz",
        classification=classification,
        symbol_hint="XYZ",
    )
    assert candidate is not None
    assert candidate.resolved is True
    assert candidate.symbol == "XYZ"
    assert candidate.side is Side.BUY
    assert candidate.stop_loss == 48.0
    assert candidate.targets and candidate.targets[0].price == 62.0

    signal = candidate.to_signal(source="website")
    assert signal is not None
    assert signal.channel_id == "site:example"
    assert signal.message_id == "https://example.com/articles/we-are-buying-xyz"


_MULTI_LEG_SPREAD_TEXT = (
    "We are buying a bull call spread on XYZ today: buying the $50 call and selling the $55 call, "
    "for a net debit of $2.10 per spread."
)

_MULTI_LEG_SPREAD_WITH_UNRESOLVED_LEG_TEXT = (
    "We are buying a call spread on XYZ today, buying the $50 call as the long leg of the spread, "
    "with the short leg struck higher up, for a net debit of $2.10 per spread."
)


def test_multi_leg_spread_with_both_legs_resolved_still_not_auto_routed_without_expiry():
    """Even with both strikes/directions found, this first-pass prose
    reader never resolves an expiry from free text (see
    build_trade_candidate's own docstring) -- so a 2-leg spread is
    correctly held unresolved rather than guessed into a live order."""
    classification = classify_article(_MULTI_LEG_SPREAD_TEXT)
    assert classification.classification is ArticleClassification.ACTIONABLE_RECOMMENDATION
    candidate = build_trade_candidate(
        _MULTI_LEG_SPREAD_TEXT,
        channel_id="site:example",
        message_id="https://example.com/articles/spread",
        classification=classification,
        symbol_hint="XYZ",
    )
    assert candidate is not None
    assert len(candidate.option_legs) == 2
    assert candidate.resolved is False
    assert candidate.to_signal(source="website") is None


def test_multi_leg_spread_with_one_unresolved_leg_stays_unresolved_not_partially_executed():
    classification = classify_article(_MULTI_LEG_SPREAD_WITH_UNRESOLVED_LEG_TEXT)
    candidate = build_trade_candidate(
        _MULTI_LEG_SPREAD_WITH_UNRESOLVED_LEG_TEXT,
        channel_id="site:example",
        message_id="https://example.com/articles/spread-partial",
        classification=classification,
        symbol_hint="XYZ",
    )
    assert candidate is not None
    # One leg was extractable (the $50 call), the other leg's strike was
    # never given a number this reader can match -- the candidate must
    # hold the WHOLE spread unresolved, never emit an order for the one
    # leg it did understand.
    assert any(not leg.is_resolved for leg in candidate.option_legs)
    assert candidate.resolved is False
    assert candidate.unresolved_reason is not None
    assert candidate.to_signal(source="website") is None


def test_adjustment_or_exit_article_never_produces_a_fresh_entry_candidate():
    classification = classify_article(_ADJUSTMENT_TEXT)
    candidate = build_trade_candidate(
        _ADJUSTMENT_TEXT,
        channel_id="site:example",
        message_id="https://example.com/articles/adjustment",
        classification=classification,
        symbol_hint="XYZ",
    )
    assert candidate is None
