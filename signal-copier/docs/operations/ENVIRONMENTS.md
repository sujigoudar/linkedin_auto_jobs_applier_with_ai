# Environment distinctions

This codebase supports three independent, overlapping environment
concepts. They are not the same thing, and conflating them is a real
source of confusion, so each is documented separately here.

## 1. PAPER vs. LIVE evidence classification (`RELAY_EVIDENCE_CLASS` / `RELAY_ENVIRONMENT`)

Every command-ledger entry and every exported `EXECUTION_APPLIED` envelope
carries an `environment` field. `app/command_ledger.py`'s
`current_environment()` returns `config.RELAY_ENVIRONMENT` directly — this
is deployment-scoped, not per-account, because several broker adapters
have **no** paper/live distinction of their own at all (e.g. Schwab: "no
sandbox/paper environment at all," per `app/brokers/schwab.py`'s own
docstring). `DestinationAccount` itself carries no per-account paper/live
field.

- `RELAY_EVIDENCE_CLASS` (default `INTERNAL_PAPER`) and `RELAY_ENVIRONMENT`
  (default `LOCAL_SIM`) are truth claims about where a deployment's
  exported numbers actually came from — never inferred from a broker
  adapter's name (a "paper"-named adapter reused against a real account
  would otherwise become a silent, false label).
- The safest values are the defaults. An operator who genuinely has real
  broker credentials configured and wants to label exported evidence as
  `OBSERVED_OWNER_LIVE` must change these deliberately — this project's
  standing default is "no live orders, everything stays
  paper-traded/simulated" until an operator explicitly opts out of that.
- `app/engine.py` reads `Environment[config.RELAY_ENVIRONMENT]` at every
  export-event construction site, and `command_ledger` rows read
  `current_environment()` live (not cached at import time), so a
  monkeypatched/changed `RELAY_ENVIRONMENT` takes effect immediately.

This is the mechanism behind what "PAPER" vs. "ACTUAL LIVE" means in this
codebase: it's an explicit, operator-set label on exported/ledger data, not
something the app infers from which broker adapter happens to be wired up
for an account. A deployment can have real broker credentials configured
(via CCXT_SANDBOX=false, real Alpaca live keys, etc.) while
`RELAY_ENVIRONMENT` still says `LOCAL_SIM`, if the operator hasn't
explicitly flipped it — the two are independently configured, on purpose.

## 2. STANDBY_MODE — read-only vs. active-writer process posture

`STANDBY_MODE` (boolean, default `false`) is a **process-level** distinction,
not an evidence label. See `docs/operations/DEPLOYMENT.md` and
`docs/FAILOVER.md` for the full mechanism; summarized here:

- `STANDBY_MODE=true`: this process serves only `GET`/`HEAD`/`OPTIONS`.
  `app/main.py`'s `_standby_read_only_gate` middleware refuses every
  mutating request with `503`, and `lifespan` never starts signal
  ingestion, `OrderReconciler`, or `PriceMonitor` — independent of what
  any individual route's own logic would otherwise allow, and independent
  of whether `config/accounts.yaml` even contains real credentials (it
  shouldn't, on a standby).
- `STANDBY_MODE=false` (or unset): the normal, active-writer posture. This
  process starts ingestion and background workers, and — if it isn't
  already superseded — acquires the writer lease (`app/writer_lease.py`)
  at startup.
- Because `STANDBY_MODE` is a boolean read by `pydantic-settings`,
  malformed values (a typo, an empty env-file line, "enabled" instead of
  "true") now **raise at startup** instead of silently coercing to
  `False` — the unsafe direction for a safety flag, since `False` means
  "not in standby," i.e. live trading authority. This was a deliberate
  fix (see `app/config.py`'s module docstring) precisely because a
  malformed safety flag defaulting to "trade anyway" is the wrong failure
  direction.
- A promotion from standby to active is always a **deliberate, separate
  restart** with `STANDBY_MODE` unset/false AND real broker credentials
  configured — never a config flip on an already-running process.

## 3. WRITER_SITE_ID — which deployed site this process identifies as

`WRITER_SITE_ID` (string, default: falls back to `socket.gethostname()`)
identifies *which* site, in a multi-site deployment, this process is. It
is distinct from both concepts above: two processes can both be
`STANDBY_MODE=false` (active-writer posture) but represent different
sites, and the writer-lease mechanism refuses to let a *different* site
acquire the lease automatically — only the *same* site restarting
reacquires it without a manual promotion. See `docs/FAILOVER.md`.

An explicit, distinct value per site matters in practice — two hosts that
happen to share a hostname (containers, generic cloud images) would
otherwise look like "the same site" to
`acquire_or_reacquire_writer_lease` and be allowed to silently swap the
writer role between them, defeating the fencing mechanism's purpose.

## How these three interact

A typical single-site deployment: `STANDBY_MODE=false`,
`WRITER_SITE_ID=primary` (or similar), `RELAY_ENVIRONMENT` set explicitly
once real broker credentials and real observed fills exist. A guarded
active/passive deployment (`docs/operations/DEPLOYMENT.md`): Site A runs
`STANDBY_MODE=false`/`WRITER_SITE_ID=primary`, Site B runs
`STANDBY_MODE=true`/`WRITER_SITE_ID=standby-a` until a human promotes it —
at which point Site B's `STANDBY_MODE` is flipped false as part of the
manual promotion procedure, never automatically. `RELAY_ENVIRONMENT` is
orthogonal to both — it describes the evidentiary quality of what gets
exported, not which process is currently allowed to write.
