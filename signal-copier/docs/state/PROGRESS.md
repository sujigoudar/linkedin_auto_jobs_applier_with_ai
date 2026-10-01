# Current progress snapshot

As of `HEAD` = `4a24d07` on `claude/signal-copier-readiness-sm44tr`
(2026-10-01). This is a snapshot, not a roadmap — update it when the state
it describes actually changes. This file was previously stale for an
extended period (it referenced an old branch, `claude/signal-copier-
redesign`, and alembic head `0015`, long after both had moved on), and was
stale again after that (alembic head `0033`/2057 passed, and three items
below listed as not-yet-landed that had in fact landed as Track 25/27) — if
you find it stale again, fix it rather than working around it.

## What wave this is

The branch completed a long sequence of numbered "Tracks" (1 through 23)
building out the Provider/Source/Connection data model, a 10-source signal
ingestion architecture (webhook, Telegram, Slack, Twitter/X, email, website,
Android notification bridge, Whop, SMS, WhatsApp), an onboarding wizard, and
the AUD-01 distinct-field quantity model across both plain-account and
managed-lifecycle order paths. That work, plus a full end-to-end audit wave
(financial/tenant-isolation/UX review agents + two live Playwright runs
across both signal-copier and signal-portfolio-commercial, see the git log
for the "Merge Track NN" commits and the audit-findings document linked from
that session), is complete and verified as of this HEAD.

An "Agent Reach" integration brief was evaluated and dispatched as
Tracks 24–25 — after inspecting the actual upstream
`Panniantong/Agent-Reach` project, the conclusion was that it adds nothing
for RSS/public-web sources (it's a router that tells an agent to call
`feedparser`/a web-reader directly, not a content-reading wrapper) and its
only genuine value (avoiding Twitter's paid API) needs real credentials
unavailable in this environment. The resulting plan — build signal-
copier's own deterministic adapter boundary (probe/fetch/poll/normalize)
and a real RSS source adapter with no dependency on Agent Reach (Track 24),
plus the two UI gaps the audit found: a Mobile Devices dashboard screen for
Track 20's existing backend, and a screen listing Track 14's provider-
catalog rows (Track 25) — has since landed; see "What's genuinely landed
and working" below. Tracks 26–29 (managed-lifecycle exit-idempotency
guard/ADR-0010, and SourceIdentity extensions) have also since landed —
check `git log --oneline --all | grep -i "track3[0-9]"` for whether
anything past Track 29 has landed since this snapshot was written.

## What's genuinely landed and working, as of HEAD

- Alembic head is `0034`. Full `pytest -q` suite: **2119 passed, 0 failed**
  (re-verified against this exact HEAD, after the Track 24–30/33 merges
  below had landed).
- `ruff check .` and the CI-scoped `mypy` command (file list in
  `.github/workflows/signal-copier-ci.yml`, 39 files) both clean against
  this HEAD.
- Managed-lifecycle fills now populate the AUD-01 quantity fields
  internally (Track 22) **and** export a real `EXECUTION_APPLIED` contract
  event (Track 23) — previously, per Track 22's own changelog entry, this
  was "the majority of real trading activity" silently invisible to
  signal-portfolio-commercial's platform ledger.
- The Track 21 onboarding wizard (`app/static/views/tr17.js`) is reachable
  and working end-to-end — a routing bug that made it completely
  unreachable (`#/providers/add` never matched the router's `/trade/...`
  prefix gate) was found and fixed this same session, along with a
  double-submit guard and partial-failure disclosure.
- **Mobile Devices dashboard screen** for Track 20's `/mobile-devices*`
  REST backend now exists (Track 25, `app/static/views/tr18.js`) — closes
  the gap this file previously listed below as not-yet-landed.
- **Provider-catalog screen** listing Track 14's provider-catalog rows now
  exists (Track 25, `app/static/views/tr19.js`) — closes the other gap
  this file previously listed below as not-yet-landed.
- **Managed-lifecycle exit-idempotency** gap closed by a bounded, in-
  memory duplicate-exit guard (Track 27, ADR-0010 —
  `docs/adr/0010-managed-exit-duplicate-episode-guard.md`), with a
  concrete two-signal reproduction test
  (`tests/test_trk27_managed_exit_duplicate_episode.py`) covering both the
  duplicate-recognized and genuine-re-entry-not-suppressed cases. See the
  ADR for the explicitly-scoped remainder this does NOT cover (in-memory
  only, no cross-restart persistence).
- An exhaustive, no-sampling E2E validation wave (both repos' full
  pytest suites, every dashboard screen/template driven live via
  Playwright, 5 live trading scenarios, a live cross-repo contract proof)
  surfaced one significant gap and several smaller ones, now all closed:
  **signal-portfolio-commercial's `_apply_projection` had no branch for
  `EventType.SOURCE_EVENT`** (Track 30) — a plain webhook `SOURCE_EVENT`
  emitted before its own `SOURCE_RECEIPT` on the same per-source stream
  parked permanently and blocked every later event on that stream, so in
  practice no `SOURCE_RECEIPT`/`ROUTING_ADMISSION_OUTCOME` delivered via
  the real webhook path ever reached the ledger. Fixed: `ORIGINAL`-kind
  events now advance the stream as a no-op (provenance already captured
  verbatim in `InboxEvent.envelope_json`); other kinds still park
  honestly as `unimplemented_source_event_kind`. Track 30 also fixed an
  owner-login bug (owners were redirected to the customer `/app` instead
  of `/ops`). Track 33 added an RSS-source duplicate-feed-URL warning
  (soft, not a rejection), a real-RLS proof that the rollback-recovery
  `require_tenant_scope` pattern is genuinely tenant-isolated (no bug
  found — reported as a verified-safe finding), and an owner-gated
  `GET /export-events` read endpoint for the export outbox. Tracks 31/32
  closed the remaining doc-staleness and accessibility-scaffolding
  findings from the same wave.

## What's genuinely NOT yet landed / still open

See the audit-findings document referenced in this session's history for
the full list; highlights carried forward here since they're not yet
closed:

- `signal_platform_contracts` is stale relative to Track 14 and Track
  22/23 — no contract-side follow-up has landed for either.
- `trading_authority`/`release_status` in `/system/readiness` remain
  honest, pre-existing placeholders (P0-6/P0-7 qualification/release-
  taxonomy work, never built) — out of scope for the Provider/Source/
  Connection and ingestion-architecture work this branch has otherwise
  been doing; not a regression, just still genuinely unbuilt.

## Verification status

Last independently re-verified state: this exact HEAD (`4a24d07`), full
`pytest -q` (2119 passed), single alembic head `0034` confirmed via
`alembic heads`. Any commit after this HEAD should be treated as
unverified by this snapshot until its own merge commit documents a fresh
run of the same checks.
