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

import enum
import re
from dataclasses import dataclass
from typing import Any

from app.errors import SignalValidationError
from app.models import AssetClass, Intent, OptionContractSpec, ProfitTarget, Signal, Side  # noqa: F401

#: This parser's own exact interpretation implementation -- see
#: `Signal.parser_version`'s own docstring. Bump whenever this grammar's
#: field-by-field reading of message text changes in a way that would
#: matter to a consumer replaying a past raw message.
PARSER_VERSION = "text-parser-v2-multi-target"

_SIDE_ALIASES = {
    "buy": Side.BUY,
    "long": Side.BUY,
    "sell": Side.SELL,
    "short": Side.SELL,
    "close": Side.CLOSE,
    "exit": Side.CLOSE,
    "trim": Side.CLOSE,
    "reduce": Side.CLOSE,
    "take": Side.CLOSE,  # for "take profit on"
}

#: Symbols that are stop-words and should never be treated as an instrument
_SYMBOL_STOP_WORDS = {
    "TO", "HALF", "ALL", "AT", "THE", "A", "AND", "OPEN", "CLOSE", "NOW"
}

#: Keywords that indicate a REDUCE intent (trim/reduce/take profit on)
_REDUCE_KEYWORDS = {"trim", "reduce", "take"}  # "take" for "take profit on"

#: Pattern to extract reduce_fraction from text like "half", "all", or "25%"
_REDUCE_FRACTION_PATTERN = re.compile(
    r"\b(?:half|all|\d+(?:\.\d+)?%)", re.IGNORECASE
)

#: SIG-XX (this pass): a bare `-?\d+(?:\.\d+)?` stops matching at the first
#: character it doesn't recognize -- for "1,000" that means it captures
#: only "1" and leaves ",000" dangling in the surrounding text, unmatched
#: and silently discarded. That isn't "1,000" parsed as one lot, it's a
#: 1000x undersized (or, if the trader meant a European decimal comma,
#: differently-wrong) quantity slipping through as a clean PARSED result.
#: Capture the WHOLE digit run (comma included) so classify_text_signal
#: can see the comma and refuse the message outright -- a comma inside a
#: number is inherently ambiguous between a thousands separator ("1,000"
#: = one thousand) and a decimal separator ("1,5" = one and a half) with
#: no locale hint anywhere in this free-text grammar, so guessing either
#: reading risks silently sizing an order 1000x off. Refuse rather than
#: guess, same as every other ambiguity this parser already declines to
#: resolve.
_NUMBER = r"-?\d[\d,]*(?:\.\d+)?"

_PATTERN = re.compile(
    r"""
    (?P<side>buy|sell|long|short|close|exit|trim|reduce|take)\s+
    (?:\s*(?:half|all|\d+(?:\.\d+)?%)\s+)?  # Optional reduce fraction (half/all/N%) - not captured, just skipped
    (?P<symbol>[A-Za-z0-9/.\-]+)
    (?:\s+(?P<quantity>""" + _NUMBER + r""")\s*(?:lots?|units?|shares?)?)?
    (?:\s*@\s*(?P<price>""" + _NUMBER + r"""))?
    (?:.*?\bSL[:=]?\s*(?P<sl>""" + _NUMBER + r"""))?
    (?:.*?\bTP[:=]?\s*(?P<tp>""" + _NUMBER + r"""))?
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# SIG-02: more than one side keyword in the same message means this is a
# compound instruction (two distinct trades in one message -- "BUY AAPL 10
# and SELL MSFT 5") -- picking just the first one and silently discarding
# the rest would trade on less than what the message actually said. Refuse
# rather than guess.
_SIDE_WORD_PATTERN = re.compile(r"\b(?:buy|sell|long|short|close|exit|trim|reduce|take)\b", re.IGNORECASE)

#: Every TP mention in the message, each with its own optional level number
#: (bare "TP"/"TP:" has an empty `num`; "TP1", "TP2", ... carry one) and its
#: own value. A multi-target instruction ("TP1 105 TP2 110") is now
#: REPRESENTABLE (see `_resolve_take_profit_targets` below) -- this no
#: longer forces every multi-TP message into AMBIGUOUS, only the genuinely
#: unparseable/inconsistent ones (bare repeated TP with no level numbers to
#: order by, or level numbers that aren't a clean 1..n sequence).
_TP_ENTRY_PATTERN = re.compile(r"\bTP(?P<num>\d*)[:=]?\s*(?P<val>" + _NUMBER + r")", re.IGNORECASE)

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
    # SIG-02: past-tense reporting of someone else's instruction ("Yesterday
    # I said BUY AAPL 10") is a description of a signal, not the signal
    # itself.
    "yesterday", "said",
}
_WORDS_BEFORE_MATCH_TO_CHECK = 4


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text.lower())


# SIG-03: a trader posting an OCC-style option contract sometimes puts a
# space between the underlying ticker and the date/type/strike code (e.g.
# "BUY AAPL 260918C00200000 1"). Left alone, the main pattern's symbol
# group only captures the ticker ("AAPL") and the option code's leading
# digits get swallowed by the quantity group instead -- "260918" units of
# AAPL is not a real instruction, and treating it as one would size an
# order off a date/strike code rather than what the trader actually typed.
# Splice the two tokens back into one contiguous symbol before the main
# pattern ever runs, so an option contract parses as one instrument with
# its own real quantity after it, same as it would if posted with no
# space at all.
_OPTION_FRAGMENT_AFTER_SYMBOL = re.compile(r"\b(?P<sym>[A-Za-z]{1,6})\s+(?P<optfrag>\d{6}[CP]\d{8})\b")


def _merge_split_option_symbols(text: str) -> str:
    return _OPTION_FRAGMENT_AFTER_SYMBOL.sub(lambda m: f"{m.group('sym')}{m.group('optfrag')}", text)


def _is_valid_symbol(symbol: str) -> bool:
    """WP-12: validate that a symbol is not purely numeric or a stop-word."""
    if symbol.isdigit():
        return False
    if symbol.upper() in _SYMBOL_STOP_WORDS:
        return False
    return True


def _try_parse_option_contract(symbol: str, text_after_symbol: str) -> tuple[str, str, float, str] | None:
    """WP-12: try to parse an option contract from symbol and following text.

    Returns (underlying, expiry_str, strike) if successful, None otherwise.
    Handles patterns like:
    - "AAPL 150C 1/17" -> ("AAPL", "2026-01-17", 150.0)
    - "AAPL 150C" -> (None - incomplete)
    - "AAPL 150call 1/17" -> ("AAPL", "2026-01-17", 150.0)
    """
    # Extract any existing OCC-style notation from symbol (e.g., "AAPL260118C00150000")
    if _OPTION_SYMBOL_PATTERN.match(symbol):
        # Already OCC format, don't re-parse
        return None

    # Check if symbol is a plain ticker (letters only)
    if not symbol.replace("/", "").replace("-", "").isalpha():
        return None

    underlying = symbol

    # Look for strike + call/put + expiry in the following text
    # Pattern: number(optional decimal) followed by C or P (case-insensitive), then call/put word
    strike_pattern = r"(?P<strike>\d+(?:\.\d+)?)\s*(?P<right>[CP]|call|put)\b"
    strike_match = re.search(strike_pattern, text_after_symbol, re.IGNORECASE)

    if not strike_match:
        return None

    try:
        strike = float(strike_match.group("strike"))
    except (ValueError, AttributeError):
        return None

    right_str = strike_match.group("right").lower()
    if right_str not in ("c", "call", "p", "put"):
        return None
    right = "call" if right_str in ("c", "call") else "put"

    # Look for expiry after the strike pattern
    # Try to find M/D, M/D/YY, or ISO date formats
    text_after_strike = text_after_symbol[strike_match.end():]

    # Common patterns: M/D, M/D/YY, ISO date
    expiry_patterns = [
        (r"(\d{1,2})/(\d{1,2})/(\d{2,4})", "mdy"),  # M/D/YY or M/D/YYYY
        (r"(\d{1,2})/(\d{1,2})(?:\D|$)", "md"),      # M/D (with lookahead to ensure not followed by digit)
        (r"(\d{4})-(\d{2})-(\d{2})", "iso"),         # ISO format
    ]

    expiry_str = None
    for pattern, fmt in expiry_patterns:
        m = re.search(pattern, text_after_strike)
        if m:
            if fmt == "mdy":
                month, day, year = m.groups()
                year_int = int(year)
                # Handle 2-digit year: 00-99 -> 2000-2099
                if year_int < 100:
                    year_int += 2000
                expiry_str = f"{year_int:04d}-{int(month):02d}-{int(day):02d}"
            elif fmt == "md":
                month, day = m.groups()
                # Assume current or next year
                import datetime
                today = datetime.date.today()
                year = today.year
                try:
                    test_date = datetime.date(year, int(month), int(day))
                    if test_date < today:
                        year += 1
                    expiry_str = f"{year:04d}-{int(month):02d}-{int(day):02d}"
                except ValueError:
                    continue
            elif fmt == "iso":
                year, month, day = m.groups()
                expiry_str = f"{year}-{month}-{day}"
            break

    if expiry_str is None:
        # No expiry found
        return None

    return (underlying, expiry_str, strike, right)


# SIG-03: every text source defaults to (or is configured with) ONE fixed
# asset_class for every message it ever produces -- fine for a genuinely
# single-market channel, wrong for a "mixed" one where different analysts
# post about different markets. A bare "BUY AAPL 10" in a channel
# configured/defaulted to CRYPTO used to come out tagged CRYPTO regardless,
# which can pass the broker asset-class gate for the WRONG reason (routed
# to a crypto venue that "succeeds" against a similarly-named but wrong
# instrument) rather than being caught. This is a best-effort classifier
# from the symbol's own shape alone -- no market data, no per-venue lookup
# -- confident enough to override an assumed default when the two
# disagree, but never confident enough to invent an asset class for a
# shape it doesn't recognize (see `_infer_asset_class`'s return of None).
_OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

_FX_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNH", "CNY",
    "HKD", "SGD", "SEK", "NOK", "DKK", "MXN", "ZAR", "TRY", "PLN", "HUF",
    "CZK", "THB", "INR",
}

_CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "TUSD", "DAI")
_CRYPTO_BASE_HINTS = {
    "BTC", "ETH", "XRP", "LTC", "BCH", "BNB", "SOL", "ADA", "DOGE", "DOT",
    "MATIC", "AVAX", "LINK", "TRX", "XLM", "ATOM",
}


def _infer_asset_class(symbol: str) -> AssetClass | None:
    """Best-effort instrument classification from the symbol's shape alone.
    WP-12: Never infers FUTURE or CRYPTO from shape alone; keeps the source-
    declared class when uncertain. Returns None when the shape doesn't
    confidently match any known convention (e.g. a bare 3-letter string could
    be a stock ticker or half of a currency pair) -- the caller must not
    guess further in that case, only fall back to whatever asset_class it
    already trusted."""
    bare = symbol.replace("/", "").upper()

    if _OPTION_SYMBOL_PATTERN.match(bare):
        return AssetClass.OPTION

    if (
        len(bare) == 6
        and bare[:3] in _FX_CURRENCY_CODES
        and bare[3:] in _FX_CURRENCY_CODES
        and bare[:3] != bare[3:]
    ):
        return AssetClass.FOREX

    # WP-12: Never infer CRYPTO from symbol shape alone
    # if bare.endswith(_CRYPTO_QUOTE_SUFFIXES):
    #     for suffix in _CRYPTO_QUOTE_SUFFIXES:
    #         if bare.endswith(suffix) and len(bare) > len(suffix):
    #             return AssetClass.CRYPTO
    # for base in _CRYPTO_BASE_HINTS:
    #     if bare.startswith(base) and bare[len(base):] in ("USD", "EUR", "GBP", "BTC", "ETH"):
    #         return AssetClass.CRYPTO

    if bare.isalpha() and 1 <= len(bare) <= 5:
        return AssetClass.EQUITY

    return None


class DispositionOutcome(str, enum.Enum):
    """E02 (bounded, adoption plan): every message this parser looks at
    gets one of these outcomes, not just a pass/fail -- see
    `classify_text_signal`'s docstring. Distinct from `Side` and every
    other enum in this codebase; this classifies the MESSAGE, not a
    resolved trade."""

    #: A real, resolved trade instruction was extracted.
    PARSED = "parsed"
    #: Recognized as negated, conditional, or past-tense commentary about
    #: a trade rather than the trade itself -- correctly not a signal,
    #: not a parser failure.
    IGNORED = "ignored"
    #: Looked like an instruction but couldn't be resolved to exactly one
    #: unambiguous trade (more than one side keyword, more than one
    #: take-profit level).
    AMBIGUOUS = "ambiguous"
    #: An instruction was recognized but a numeric field was structurally
    #: invalid (e.g. a truncated/negative quantity or level) -- distinct
    #: from AMBIGUOUS: the shape is clear, one specific value is not.
    MISSING_DATA = "missing_data"
    #: Nothing this grammar recognizes as any kind of trade instruction.
    NO_MATCH = "no_match"


@dataclass
class MessageDisposition:
    text: str
    outcome: DispositionOutcome
    signal: Signal | None = None
    detail: str | None = None


@dataclass
class _TpLevel:
    """One raw TP mention found by `_TP_ENTRY_PATTERN`, resolved to its
    final target ordinal -- `label` is that ordinal as a string ("1",
    "2", ... or "" for a single unlabeled TP), `value` is its raw
    (comma/zero-unvalidated) numeric text."""

    label: str
    value: str


def _resolve_take_profit_targets(stripped: str) -> str | tuple[str | None, list[_TpLevel]]:
    """Resolves every TP mention in the message to either a genuine
    multi-target ordered collection or a single primary take-profit --
    returning a `str` (the AMBIGUOUS detail message) when the message's
    own TP labeling doesn't unambiguously determine an order.

    - No TP mention: `(None, [])`.
    - Exactly one TP mention (labeled or not): `(that value, [that level])`
      -- unchanged from this parser's previous single-TP behavior.
    - Two or more TP mentions, each carrying a distinct level number that
      forms a clean `1..n` sequence (e.g. "TP1 105 TP2 110", in any word
      order): REPRESENTABLE -- returns them sorted into target order,
      `take_profit` set to TP1's own value (the back-compat primary/
      first-target convention -- see `Signal.take_profit`'s own
      docstring).
    - Two or more TP mentions that are NOT a clean `1..n` sequence (a bare
      repeated "TP" with no level numbers to order by, a repeated same
      number, or numbers that skip/aren't sequential): genuinely
      ambiguous -- this grammar has no reliable way to determine which
      mention is "first", so it refuses rather than guessing, same as
      before this multi-target capability existed."""
    entries = list(_TP_ENTRY_PATTERN.finditer(stripped))
    if not entries:
        return (None, [])
    if len(entries) == 1:
        entry = entries[0]
        level = _TpLevel(label=entry.group("num") or "", value=entry.group("val"))
        return (level.value, [level])

    nums = [entry.group("num") for entry in entries]
    if any(n == "" for n in nums):
        return "more than one take-profit level, but not consistently numbered (TP1, TP2, ...) -- can't determine target order"
    ints = [int(n) for n in nums]
    if len(set(ints)) != len(ints) or sorted(ints) != list(range(1, len(ints) + 1)):
        return "take-profit levels are not a clean TP1, TP2, ... sequence -- can't determine target order"

    ordered = sorted(zip(ints, entries, strict=True), key=lambda pair: pair[0])
    targets_raw = [_TpLevel(label=str(num), value=entry.group("val")) for num, entry in ordered]
    return (targets_raw[0].value, targets_raw)


def _determine_intent_and_reduce_fraction(side: Side, text: str) -> tuple[Intent | None, float | None]:
    """WP-08: determine intent and reduce_fraction from the side and text.

    Maps side keywords to intent values and extracts reduce_fraction from
    "half", "all", or "N%" patterns when a reduce verb is detected.

    Returns (intent, reduce_fraction) where intent can be None (to be derived
    in Signal.__post_init__) and reduce_fraction is None unless the message
    indicates a partial reduction.
    """
    # Map side to intent
    intent = None
    if side == Side.BUY:
        intent = Intent.ENTRY_LONG
    elif side == Side.SELL:
        # "short" keyword maps to ENTRY_SHORT intent
        if "short" in text.lower():
            intent = Intent.ENTRY_SHORT
        else:
            # "sell" by itself is SELL intent (ambiguous, resolved by engine)
            intent = Intent.SELL
    elif side == Side.CLOSE:
        # "close"/"exit"/"flat" → EXIT intent, but could be REDUCE if reducing
        text_lower = text.lower()
        if any(kw in text_lower for kw in _REDUCE_KEYWORDS):
            intent = Intent.REDUCE
        else:
            intent = Intent.EXIT

    # Extract reduce_fraction for REDUCE intents or CLOSE with a fraction
    reduce_fraction = None
    text_lower = text.lower()

    # Look for reduce fraction indicators ("half", "all", or "N%")
    if "half" in text_lower:
        reduce_fraction = 0.5
    elif "all" in text_lower:
        reduce_fraction = 1.0
    else:
        # Look for a percentage pattern (e.g., "25%")
        fraction_match = _REDUCE_FRACTION_PATTERN.search(text)
        if fraction_match:
            frac_text = fraction_match.group(0).lower()
            if frac_text.endswith("%"):
                # Extract percentage and convert to fraction
                try:
                    percent_value = float(frac_text[:-1])
                    reduce_fraction = percent_value / 100.0
                    # Ensure it's in valid range (0, 1]
                    if reduce_fraction <= 0 or reduce_fraction > 1.0:
                        reduce_fraction = None
                except ValueError:
                    reduce_fraction = None

    # If a reduce_fraction was found and side is CLOSE, map to REDUCE intent
    if reduce_fraction is not None and intent == Intent.EXIT:
        intent = Intent.REDUCE

    return intent, reduce_fraction


def classify_text_signal(
    text: str, *, source: str, asset_class: AssetClass = AssetClass.CRYPTO, analyst: str | None = None
) -> MessageDisposition:
    """E02 (bounded): classify one message's disposition -- PARSED,
    IGNORED, AMBIGUOUS, MISSING_DATA, or NO_MATCH -- rather than the
    binary "produced a Signal or raised" this module used to expose. This
    is what a source onboarding/history-review workflow needs (the
    adoption plan's E02 acceptance obligation: "every selected record has
    a parsed/ignored/ambiguous/missing-data disposition") -- reviewing
    WHY a past message never became a trade, not only that it didn't.

    `parse_text_signal` (below) is now a thin wrapper over this that
    preserves its exact previous behavior (raises SignalValidationError
    for every non-PARSED outcome) -- nothing on the live signal-ingestion
    path changed."""
    stripped = _merge_split_option_symbols(text.strip())
    match = _PATTERN.search(stripped)
    if not match:
        return MessageDisposition(text=text, outcome=DispositionOutcome.NO_MATCH, detail="no recognizable trade instruction")

    preceding = _words(stripped[: match.start()])[-_WORDS_BEFORE_MATCH_TO_CHECK:]
    following = _words(stripped[match.end() :])
    if any(w in _NEGATION_OR_CONDITIONAL_WORDS for w in preceding + following):
        return MessageDisposition(
            text=text,
            outcome=DispositionOutcome.IGNORED,
            detail="negated, conditional, or still-pending commentary rather than a trade instruction",
        )

    if len(_SIDE_WORD_PATTERN.findall(stripped)) > 1:
        return MessageDisposition(
            text=text,
            outcome=DispositionOutcome.AMBIGUOUS,
            detail="more than one trade instruction in a single message",
        )

    tp_resolution = _resolve_take_profit_targets(stripped)
    if isinstance(tp_resolution, str):
        # Genuinely unparseable/inconsistent TP labeling -- see
        # `_resolve_take_profit_targets`'s own docstring for exactly which
        # shapes still fall here (bare repeated TP with nothing to order
        # by, or level numbers that aren't a clean 1..n sequence).
        return MessageDisposition(text=text, outcome=DispositionOutcome.AMBIGUOUS, detail=tp_resolution)
    take_profit_raw, targets_raw = tp_resolution

    for field, raw in (
        ("quantity", match.group("quantity")),
        ("price", match.group("price")),
        ("sl", match.group("sl")),
        *((f"tp{level.label}", level.value) for level in targets_raw),
    ):
        if raw is not None and (raw.startswith("-") or float(raw.replace(",", "")) == 0):
            # A quantity, price, or stop/target level of zero (or less) is
            # never a valid trade instruction -- app/sources/webhook.py's
            # JSON ingestion path already enforces exactly this ("must be
            # greater than zero") for the same fields; this text grammar
            # used to let a literal "0" through as a clean PARSED result
            # with no such check.
            return MessageDisposition(
                text=text,
                outcome=DispositionOutcome.MISSING_DATA,
                detail=f"'{field}' must be greater than zero, got {raw!r}",
            )
        if raw is not None and "," in raw:
            # A comma inside a number is ambiguous (thousands separator vs.
            # decimal separator) with no locale hint in free text -- see
            # _NUMBER's comment above. Refuse instead of guessing which
            # reading the trader meant.
            return MessageDisposition(
                text=text,
                outcome=DispositionOutcome.MISSING_DATA,
                detail=f"'{field}' has an ambiguous comma-separated number, got {raw!r}",
            )

    side = _SIDE_ALIASES[match.group("side").lower()]
    symbol = match.group("symbol").upper()

    # WP-12: Validate symbol - reject stop-words and purely numeric symbols
    if not _is_valid_symbol(symbol):
        return MessageDisposition(
            text=text,
            outcome=DispositionOutcome.MISSING_DATA,
            detail=f"symbol '{symbol}' is not a valid instrument (stop-word or purely numeric)",
        )

    # WP-12: Try to parse option contracts
    text_after_symbol = stripped[match.end("symbol"):]
    option_parse_result = _try_parse_option_contract(symbol, text_after_symbol)
    option_contract = None
    if option_parse_result is not None:
        underlying, expiry_str, strike, right = option_parse_result
        option_contract = OptionContractSpec(
            underlying=underlying,
            expiry=expiry_str,
            strike=strike,
            right=right,
        )
        resolved_asset_class = AssetClass.OPTION
    else:
        # Check if the symbol looks like an incomplete option
        strike_pattern = r"\d+(?:\.\d+)?\s*[CP](?:\s|$)"
        if re.search(strike_pattern, text_after_symbol, re.IGNORECASE):
            # Found strike+C/P but no expiry
            return MessageDisposition(
                text=text,
                outcome=DispositionOutcome.MISSING_DATA,
                detail="option contract incomplete (missing expiry date)",
            )

        # Standard asset class inference
        inferred_asset_class = _infer_asset_class(symbol)
        resolved_asset_class = (
            inferred_asset_class if inferred_asset_class is not None else asset_class
        )

    # Only a GENUINE multi-target message (2+ cleanly-numbered TP levels)
    # populates `targets` -- a single TP mention keeps this parser's
    # previous behavior exactly (`targets` stays empty, `take_profit`
    # alone carries the value).
    targets = (
        [ProfitTarget(price=float(level.value.replace(",", "")), label=f"TP{level.label}") for level in targets_raw]
        if len(targets_raw) > 1
        else []
    )

    # WP-12: Mark if asset class was inferred from symbol shape
    raw_data: dict[str, Any] = {"text": text}
    was_inferred = False
    if option_contract is None and inferred_asset_class is not None:
        was_inferred = inferred_asset_class != asset_class
        raw_data["asset_class_inferred"] = was_inferred

    # WP-08: Determine intent and reduce_fraction from side and text
    intent, reduce_fraction = _determine_intent_and_reduce_fraction(side, text)

    signal = Signal(
        source=source,
        symbol=symbol,
        side=side,
        asset_class=resolved_asset_class,
        analyst=analyst,
        quantity=_optional_float(match.group("quantity")),
        price=_optional_float(match.group("price")),
        stop_loss=_optional_float(match.group("sl")),
        take_profit=_optional_float(take_profit_raw),
        targets=targets,
        intent=intent,
        reduce_fraction=reduce_fraction,
        option=option_contract,
        parser_version=PARSER_VERSION,
        raw=raw_data,
    )
    return MessageDisposition(text=text, outcome=DispositionOutcome.PARSED, signal=signal)


def classify_batch(
    texts: list[str], *, source: str, asset_class: AssetClass = AssetClass.CRYPTO, analyst: str | None = None
) -> list[MessageDisposition]:
    """E02 (bounded): classify a batch of historical messages -- the
    piece of a full source-onboarding/parser-lab workflow this session
    can deliver without a specific source's own history-export API to
    pull from (that part -- "one workflow imports all accessible
    authorized selected history" -- stays a disclosed gap; this is the
    classification step that workflow would call per message)."""
    return [classify_text_signal(text, source=source, asset_class=asset_class, analyst=analyst) for text in texts]


def parse_text_signal(
    text: str, *, source: str, asset_class: AssetClass = AssetClass.CRYPTO, analyst: str | None = None
) -> Signal:
    disposition = classify_text_signal(text, source=source, asset_class=asset_class, analyst=analyst)
    if disposition.outcome is not DispositionOutcome.PARSED:
        raise SignalValidationError(f"{disposition.detail}: {text!r}")
    assert disposition.signal is not None  # PARSED always sets it -- see classify_text_signal
    return disposition.signal


def _optional_float(value: str | None) -> float | None:
    return float(value) if value is not None else None
