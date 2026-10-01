# Current progress snapshot

As of `HEAD` = `4e41b15e08585ee7ca189f1e84f47523c7b94c5f` on
`claude/signal-copier-readiness-sm44tr` (2026-10-01). This is a snapshot, not
a roadmap — update it when the state it describes actually changes. This
file was previously stale for an extended period (it referenced an old
branch, `claude/signal-copier-redesign`, and alembic head `0015`, long after
both had moved on) — if you find it stale again, fix it rather than working
around it.

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

Currently in flight (as of this snapshot): an "Agent Reach" integration —
after inspecting the actual upstream `Panniantong/Agent-Reach` project, the
conclusion was that it adds nothing for RSS/public-web sources (it's a
router that tells an agent to call `feedparser`/a web-reader directly, not a
content-reading wrapper) and its only genuine value (avoiding Twitter's paid
API) needs real credentials unavailable in this environment. The resulting
plan: build signal-copier's own deterministic adapter boundary
(probe/fetch/poll/normalize) and a real RSS source adapter with no
dependency on Agent Reach, plus two UI gaps the audit found (no Mobile
Devices dashboard screen for Track 20's existing backend, and no screen
listing Track 14's provider-catalog rows). These are dispatched as
Tracks 24–25 — check `git log --oneline --all | grep -i "track2[4-9]"`
for whether they've landed since this snapshot was written.

## What's genuinely landed and working, as of HEAD

- Alembic head is `0033`. Full `pytest -q` suite: **2057 passed, 0 failed**
  (verified fresh against this exact HEAD after the Track 23 merge — see
  that merge commit's message for the full command output).
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

## What's genuinely NOT yet landed / still open

See the audit-findings document referenced in this session's history for
the full list; highlights carried forward here since they're not yet
closed:

- **No Mobile Devices dashboard screen** for Track 20's `/mobile-devices*`
  REST backend — confirmed via live browser testing, every guessed route
  renders an honest "Not found" page. (Being addressed as Track 25 — check
  whether it's landed.)
- **No screen lists Track 14's provider-catalog rows** (the data the
  onboarding wizard actually creates) — `app/static/views/tr09.js` reads a
  different, older data model (`GET /providers`) and will never show them.
  (Also part of Track 25.)
- **Managed-lifecycle exit idempotency** relies on `CloseArbiter.pending_exit`,
  not a real retry-safe key — a genuinely duplicate EXIT signal (different
  `channel_id`/`message_id`, same real-world event) arriving after the prior
  exit already resolved would not be caught. Needs a concrete two-signal
  reproduction test; not yet written.
- `signal_platform_contracts` is stale relative to Track 14 and Track
  22/23 — no contract-side follow-up has landed for either.
- `trading_authority`/`release_status` in `/system/readiness` remain
  honest, pre-existing placeholders (P0-6/P0-7 qualification/release-
  taxonomy work, never built) — out of scope for the Provider/Source/
  Connection and ingestion-architecture work this branch has otherwise
  been doing; not a regression, just still genuinely unbuilt.

## Verification status

Last independently re-verified state: this exact HEAD (`4e41b15`), full
`pytest -q` (2057 passed), `ruff check .` clean, CI-scoped `mypy` clean,
single alembic head `0033` confirmed via `alembic heads`. Any commit after
this HEAD should be treated as unverified by this snapshot until its own
merge commit documents a fresh run of the same three checks.
