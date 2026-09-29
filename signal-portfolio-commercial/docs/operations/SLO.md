# Service level objectives

## Real, established numeric targets

### Export outbox size ceiling (sibling app's INT-040 work)

The one genuinely established, numeric, tested threshold relevant to this
service's own ingest lag lives in the **sibling** signal-copier repository,
not in this one -- but it directly bounds how much backlog this service's
relay ingress can ever be asked to absorb in one burst, so it belongs in
this service's own SLO reference.

From signal-copier's `app/config.py` (`EXPORT_OUTBOX_SIZE_CEILING_BYTES`)
and `INTEGRATION_ACCEPTANCE_STATUS.md`'s INT-040 entry:

- **Default ceiling: 256 MiB**, measured as a live
  `SELECT COUNT(*), SUM(LENGTH(envelope_json))` over undelivered
  `export_events` rows in signal-copier's own private outbox
  (`SignalStore.export_outbox_backlog()`) -- never an estimate, never the
  whole database file's size (which would conflate every other table's own
  growth with outbox pressure specifically).
- This is an **operator-tunable starting point**, not a claim about any
  specific deployment's real disk capacity -- the config comment is
  explicit that a real `EventEnvelope` (a handful of scalar
  identity/timing fields plus one small payload) is small, so 256 MiB
  represents a large number of undelivered events before it is reached.
- **What happens at the ceiling**: signal-copier's `GET /health` surfaces
  `outbox_backlog_ok`/`outbox_backlog_bytes`/`outbox_backlog_row_count`/
  `outbox_backlog_ceiling_bytes`, and its TR-16 dashboard folds an
  over-ceiling backlog into the **DEGRADED** tier, deliberately never the
  critical NOT READY gate -- "an over-ceiling backlog doesn't itself mean
  open positions are unprotected." This is a stated, explicit rollup
  decision, not a silent omission.
- **What never happens at the ceiling**: nothing in signal-copier deletes,
  prunes, or truncates `export_events` -- structurally verified there by
  `tests/test_export_outbox_ceiling.py::test_no_code_path_anywhere_deletes_or_truncates_export_events`.
  An over-ceiling backlog is surfaced, never silently discarded.
- **What is honestly BLOCKED, per that status entry**: behavior under an
  actual OS-level out-of-space write failure (a real full disk) --
  explicitly stated as needing real infrastructure the sandboxed
  environment that built it couldn't provide, and not simulated or guessed
  at.

**Relevance to this service's own ingest lag**: this service's
`ingest_export_event()` (`app/services/integration_inbox.py`) is the
consumer on the other end of that same outbox. The 256 MiB ceiling is the
real, tested bound on how much unacknowledged financial evidence
signal-copier will accumulate before an operator is alerted -- which is
also, in practice, the largest realistic backlog this service's relay
ingress could ever be asked to catch up on in one recovery pass. There is
no separate, this-service-side numeric SLO for how quickly it must drain
that backlog once delivery resumes.

### Non-live load-testing capacity profile

`spec/docs/13_operations_deployment_and_cost.md` states real, if
explicitly-labelled-as-benchmark, capacity numbers for non-live load
testing:

- 100 concurrent website users
- 1,000 total registered tenants
- 100 concurrent paid portfolio subscribers
- 20 sleeves and 10 products
- 10 normalized events/second sustained, with a 100-event burst

The same document is explicit these are "benchmark profiles, not a claim
of free-host capacity or authorized subscription capacity," and that a
deployment must "load-test at the selected cloud profile, identify
queue/broker/platform bottlenecks, and lower declared commercial capacity
if measurements fail; do not raise financial limits."

## What is explicitly NOT an established target in this codebase

- **No request-latency SLO** (e.g. "p99 API latency under Nms") exists
  anywhere in this codebase or the specs read for this document.
  `spec/docs/13_operations_deployment_and_cost.md` states the *principle*
  directly: "Core command latency/service SLO must be set from selected
  strategy/route and measured, not an arbitrary web SLA" -- i.e. this is a
  stated requirement to derive a real number from measurement, not a
  number that has been derived yet.
- **No uptime/availability percentage target** (e.g. "99.9% availability")
  is stated anywhere.
- **No this-service-side ingest-lag SLO** (e.g. "apply an event within Ns
  of relay delivery") -- `ingest_export_event()`'s correctness guarantees
  (ordering, idempotency, gap detection) are real and tested, but no
  numeric latency target for how quickly a received-but-parked event must
  be resolved is stated anywhere in this codebase.
- **No error-budget or burn-rate policy** is defined.

## Recommendation for anyone establishing real SLOs here

Per the spec's own stated principle, any latency/availability SLO for this
service should be derived from the actual selected strategy/route and
measured against the real capacity profile above (or a deployment's own
measured equivalent), not asserted from a generic web-service default --
this document intentionally does not invent numbers the codebase itself
has not established.
