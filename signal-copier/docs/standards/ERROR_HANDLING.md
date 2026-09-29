# Error Handling Standards

The real exception/error conventions in this codebase, evidenced by every
custom exception class under `app/` and their call sites.

## The full custom exception catalog

```
app/errors.py:                       SignalValidationError(ValueError)
app/command_ledger.py:                CommandFingerprintMismatch(ValueError)
app/context/fred.py:                  NotConfigured(RuntimeError)
app/context/sec_edgar.py:             NotConfigured(RuntimeError)
app/lifecycle/close_arbiter.py:       Halted(RuntimeError)
app/qualification.py:                 QualificationError(ValueError)
app/relay_worker.py:                  RelayNotConfiguredError(Exception)
app/services/catalog_fit_sim_auth.py: InvalidCatalogFitSimSignatureHeaderError(Exception)
app/services/catalog_fit_sim_auth.py: CatalogFitSimSignatureMismatchError(Exception)
app/services/catalog_fit_sim_auth.py: StaleCatalogFitSimTimestampError(Exception)
app/writer_lease.py:                  FencedOutError(RuntimeError)
app/writer_lease.py:                  LeaseStillValidError(RuntimeError)
app/writer_lease.py:                  WriterLeaseHeldByAnotherSiteError(RuntimeError)
```

Pattern: every custom exception is a thin subclass (`ValueError`,
`RuntimeError`, or plain `Exception`) with **no custom `__init__`** — the
class exists purely to give a distinct, catchable *name* to one specific
failure mode, and its docstring carries the real content: who raises it,
who must catch it (if anyone), and what the caller is required to do in
response. `ValueError` is used when the failure is about a bad *value*
supplied by the caller (`SignalValidationError`, `CommandFingerprintMismatch`,
`QualificationError`); `RuntimeError` is used when the failure is about
*state* the caller doesn't fully control (`FencedOutError`,
`Halted`, `LeaseStillValidError`, `WriterLeaseHeldByAnotherSiteError`,
`NotConfigured`).

Two modules (`app/context/fred.py`, `app/context/sec_edgar.py`) each
define their own `NotConfigured` rather than sharing one — deliberately:
these are independent, unrelated "this optional context source has no API
key/user-agent configured" conditions, and a caller catching one must
never accidentally also swallow the other's. Don't create a shared
`NotConfigured` in a common module to "reduce duplication" — the
duplication here is the point.

## Raise vs. return `None` vs. an honest "unknown" status

This codebase uses all three, and the choice is never arbitrary:

**Raise** when the caller has a bug, or when proceeding would be actively
unsafe and the caller MUST stop:
- `CommandFingerprintMismatch` — "the caller has a bug (reused an
  idempotency key for a different command) and must not proceed."
- `FencedOutError` — every command-execution path "must let this
  propagate and refuse to submit the command — never caught and ignored."
- `WriterLeaseHeldByAnotherSiteError` — raised, uncaught, at startup;
  deliberately crashes the process rather than starting as a silent
  second writer.

**Return `None`** when the absence of a value is itself meaningful
domain information, not an error — e.g. a broker adapter's
`get_last_price` returning `None` means "no price available this cycle,"
which `app/pricing.py::PriceMonitor._poll_one` handles by skipping that
position for this pass (not raising, not treating it as zero). The rule
that makes this safe: `None` must never be produced by *coercing away* a
real value (see the `x or None` fill-quantity bug in
`docs/standards/CODING.md` §2 / `docs/testing/REGRESSIONS.md` — P0-9).
`None` is legitimate only when nothing was actually observed; it must
never stand in for a genuine `0.0` or `False`.

**Return an honest "unknown"/"not_tracked" status string** (never raise,
never guess) when the caller is a read/reporting path whose job is to
*describe* the system's current state truthfully, not to enforce an
invariant. See `docs/standards/CODING.md` §3 for the full status
vocabulary (`unknown`, `not_tracked`, `not_held`, `partial`, `stale`).
These are structured data returned to a dashboard, not Python exceptions
— an exception would be the wrong tool here because nothing has gone
wrong; the system genuinely doesn't have that information right now, and
disclosing that honestly is the entire feature.

## Fail-closed exception handling: what's allowed to swallow an exception

A bare `except Exception:` is rare and always annotated with why it's
safe, using `# noqa: BLE001 - <reason>`:

```python
# app/writer_lease.py — WriterLeaseGuard.require_active()
except Exception as exc:  # noqa: BLE001 - fail closed: a DB error must never be treated as "still valid"
    raise FencedOutError(...) from exc

# app/main.py — the background lease-renewal heartbeat loop
except Exception:  # noqa: BLE001 - a heartbeat failure must not crash the loop or the app
    logger.exception("writer lease heartbeat failed (will retry next interval)")

# app/pricing.py — PriceMonitor._loop / _poll_one
except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
    logger.exception("error during price monitor pass")
except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
    logger.exception("get_last_price failed for account=%s symbol=%s broker=%s", ...)
```

The two legitimate reasons a broad catch appears here:

1. **Fail closed by converting any ambiguity into the most restrictive
   outcome** (`require_active`'s case: any DB error becomes
   `FencedOutError`, never "assume still valid").
2. **Isolate one failure so it can't take down a long-running loop or an
   unrelated unit of work** (the heartbeat and price-monitor cases) — but
   always paired with `logger.exception`/`logger.critical` so the failure
   is never silent, and always re-raising `asyncio.CancelledError`
   explicitly first, since swallowing cancellation would make the task
   un-cancellable (see `app/pricing.py::PriceMonitor._loop`).

A broad catch that neither converts to fail-closed nor logs is not this
codebase's style — if you add one, it needs a `# noqa: BLE001 - <reason>`
comment stating which of the two cases it is and why.

## Command-ledger three-step shape (the fail-closed template for any new broker-write call site)

`app/command_ledger.py`'s module docstring defines the required shape for
every place this codebase talks to a broker to change state:

1. Open a durable, pre-effect ledger entry (`store.open_command_ledger_entry`)
   *before* calling the broker. If this raises `CommandFingerprintMismatch`,
   stop — that's a caller bug. If the returned entry is already resolved
   (not `PENDING_SUBMISSION`), a prior attempt under the same idempotency
   key already ran — replay its tracked state, never call the broker
   again.
2. Call the broker. On a clean return, classify the `OrderResult` and
   call `store.mark_command_ledger_outcome(...)`.
3. On the broker call *raising*, classify as `UNKNOWN_AMBIGUOUS` via
   `ambiguous_evidence_for_exception` and record that — "never let an
   exception skip this ledger update; the whole point of this module is
   that an ambiguous outcome is recorded, not silently dropped."

Any new code path that submits, cancels, or replaces a broker order
follows this three-step shape — see the real call sites in
`app/engine.py` (`handle_signal`, `_submit_order`, `_handle_managed_entry`)
and `app/lifecycle/manager.py` (`_submit_exit_order`,
`_place_or_replace_stop`-style sites) for the concrete pattern to copy.
