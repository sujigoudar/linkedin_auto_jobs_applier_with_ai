"""Generic free-text signal parser, shared by every text-message source
(Telegram, Discord, Slack, SMS, Twitter/X).

There's no universal signal-channel format, but the vast majority of manual
trade-call channels use some variant of:

    BUY BTCUSDT
    BUY BTCUSDT @ 65000
    SELL EURUSD 0.50 lots SL 1.0950 TP 1.1050
    LONG AAPL 10 @ 190.25 SL 185 TP 200
    close ETHUSDT

This parser handles that family. If a specific channel uses a format this
doesn't cover, write a dedicated parser for it (see each source's
docstring) rather than fighting this regex into something it isn't.
"""
from __future__ import annotations

import re

from app.errors import SignalValidationError
from app.models import AssetClass, Signal, Side

_SIDE_ALIASES = {
    "buy": Side.BUY,
    "long": Side.BUY,
    "sell": Side.SELL,
    "short": Side.SELL,
    "close": Side.CLOSE,
    "exit": Side.CLOSE,
}

_PATTERN = re.compile(
    r"""
    (?P<side>buy|sell|long|short|close|exit)\s+
    (?P<symbol>[A-Za-z0-9/.\-]+)
    (?:\s+(?P<quantity>\d+(?:\.\d+)?)\s*(?:lots?|units?|shares?)?)?
    (?:\s*@\s*(?P<price>\d+(?:\.\d+)?))?
    (?:.*?\bSL[:=]?\s*(?P<sl>\d+(?:\.\d+)?))?
    (?:.*?\bTP[:=]?\s*(?P<tp>\d+(?:\.\d+)?))?
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# A plain substring match doesn't prove the message is actually giving that
# instruction -- "DO NOT BUY AAPL 10" contains "BUY AAPL 10" too. This is not
# a full parse of meaning (see this module's docstring: dedicated per-source
# parsers exist for that), but a bounded, cheap check that refuses the
# obvious cases of negated or still-conditional commentary rather than
# silently trading on them -- looking at a few words immediately before the
# matched instruction, and anywhere after it, for words that reverse or
# defer it.
_NEGATION_OR_CONDITIONAL_WORDS = {
    "not", "don't", "dont", "doesn't", "doesnt", "didn't", "didnt",
    "won't", "wont", "wouldn't", "wouldnt", "shouldn't", "shouldnt",
    "never", "no", "avoid", "skip", "cancel", "cancelled", "canceled",
    "if", "unless", "maybe", "possibly", "might", "considering", "consider",
    "wait", "waiting", "hold", "holding", "ignore", "disregard",
}
_WORDS_BEFORE_MATCH_TO_CHECK = 4


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text.lower())


def parse_text_signal(
    text: str, *, source: str, asset_class: AssetClass = AssetClass.CRYPTO, analyst: str | None = None
) -> Signal:
    stripped = text.strip()
    match = _PATTERN.search(stripped)
    if not match:
        raise SignalValidationError(f"could not parse a signal out of: {text!r}")

    preceding = _words(stripped[: match.start()])[-_WORDS_BEFORE_MATCH_TO_CHECK:]
    following = _words(stripped[match.end() :])
    if any(w in _NEGATION_OR_CONDITIONAL_WORDS for w in preceding + following):
        raise SignalValidationError(
            f"looks like negated, conditional, or still-pending commentary rather than a trade "
            f"instruction, refusing to admit it: {text!r}"
        )

    side = _SIDE_ALIASES[match.group("side").lower()]

    return Signal(
        source=source,
        symbol=match.group("symbol").upper(),
        side=side,
        asset_class=asset_class,
        analyst=analyst,
        quantity=_optional_float(match.group("quantity")),
        price=_optional_float(match.group("price")),
        stop_loss=_optional_float(match.group("sl")),
        take_profit=_optional_float(match.group("tp")),
        raw={"text": text},
    )


def _optional_float(value: str | None) -> float | None:
    return float(value) if value is not None else None
