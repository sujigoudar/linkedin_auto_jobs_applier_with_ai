"""C22: structlog for the core financial path's correlated, structured
logs (signal_id/source/symbol/side, secrets redacted).

Deliberately configured to write through its OWN dedicated renderer
(`structlog.PrintLoggerFactory`), never by reformatting the existing
stdlib root logger's handlers -- an earlier version of this module set a
custom formatter on every handler already attached to the root logger at
configure time, which corrupted OTHER code's plain stdlib log output
whenever that code (or a test's `caplog`) shared the same process/root
logger. This version touches nothing this module doesn't own: existing
`logging.getLogger(__name__)` calls throughout the codebase are
completely unaffected, in tests and in production alike.

The tradeoff: a plain stdlib `logger.info(...)` call deep in
lifecycle/manager.py or reconciliation.py does NOT automatically pick up
the bound correlation fields the way a genuinely unified logging setup
would -- only code that explicitly calls `structlog.get_logger(...)`
(as app/engine.py's `handle_signal` now does at its key checkpoints)
gets them. That's a real, disclosed limitation, not a claim of full
coverage across every existing log call site.
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator

import structlog

#: Field names this app treats as secret-shaped anywhere they appear in a
#: log call's kwargs -- matches this project's own vocabulary for what
#: never belongs in logs/exports/page source (OWNER_PASSWORD,
#: SESSION_SECRET, WEBHOOK_SHARED_SECRET, broker API keys/tokens, Twilio
#: auth token, ...). A conservative substring match, not an exhaustive
#: allowlist -- it's fine to redact a field that wasn't actually secret,
#: it's not fine to miss one that was.
_SECRET_KEY_SUBSTRINGS = ("password", "secret", "token", "api_key", "apikey", "auth")


def _redact_secrets(logger, method_name, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict.keys()):
        lowered = key.lower()
        if any(marker in lowered for marker in _SECRET_KEY_SUBSTRINGS):
            event_dict[key] = "***redacted***"
    return event_dict


def configure_structlog(json_output: bool = True) -> None:
    """Call once at process startup. Independent of stdlib
    `logging.basicConfig(...)` -- neither reads nor mutates its handlers."""
    renderer = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_secrets,
            renderer,
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(0),  # 0 = NOTSET, i.e. no level filtering here
        cache_logger_on_first_use=True,
    )


@contextlib.contextmanager
def bind_signal_context(**fields: Any) -> Iterator[None]:
    """Bind fields (signal_id, source, symbol, side, account_id, ...) onto
    every structlog call made anywhere during this `with` block, then
    clears them back out on exit so they don't leak into an unrelated
    signal's logs on the same event loop."""
    tokens = structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
