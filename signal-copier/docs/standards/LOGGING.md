# Logging Standards

This codebase runs **two parallel, deliberately independent** logging
paths. Knowing which one to use, and when, is the whole standard.

## Two loggers, on purpose

1. **Plain stdlib `logging.getLogger(__name__)`** — used everywhere by
   default: `app/main.py`, `app/writer_lease.py`, `app/lifecycle/manager.py`,
   `app/reconciliation.py`, `app/pricing.py`, every `app/sources/*.py`
   adapter, `app/auth.py`, `app/promote_cli.py`, etc.
2. **structlog, via `structured_logger = structlog.get_logger(__name__)`**
   — used only at `app/engine.py`'s and `app/relay_worker.py`'s key
   checkpoints, for the correlated, structured signal-processing log line.

`app/logging_config.py`'s module docstring states why these are kept
separate rather than unified: an earlier version of `configure_structlog`
attached a custom formatter to every handler already on the stdlib root
logger, and "corrupted OTHER code's plain stdlib log output whenever that
code (or a test's `caplog`) shared the same process/root logger." The
current version "touches nothing this module doesn't own" — it renders
through its own `structlog.PrintLoggerFactory`, never by reconfiguring
stdlib handlers. The disclosed tradeoff: a plain
`logger.info(...)` call deep in `lifecycle/manager.py` or
`reconciliation.py` does **not** automatically pick up the bound
correlation fields the way a fully unified setup would — only code that
explicitly calls `structlog.get_logger(...)` gets them. Don't try to
"fix" this by making every log call structlog; that was tried and it
broke `caplog`-based tests. If you need correlated context on an existing
plain-stdlib log line, either migrate that call site's module explicitly
(deliberately, the way `app/engine.py` and `app/relay_worker.py` did) or
pass the correlating fields as `%s`-args instead.

## When to use structlog (`structured_logger`)

Only for the core financial signal-processing path, at points where a
downstream consumer needs machine-parseable, correlated fields
(`signal_id`, `source`, `symbol`, `side`, `analyst`, `account_id`) rather
than a human-readable sentence. The pattern (`app/engine.py::handle_signal`):

```python
with bind_signal_context(signal_id=signal.id, source=signal.source,
                          symbol=signal.symbol, side=signal.side.value):
    structured_logger.info("signal_received", quantity=signal.quantity, analyst=signal.analyst)
    results = await self._handle_signal(signal)
    structured_logger.info("signal_processed", destination_count=len(results),
                            statuses=[r.status.value for r in results])
```

`bind_signal_context` (`app/logging_config.py`) binds fields onto every
structlog call made anywhere during that `with` block via
`structlog.contextvars`, then clears them on exit "so they don't leak
into an unrelated signal's logs on the same event loop." Event names are
short, snake_case, past-tense-ish nouns (`signal_received`,
`signal_processed`) — not full sentences; the sentence-shaped detail goes
in kwargs, not the event string.

## Secret redaction is automatic and field-name-based — don't bypass it

`app/logging_config.py::_redact_secrets` is wired into every structlog
call as a processor. Any kwarg whose key contains `password`, `secret`,
`token`, `api_key`, `apikey`, or `auth` (case-insensitive substring match)
is replaced with `"***redacted***"` before rendering. This is
deliberately a conservative substring match, not an exhaustive allowlist:
"it's fine to redact a field that wasn't actually secret, it's not fine
to miss one that was." When adding a new structlog call with a field that
might carry a credential, name the field so it matches this list (e.g.
`webhook_secret=...`, not `webhook_val=...`) rather than relying on
manual redaction at the call site. Plain stdlib log calls have no
equivalent automatic redaction — never log a secret-shaped value through
`logger.info(...)` either; there is no safety net there.

## Level conventions actually used

- **`logger.critical`** — reserved for conditions where this process must
  stop acting as the active writer and a human needs to know immediately.
  The only two real call sites are in `app/main.py`: the writer-lease
  heartbeat discovering this process has been **fenced out** (a new
  writer lease token exists elsewhere), and `lifespan()` refusing to
  start at all because a different site already holds the lease. Both
  messages explicitly point at `docs/FAILOVER.md` and state that this is
  not auto-resolved. Use `critical` only for "the single-writer safety
  invariant this system depends on may have just been violated," not for
  ordinary request-level failures.
- **`logger.warning`** — degraded-but-handled states: `STANDBY_MODE` at
  startup ("not starting signal ingestion, reconciliation, or price
  polling"), and per-position degraded conditions inside
  `app/lifecycle/manager.py` (e.g. a stop/target amend that couldn't be
  confirmed). Warning means "this is a real gap in coverage, but the
  process is continuing correctly given what it knows."
- **`logger.info`** — the default for anything that's a normal, expected
  branch of control flow worth a durable trace of: a signal replayed
  because it was already processed, a signal with no configured
  destinations, an account disabled by settings, a duplicate
  replace/cancel command idempotency-key hit
  (`app/lifecycle/manager.py`: `"duplicate replace command
  idempotency_key=%s -- replaying tracked state"`), a writer lease
  acquired (`app/writer_lease.py::WriterLeaseGuard.acquire`).
- **`logger.error`** — a specific operation failed in a way that's
  notable but not an immediate safety-invariant breach (an isolated
  reconciliation mismatch, a lifecycle-manager error path).
- **`logger.exception`** — always inside an `except` block that's about
  to swallow the exception so a loop can keep running (the price-monitor
  pass, the lease-renewal heartbeat's non-`FencedOutError` branch) — see
  `docs/standards/ERROR_HANDLING.md`'s "fail-closed exception handling"
  section. `logger.exception` (not `logger.error`) is used specifically
  because it captures the traceback automatically.

## Fencing/promotion events specifically

Every event in the writer-lease fencing lifecycle is logged, and at a
level matched to how serious it is:

- Lease acquired (new or reacquired by the same process/site): `info`
  (`app/writer_lease.py::WriterLeaseGuard.acquire`), includes
  `site`, `holder`, `token`.
- Refused to start because a different site already holds the lease:
  `critical` (`app/main.py::lifespan`).
- Fenced out on a renewal heartbeat: `critical` (`app/main.py::_writer_lease_heartbeat`).
- Standby mode refusing to start ingestion: `warning` (`app/main.py::lifespan`).

If you add a new fencing/promotion-adjacent event, match this pattern:
anything that means "the single-writer invariant may be compromised, act
now" is `critical`; anything that's an expected, safe branch (a routine
acquire/renew, a deliberate standby) is `info`/`warning`.
