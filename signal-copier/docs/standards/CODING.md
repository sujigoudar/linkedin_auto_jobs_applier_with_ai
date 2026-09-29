# Coding Standards

These are the rules this codebase actually follows, evidenced by reading
`pyproject.toml`, `.github/workflows/signal-copier-ci.yml`, and the `app/`
modules themselves — not generic Python advice. Where a rule has an
exception, the exception is named.

## 1. Comments exist only to explain non-obvious WHY, never WHAT

Grep any module here (`app/writer_lease.py`, `app/command_ledger.py`,
`app/rate_limit.py`, `app/pricing.py`, `app/context/sec_edgar.py`, ...) and
you will not find a comment that restates the line below it. Every comment
and docstring answers a question a reader would otherwise have to
reconstruct from git history or a production incident:

- *Why this shape, not the obvious one.* `app/logging_config.py`'s
  docstring explains it configures structlog through its own
  `PrintLoggerFactory` specifically because an earlier version reformatted
  the stdlib root logger's handlers and corrupted unrelated `caplog`-based
  tests — the comment records the rejected alternative and why it broke.
- *Why a bound is what it is.* `app/rate_limit.py`: "30/minute... is a
  defensive ceiling against abuse/flooding, not a constraint on legitimate
  traffic." `app/context/sec_edgar.py`: 5 req/sec self-imposed, half of
  SEC's own documented 10/sec ceiling, "so a bug ... can't get this
  service's IP blocked."
- *Why a lock is needed at all.* `app/writer_lease.py`'s `WriterLeaseGuard`
  has an 18-line comment on `self._lock` walking through the exact
  interleaving (two OS threads, not just coroutines, both observing
  `_token is None`) that would double-acquire without it. This is the
  standard: when a concurrency primitive exists, the comment next to it
  must explain the race it closes, not just assert "thread-safe."
- *Why NOT to do the obvious thing.* `app/writer_lease.py`'s module
  docstring has a "What this deliberately does NOT do" section. This
  pattern — naming the tempting-but-wrong alternative and why it was
  rejected — recurs throughout (`app/capital_allocator.py`'s "Known gap"
  notes, `app/rate_limit.py`'s "not implemented here since nothing in this
  project runs that way").

Module-level docstrings are long and load-bearing here (see
`app/writer_lease.py`, `app/command_ledger.py`, `app/logging_config.py`,
`app/rate_limit.py`); function/method docstrings are shorter but still
carry the *why*, e.g. `app/risk.py::size_for_account`'s docstring explains
the precedence rule (fixed quantity beats multiplier) and the use case
each branch serves. There is no ambient policy against writing lines of
comments — the constraint is on *content*: a comment must earn its place
by disclosing something the code alone doesn't.

If you add a comment, ask: does this tell the reader something they could
not get by reading the code itself? If not, delete it.

## 2. Fail closed, not fail open

This is the single most repeated design decision in the codebase, and CI
enforces the mutation-testing baseline specifically on `app/auth.py`
because "a mutation that quietly weakens one of those checks ... is
exactly the class of bug a mutation score can catch that a coverage
percentage cannot" (`pyproject.toml`, `[tool.mutmut]`).

Concrete instances to follow as precedent:

- `app/writer_lease.py::WriterLeaseGuard.require_active()` treats a DB
  error the same as a lease mismatch — both raise `FencedOutError` — with
  an explicit `# noqa: BLE001 - fail closed: a DB error must never be
  treated as "still valid"`.
- `app/capital_allocator.py` (P0-3): a signal with no resolvable price
  used to silently *skip* the admission gate; it now is *rejected*
  whenever any exposure/risk gate is configured. A position whose average
  cost can't be resolved used to contribute `0.0` to exposure (i.e.
  "invisible" risk); it now returns `unresolved_symbols` and every gate
  rejects outright rather than treating unresolved exposure as zero.
- `app/brokers/ibkr.py` / `app/sources/rithmic.py` (P0-9): fill quantity
  and price used the `x or None` / `x if x else None` idiom, which
  silently turns a genuine `0.0` into `None`. Downstream code treats
  `None` as "unknown, fall back to the full requested quantity" — so a
  real zero-fill was reported as a full fill. Fixed to `is not None`
  checks. **Never use the `or`/truthiness idiom to coerce a financial
  quantity that can legitimately be zero** — see `docs/standards/ERROR_HANDLING.md`.
- `app/db.py`'s AUD-01 quantity model: a PENDING order used to
  optimistically apply the full requested quantity to
  `positions.net_quantity` before any broker confirmation. It now leaves
  the confirmed position untouched until a broker-confirmed fill lands,
  and tracks the uncertain remainder separately
  (`outstanding_possible_fill`) rather than guessing.
- `app/writer_lease.py::WriterLeaseHeldByAnotherSiteError` /
  `LeaseStillValidError`: automatic failover is refused outright; only an
  explicit, human-run `python -m app.promote_cli` (three confirmation
  flags) may move the writer role.

The house rule: when a value is ambiguous, missing, or a check can't be
completed, the code must land on the side that blocks a trade or
disclosures an unknown — never on the side that silently proceeds as if
everything were fine.

## 3. Honest capability/readiness disclosure over fabricated metrics

`app/main.py`'s readiness endpoints use a small, consistent status
vocabulary — `not_tracked`, `unknown`, `not_held`, `partial`, `stale` —
instead of inventing a number or a green checkmark when the real answer
isn't known:

```
account_rows.append({"account_id": account_id, "status": "unknown",
    "reason": f"no broker adapter registered for '{account.broker}'"})
account_rows.append({"account_id": account_id, "status": "not_tracked",
    "reason": f"{broker.name} adapter has no verified get_account_balance implementation."})
```

The dashboard JS renders these through a shared `Components.renderCapabilityState(...)`
helper (or an inline `pill()` helper) rather than each call site inventing
its own fallback — see P0-9's sweep note that "missing balances render as
an honest 'unknown' pill ... rather than a fabricated $0."

`POST /routing-rules/simulate`'s docstring is the clearest statement of
this principle: deduplication "is deliberately NOT evaluated here... a
hypothetical signal invented for this dry run has no counterpart to
[compare against]. Guessing an answer for it would be worse than admitting
there's nothing real to check."

When you add a new readiness/status field: if you cannot verify it, return
`unknown` or `not_tracked` with a `reason` string, never a plausible-looking
default.

## 4. `pytest.importorskip` for optional runtime dependencies

Four broker/source integrations (`ccxt`, `ib_async`, `async_rithmic`,
`twilio`, `tweepy`) are optional at runtime — `ccxt` is not even installed
in CI (`requirements.txt` has it commented out). Every test that imports
one of these guards itself:

```python
pytest.importorskip("ccxt")  # optional dependency -- not installed in CI (see requirements.txt)
```

used at module scope (`tests/test_ccxt_broker.py`) or inside individual
test functions when only part of a file needs it
(`tests/test_account_balance_capability.py`,
`tests/test_broker_capability_gate.py`). See
`docs/standards/TESTING.md` for the full convention including CI's mypy
scope, which likewise excludes files gated behind these optional imports
where relevant.

## 5. Bounded concurrency and self-imposed rate limits, always with the "why this number" comment

Anywhere this codebase does concurrent I/O against an external system, the
bound is explicit and the comment states which real constraint it is
protecting against (see `docs/standards/PERFORMANCE.md` for the full list:
`app/rate_limit.py`, `app/context/{fred,fx,sec_edgar}.py`,
`app/pricing.py::PriceMonitor`). Do not add an unbounded `asyncio.gather`
or an unthrottled external HTTP loop — follow the `asyncio.Semaphore`/
`aiolimiter.AsyncLimiter` precedent and document the real limit (vendor's
documented ceiling, or a self-imposed courtesy ceiling when the vendor
documents none, as in `app/context/fx.py`).
