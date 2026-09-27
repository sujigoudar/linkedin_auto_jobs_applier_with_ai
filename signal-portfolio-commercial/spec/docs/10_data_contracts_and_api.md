# Data contracts and commercial API

The schema files define application-owned interchange contracts. They are not a copy of vendor OpenAPI schemas and are not proof that external operations exist. Fetch and pin each official vendor contract during adapter implementation. Schema validation is only the first layer; cross-field numeric, identity, rights and effect invariants require the code/test checks specified here.

## Required entities and persistence keys

rights_grants: granting party+grantee+source product+contract version; no overlap automatically broadens permissions.
sleeves: provider+analyst+strategy+parser version+management policy+product profile.
portfolio_versions: immutable version ID, source-universe hash, components/cash, policy/research/report refs.
trial_runs/candidates: all evaluated/failed candidates, input/solver hashes, fold/outcome evidence.
releases: artifact+portfolio+channel+customer audience+rights+review signatures, effective/expiry.
publications: unique logical action/revision/destination, parent lifecycle, audience snapshot and remote effect family.
customers/tenants/memberships: verified identity with scoped roles; tenant from authentication, not caller-supplied authority.
subscriptions/entitlements: processor truth and exact paid-through intervals, immutable price version.
mandates/allocations: independent platform/customer permission, exact product/account/risk scope and termination policy.
deliveries: publication/customer/channel/revision unique, delivery attempts and final disposition.
metric_series/metric_values: immutable origin, accounting/valuation/cost version and quality.
managed_programs/capital_requests/fee_statements: broker-native authority and observed status, disabled unless approved.
review_actions/incidents/audit_events: actor, reason, immutable previous/new hashes and evidence.

Use UTC aware timestamps internally, integer basis points for weights, decimal strings for money and explicit integer quantity steps where products require. Preserve legal signed prices for explicitly compatible products; equity/FX price positivity must not be generalized to every future. Client-generated identity is validated for format but is never authorization. Use explicit version fields and optimistic concurrency on editable resources.

## API conventions

Prefix `/api/commercial/v1`. Public reads return only published projections. Customer/staff requests require verified role and tenant scope; sessions require CSRF protection for mutations. `If-Match` or expected_revision is mandatory for changes to a current draft, account setting or mandate. `Idempotency-Key` plus canonical request fingerprint is mandatory for payments, subscriptions, release/publish/wind-down and other effects. Same key+different body returns409. Expired/stale state returns409 with non-sensitive current revision; denied authorization returns403 or indistinguishable404 as appropriate.

List APIs use stable cursors plus snapshot/as-of identity, filters bounded and allowlisted. No browser supplies raw SQL, remote callback URL without validation, processor amount, staff role or external strategy identity it does not own. Responses include object ID, environment, version and timestamps. Errors return code, human-readable safe message, retry classification and correlation ID without secrets. All exports are tenant-bound, expiring and rights-filtered; caches key identity/entitlement/version and invalidate on revocation.

Endpoint inventory is in catalog/endpoints.json with role, exact input fields, response contract, expected effect, idempotency and acceptance tests. Actual OpenAPI must be generated from strict Pydantic request/response models in implementation, with no unrestricted catch-all payloads. API build completion requires every inventory row bound to an actual consumer and tests, and every discovered extra route added to the inventory. Public website and metrics/health routes are also inventoried for leakage and side effects.

## Event envelope

All internal events include event_id, event_type, schema_version, occurred_at, available_at, received_at, tenant_scope, environment, aggregate_id, aggregate_revision, causation_id, correlation_id, payload_hash, provenance, data_quality and typed payload. Never flatten a source edit into another independent entry. Event consumers deduplicate by semantic identity and record progress transactionally. Ordered delivery is enforced per aggregate while unrelated jobs remain concurrent. Out-of-order state-bearing vendor events trigger authoritative readback, not blind last-arrival-wins updates.

## Read versus effects

Creating a research draft can write control-plane data but never reaches a publisher. A model 'preview' stays non-actionable. A release approval is an authority change and must not be a hidden side effect of generating a report. Checkout creation can create a payment-session object only after allowed product/price validation; payment authorization cannot be reused as broker consent. Platform deletion/copy removal/cancel operations must be classified by their actual external financial consequences.
