# ADR-0010: A real release-maturity taxonomy and a fail-closed trading-authority qualification gate for `GET /system/readiness`

## Status

Accepted. Implemented in `app/services/release_taxonomy.py`,
`app/services/trading_authority.py`, and wired into the new
`GET /system/readiness` route in `app/main.py`.

## Context

This build had never computed a real answer to two questions a deployed
commercial platform must be able to answer honestly:

1. **Release maturity** -- where does a given `Product` sit on the path
   from "being drafted" to "live in front of real customers"?
2. **Trading authority (qualification)** -- does this platform's own
   stored state currently authorize a given `Product` to route a real
   customer order?

There was no `/system/readiness`-shaped route at all, and the fields a
prior track's own brief describes (`trading_authority`, `release_status`)
existed nowhere in this codebase to even be honest placeholders -- the
honest state, until this change, was simply that nothing here answered
either question. This ADR is about building the real thing, not about
replacing a stub.

This build already has two real, separately-decided pipelines these
questions must be grounded in, not reinvented alongside:

- `Product.lifecycle_state` (`app/models/product.py`) and `ReleaseReview`
  (`app/models/release_review.py`, AD-08) -- `DRAFT -> VALIDATED ->
  APPROVED -> PUBLISHED`. `app/services/release_review.py`'s own
  docstring is explicit that "release is not publication": approving a
  review moves a product to `APPROVED`, never to `PUBLISHED`, which
  stays "a wholly separate, later admission decision this model has no
  authority over." As of this change, no code anywhere in this
  repository ever sets a `Product` to `PUBLISHED` -- it is a reachable
  value in the data model, genuinely exercised by no command yet.
- `RightsGrant`/`check_rights`/`check_portfolio_rights`
  (`app/services/rights_registry.py`, `app/services/portfolio_rights.py`)
  -- fail-closed, recomputed-every-call, never cached.
- `CopyMandate` (`app/models/copy_mandate.py`) and `ManagedProgram`
  (`app/models/managed_program.py`) -- both models' own docstrings say,
  explicitly, that neither one has an ACTIVE/enrolled-live state:
  "activation needs a real scoped publisher/execution pipeline this
  build does not have." A customer-facing mandate or managed-program
  enrollment can exist in this build today only as a draft; it can never
  be the real authority behind a live order.
- `Incident` (`app/models/incident.py`) -- a real, tenant-scoped record
  of an open rights/payment/publication/customer-exposure problem.

`dashboard_spec/screens/AD-08.md` (release-and-change approvals) and
`AD-22.md` (commercial deployments and recovery, this ADR's own closest
screen spec, `Status: designed, not implemented`) both already name the
missing concept this ADR closes, without building it: AD-22-P05's own
panel contract reads "Qualification enumerates independently evaluated
conditions, actual outcome, reason code, evidence age and permitted next
step. **Do not collapse payment, connection, rights and trading
authority into one active badge.**" `spec/catalog/endpoints.json`'s
API-042 (`GET /api/commercial/v1/health/ready`, role `OPS`, request
field `scope`, response contract `ScopedReadiness`, `implemented: false`)
and the matching `CPT-087` test cases ("Surface distinct not-ready
product/route, no green trading-ready" / "Homepage HTTP200 qualifies
whole service" as the exact anti-pattern to avoid) are this feature's
real spec counterpart -- `GET /system/readiness` is this ADR's concrete
implementation of that spec intent, scoped by an optional `?scope=
<product_id>` query param exactly as API-042 describes.

## Decision

### Release taxonomy (`release_status`)

`app/services/release_taxonomy.py`'s `ReleaseStage` enum
(`RESEARCH_ONLY -> SHADOW -> LIMITED_LIVE -> FULLY_RELEASED`) is a pure,
total, 1:1 rename of the existing `ProductLifecycleState` ladder:

| `ProductLifecycleState` | `ReleaseStage`  | What is real at this stage |
|---|---|---|
| `DRAFT` | `RESEARCH_ONLY` | Being drafted/edited, or sent back from a rejected/changes-requested review. No independent reviewer has evaluated the current revision. |
| `VALIDATED` | `SHADOW` | Submitted for independent review (`compute_publication_blockers` was empty at request time) and awaiting a real decision -- evaluated against this build's real gates, zero customer-facing exposure. |
| `APPROVED` | `LIMITED_LIVE` | An independent reviewer approved this exact revision. Internal gates cleared; publication/admission to the public catalog has not happened. |
| `PUBLISHED` | `FULLY_RELEASED` | Admitted to the public catalog. Reachable in the model; as of this change, genuinely unreached by any command in this build -- reported honestly either way. |

No new column, table, or transition was introduced. This is deliberate:
inventing a second, parallel state machine next to the one
`release_review.py` already owns would create exactly the two-sources-
of-truth risk this codebase's own `eligibility.py` module docstring
warns against ("the decision is always computed live ... so it can
never go stale").

### Trading-authority qualification gate (`trading_authority`)

`app/services/trading_authority.py`'s `assess_trading_authority` is a
fail-closed, recomputed-on-every-call function over one `Product`,
returning a `TradingAuthorityAssessment(applicable, qualified, reason,
checks)`:

1. **Applicability.** A product whose `service_modes` contain neither
   `copying` nor `managed_program` never routes a real order at all
   (research/alerts distribution only) -- `applicable=False`,
   `qualified=None`, `reason="not_applicable"`. This is a distinct axis
   from a failed check: asking "does this have trading authority" is a
   category error for an alerts-only product, not a question with a
   `False` answer.
2. **`product_published`** -- `lifecycle_state == PUBLISHED`.
3. **`no_publication_blockers`** -- `product_admin.
   compute_publication_blockers` is empty (reuses the existing function;
   never reimplemented).
4. **`has_current_approved_release_review`** -- a `ReleaseReview` row
   with `state == APPROVED` and `object_revision_reviewed == product.
   revision` exists (a later edit makes an old approval stale, same
   invariant `decide_release_review`'s own `StaleReviewTargetError`
   enforces).
5. **`order_routing_rights_granted`** -- `check_portfolio_rights` against
   the `RightsUse` that actually matches each requested order-routing
   service mode (`AUTOMATED_PUBLICATION` for `copying`,
   `MANAGED_ACCOUNTS` for `managed_program`) -- deliberately NOT the
   `COMMERCIAL_ALERTS` use `compute_publication_blockers` itself checks,
   since a grant for alerts distribution is not evidence anyone granted
   rights to route a real order under a copy relationship.
6. **`no_blocking_open_incident`** -- no `Incident` with `state` in
   `{OPEN, ACKNOWLEDGED, ASSIGNED}` and `severity` in `{HIGH, CRITICAL}`
   naming this product as `affected_object_id`.
7. **`execution_activation_pipeline`** -- the one genuinely missing
   input. Every check above can, in principle, pass; this one
   structurally cannot, in this build, because `CopyMandateState` and
   `ManagedProgramState` have no ACTIVE/enrolled-live member at all.
   When every other check passes, this gate returns
   `qualified=False`, `reason="missing_input:execution_activation_
   pipeline"` -- never a fabricated `True`.

Each check's own PASS/FAIL outcome is recorded in `checks`, not just the
first failing reason, directly satisfying AD-22-P05's "do not collapse
... into one active badge" instruction.

### `GET /system/readiness`

A new route in `app/main.py`, gated by the same `view_deployment_status`
permission (`OWNER`/`PUBLISHER_OPERATOR`) AD-22 itself names. Reports,
for every one of the caller's own tenant's products (or one, via
`?scope=<product_id>`): `release_status` and a `trading_authority`
object (`applicable`/`qualified`/`reason`/`checks`). At the unscoped,
multi-product level, the top-level `release_status`/`trading_authority`
fields stay `None` rather than collapsing a tenant's products into one
rolled-up verdict -- the exact anti-pattern AD-22-P05 forbids -- while
`products` carries every real, per-product answer.

## Alternatives considered

1. **A single boolean `trading_ready` flag, hand-set by an operator.**
   Rejected outright -- this is precisely the "invented authorization"
   CPT-087's own `prohibited` clause and this repo's CLAUDE.md rule 11
   forbid. An operator-set flag with no computed basis could silently
   drift from the real underlying state (a revoked grant, a newly open
   incident) with nothing to invalidate it.
2. **Fold `trading_authority` into `release_status` as a fifth
   "LIVE_QUALIFIED" stage.** Rejected: this is exactly the "one active
   badge" AD-22-P05 says not to build. Release maturity (can this be
   shown to anyone) and order-routing authority (can this route a real
   order) are independent axes in this build already -- a `FULLY_
   RELEASED` product can, and in this build always does, have
   `trading_authority.qualified=False`.
3. **Add a new `ExecutionAuthorization` table now, so the gate has
   somewhere to report `qualified=True` once `CopyMandate`/
   `ManagedProgram` activation exists.** Rejected for this change:
   inventing persisted structure for a capability this build does not
   have yet (no real broker/execution pipeline) would be exactly the
   "scaffolding around nothing" this codebase's own `docs/KNOWN_ISSUES.md`
   and `app/main.py` module docstring explicitly say this build avoids.
   When a real execution-activation pipeline is built, this gate's own
   step 7 is the one, named place to update.
4. **Compute `trading_authority` per customer `CopyMandate`/
   `ManagedProgram` row instead of per `Product`.** Considered, but
   every such row in this build is structurally `draft`/`cancelled` (no
   ACTIVE state exists), so a per-mandate gate would report the
   identical `missing_input` answer for every row, with strictly less
   information than reporting it at the `Product`/route grain CPT-087
   itself asks for ("Surface distinct not-ready product/route").

## Consequences

- `release_status` is now real and computed, for every product, with no
  new persisted state.
- `trading_authority` is now real, fail-closed, and computed fresh on
  every read. It is structurally unable to report `qualified=True` for
  any order-routing product in this build today -- an accurate
  reflection of this build's real capability, not a bug. The one named
  place this will change is `assess_trading_authority`'s own step 7,
  once a real `CopyMandate`/`ManagedProgram` activation pipeline exists.
- `GET /system/readiness` gives AD-22's own "Qualification" panel (still
  itself unbuilt, per its own `Status: designed, not implemented`
  marker -- out of this change's scope) a real read model to bind to
  when it is eventually built.
