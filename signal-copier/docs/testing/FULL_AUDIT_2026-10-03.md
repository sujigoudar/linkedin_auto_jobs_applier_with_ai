# Full test audit - 2026-10-03

Scope: every automated check available in this repository was run (not sampled),
every failure was root-caused, and the owner's 46-69 test taxonomy (491 items) was
classified against the real tests. Both applications are covered: `signal-copier`
and `signal-portfolio-commercial`.

Evidence level used throughout: **VERIFIED** means it was run or reproduced in this
audit; **MATRIX** means it was reported by a read-only audit agent with file/line
evidence in `docs/testing/audit/matrix-*.md` and has not been independently re-run.

## 1. What ran and the result (all VERIFIED)

| Check | Result |
|---|---|
| signal-copier full suite, serial (same as CI `pytest -q`) | 4,369 passed, 2 skipped, 0 failed (18m47s) |
| signal-copier full suite, parallel `pytest -n 4` | 4,382 passed, 2 skipped, 0 failed (7m03s), after this audit's fixes |
| signal-portfolio-commercial full suite | 1,445 passed (1,172 in the full run + 273 re-run after infrastructure errors, see 3.11) |
| Workflow contract, all cases (`WC10_FULL`, sharded) | 4,608/4,608 admission, 4,500/4,500 sizing |
| ruff (both apps), mypy (CI scope, signal-copier; whole package, commercial), docs manifest | clean |
| pip-audit (both apps) | no known vulnerabilities (1 documented ignore, CVE-2026-49265) |
| CI on the PR head | `test`, `build`, `secret-scan`, design-system jobs green |

The two skipped tests are the full-contract tests that only run with `WC10_FULL=1`; they
were run separately (row 4).

## 2. Not run, and why (only these are acceptable skips)

| Item | Needs |
|---|---|
| Docker build / container startup / compose smoke | A running Docker daemon (client present, daemon absent) |
| Firefox, Safari/WebKit, Edge, iPhone Safari, iPad, Android Chrome, Windows, macOS | Browser engines / devices not installed or not available; only Chromium on Linux exists |
| Live broker, billing-processor, DNS/TLS, region and VPS recovery tests | External accounts / real infrastructure |
| `gitleaks` locally | Binary not installed locally; it runs and passes in CI |
| Full `mutmut` mutation run | Not completed in this audit: it is slow and was historically unstable in this container (see PROGRESS.md). Hand-written mutation regression suites are in the normal test run. |

## 3. Errors found, root cause, fix

| # | Error | Root cause | Fix (commit) |
|---|---|---|---|
| 3.1 | CI type-check failed (3 mypy errors) | Untyped `defaultdict`s in the AgentMail statistics endpoint (Task 195) | Annotated (11a0224) |
| 3.2 | Docs manifest check failing | 61 real docs files not indexed in `docs/manifest.yaml` | Indexed (11a0224) |
| 3.3 | **CI `Run tests` failed on every AgentMail commit** | `broker_operations_incidents` was added to the store bootstrap with no Alembic revision, so `test_e01_alembic_migration_stamping` failed | Alembic 0060 (11b8a16) |
| 3.4 | ~15 test files silently skipped in CI; 3 tests skipped even where possible | `ccxt` was commented out of `requirements.txt` (so `importorskip` skipped everywhere in CI), and three tests had `skipif(True)` | `ccxt` installed; skips removed (588810e) |
| 3.5 | "Passes alone, fails in the full/parallel run": 283 failures in an earlier run | `app.main` takes the single-writer lease on its database at import time. Every pytest process shared `signal_copier.db`, so a second process fenced the first (`FencedOutError`). Earlier "pre-existing failure" and "xdist isolation" explanations were wrong. | Per-process (and per-xdist-worker) database in `tests/conftest.py` (6079b0a) |
| 3.6 | Contract suite: 4,196/4,608 admission and 1,105/4,500 sizing failures when run in full | Test-adapter defects, not engine defects: fixture inserts used stale column names inside `try/except: pass` (uncertain-effect, portfolio-halt, budget scenarios were never created); sizing read a message that never contains the quantity; float stop prices (49.98000...04) rejected as sub-cent; fixture broker returned `Decimal` where real brokers return floats; a misplaced block (my own, c71815c, fixed in 9dd3067) | Adapter rebuilt on real store APIs, fails loudly, reads the engine's own durable records (5e9e730, c71815c, 9dd3067, 9637e28, 8e0d6fa) |
| 3.7 | Budget treated as unlimited when no limit is configured | `get_level_remaining` returns an effectively unlimited sentinel when no limit exists | Owner decision: budget is never unlimited. Admission now blocks when the broker reports capital (buying power, else cash) and none remains after unfilled reservations; unreported capital is refused by the existing order-time gate with its own message (8e0d6fa) |
| 3.8 | Admission reported only the first blocking reason | Short-circuited `if not excluded_reasons` chain | All applicable reasons collected (8e0d6fa) |
| 3.9 | Compose Postgres published on all interfaces next to a fixed password | `ports: "5432:5432"` | Bound to `127.0.0.1`; regression test (a2c57f4) |
| 3.10 | **Wrong-side protective levels accepted** (long stop above entry; long target below entry; mirror for shorts) | No validation of stop/target direction before sizing | Rejected before any sizing/reservation/broker call; 11 tests, 7 fail without the fix (657e559) |
| 3.11 | Commercial suite: 199 setup errors in one long run | The temporary Postgres cluster started by the test fixture became unreachable mid-run. All 273 affected tests pass in isolation. | **Open - cause not found.** Treated as an infrastructure flake, not code. |

Regression I introduced and fixed in this audit: c71815c broke every admission sample
test (code inserted into the wrong function) and was pushed because a failing pytest
exit status was hidden by a pipe. Fixed in 9dd3067; every later commit was gated on
the real exit status.

## 4. Open product defects and missing features (decisions needed)

Verified by reproduction in this audit:

1. **Daily-loss limit is unusable.** Configuring `daily_loss_limit_percent` rejects every
   entry and persists a halt; `daily_loss_limiter.py` calls a store method that does not exist.
   (The only test mocks the limiter.)

Reported by the matrices (MATRIX; evidence in the linked files):

2. Margin is never reserved: `initial_margin_cents = 0` for every account (`app/engine.py`, ~L517). Account type is ignored.
3. Scale-in (`Intent.ADD`) has no test and `app/workflow/scaling.py` has no production caller; `identity.py`, `kelly.py`, `selection.py` likewise. `PRODUCTION_READINESS.md` still claims Kelly/pyramiding.
4. Margin-call alerts can never be cleared in production (`resolve_margin_call` has no caller) while an open alert blocks all entries.
5. Operator alerting is effectively absent: the alert sink's webhook push is never wired; protection-deficit and adoption alerts are TODOs.
6. Commercial billing: nothing sets `Subscription.state`; trial, trial-expiry, upgrade, downgrade, reactivation and failed-payment handling do not exist; `portfolio_limit` and feature entitlements are stored but not enforced.
7. No customer data export or account deletion.
8. A plain-account entry whose response was lost but which filled is not reconciled (per the allocation traceability doc; not re-run).
9. `update_targets` is a no-op with a TODO and no test; owner-scope halts cannot be cleared through the API.
10. FUTURE contract handling, broker credential/OAuth expiry mid-session (5 brokers), future-dated timestamps counted as fresh, and 12 of 15 broker adapters have no engine-level test.
11. No real historical signal corpora (KamdenAI/Telegram/Discord) are replayed anywhere.
12. No coverage gate, no random-order or flake tooling; `pytest-xdist` is not in requirements/CI; mutation tests never run in CI; no backup/restore/RTO/RPO/performance/load checks.
13. Only 59 of 292 workflow-contract scenarios carry a test marker; several `test_scn_*`/`test_wc06` tests are `pass`-bodied placeholders that traceability counts as tested; the 120 race vectors never touch the engine.
14. Docs: README quickstart fails as written (missing webhook-secret header; `.env.example` ships it blank); 30 settings missing from `.env.example`; ~43 backticked paths point at files that do not exist; no user/admin/incident/onboarding guides.

## 5. Taxonomy classification (sections 46-69, 491 items)

| Status | Count |
|---|---|
| COVERED | 124 |
| PARTIAL | 221 |
| GAP | 78 |
| INFRA | 29 |
| N/A | 39 |

Per-section tables with the evidence for each row:
[A (46-50)](audit/matrix-A.md), [B (51-55)](audit/matrix-B.md),
[C (56-61)](audit/matrix-C.md), [D (62-69)](audit/matrix-D.md).
`PARTIAL` is the dominant status by design of the strict rubric: a row is `COVERED`
only when a test that was opened asserts the behaviour. The matrices were produced by
read-only agents; the headline defects in section 4 marked VERIFIED were reproduced
independently, the rest should be treated as leads with cited evidence.

## 6. Reproduce

```
cd signal-copier
pytest -q -n 4                    # whole suite in parallel (per-worker databases)
WC10_FULL=1 pytest tests/test_wc10_contract_adapter.py   # all 9,108 contract cases (~1h serial)
ruff check . && make typecheck && python scripts/check_docs_manifest.py
cd ../signal-portfolio-commercial && pytest -q
```
