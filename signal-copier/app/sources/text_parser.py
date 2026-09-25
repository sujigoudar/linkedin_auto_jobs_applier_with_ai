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


def parse_text_signal(text: str, *, source: str, asset_class: AssetClass = AssetClass.CRYPTO) -> Signal:
    match = _PATTERN.search(text.strip())
    if not match:
        raise SignalValidationError(f"could not parse a signal out of: {text!r}")

    side = _SIDE_ALIASES[match.group("side").lower()]

    return Signal(
        source=source,
        symbol=match.group("symbol").upper(),
        side=side,
        asset_class=asset_class,
        quantity=_optional_float(match.group("quantity")),
        price=_optional_float(match.group("price")),
        stop_loss=_optional_float(match.group("sl")),
        take_profit=_optional_float(match.group("tp")),
        raw={"text": text},
    )


def _optional_float(value: str | None) -> float | None:
    return float(value) if value is not None else None
