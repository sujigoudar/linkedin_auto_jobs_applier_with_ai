# Architecture and isolation

## Chosen topology

Keep one repository and the existing private execution application. Add a separate `commercial_api` process and a `portfolio_worker`/`publication_worker` role using shared reviewed Python packages and build artifacts. Do not expose the private owner application's account/flatten endpoints to public customers. Reuse current HTML/JS, Chart.js and Tabulator assets with public/customer/operator shells; no frontend-framework rewrite is required.

Public/customer control plane: PostgreSQL with Supabase Auth and row-level security is the target for new customer identity, subscriptions and product records. This is justified by the new multi-customer scope, not a retroactive assertion that Supabase exists in the current repository. Use an isolated authorized project/schema with appropriate database roles; do not repurpose a project with unrelated writers without explicit isolation review. Local tests use disposable PostgreSQL and a controlled JWT issuer. Login consent and an actual hosted Auth project are external deployment tasks. Do not migrate or reset the owner's current SQLite execution store simply to introduce customer accounts.

Private execution: retain the corrected current store and writer. Export reviewed immutable execution/metric events through a narrow outbox/projection bridge. The commercial API cannot query broker keys or modify this database. Customer performance observations are independently sourced and tenant-scoped; private owner data is never a customer's default dataset.

Publisher: consume only released `PublicationIntent` records in the commercial database. It holds a separate approved strategy-publisher key, never a broad customer's brokerage credential by default. External C2/eToro strategies can affect real followers; a publisher is a financial-effect component even when it sends only 'signals'. One publication owner per strategy/account/channel, enforced by durable claims and actual fencing. No public HTTP worker, billing webhook handler or LLM may call a broker or publisher directly.

Research: read immutable authorized Parquet/snapshot data using the existing numerical stack. Dedicated processes have bounded CPU/memory and no trade keys. No research query runs against mutable live financial projections without a consistent snapshot. Optional model calls receive only permitted redacted data and have no execution tools.

## Tenant model

Objects: `tenant`, `user_identity`, `membership`, `customer_profile`, `product`, `product_version`, `portfolio_version`, `subscription`, `entitlement`, `copy_mandate`, `subscriber_allocation`, `delivery`, `customer_execution_observation`, `report`, `support_case`. Operator roles are separate memberships: owner, researcher, reviewer, publisher_operator, billing_operator, support_readonly. A billing operator cannot release a strategy; researcher cannot grant rights; customer cannot query another tenant; support cannot view broker credentials.

Use opaque identifiers, authenticated principal-derived tenant scope, SQL parameterization and RLS. Never accept a browser tenant ID as proof of access. All child rows have compound foreign keys or equivalent constraints preventing cross-tenant parent references. Public products/reports are sanitized published projections, not broad table reads. Service-role keys bypass RLS and stay server-only with narrower DB roles where possible. Test API authorization and DB row policies independently, including exports, stored objects, SSE, task results and signed URLs.

A customer's registered identity is not automatically an approved trading customer. Email verification, appropriate residence eligibility, accepted agreements, platform identity, exact account mandate and active product entitlement are separate conditions. Withdrawal/custody permissions are not requested. Secrets are encrypted under role/site-separated keys; token revocation is independent of subscription status.

## Persistence and effect contracts

Every original signal has stable provider/channel/message/revision/time identity. Immutable portfolio versions pin component/parser/policy/data references. Publisher intents have unique `(channel, external_strategy, portfolio_version, logical_action, revision)` keys plus body hash. Commit local intent and entitlement/audience snapshot before effects. Attempt records retain request hash, secret-free correlation, unknown/accepted/rejected status, external IDs, observed child families and reconciliation requirements.

Use short transactions and durable job records. No Redis, Kafka, Temporal or Kubernetes is required initially. Small worker pools claim jobs with transactions and lease ownership for scheduling, while financial fencing is independent. A lost lease never proves a previously running publisher can no longer send. Parallel workers may research disjoint candidates, deliver nonfinancial email and render reports; only the selected writer issues strategy-changing commands.

## Environments

LOCAL_SIM, INTEGRATION_ISOLATED, PLATFORM_DEMO, PRIVATE_SHADOW and COMMERCIAL_LIVE are disjoint deployment identities. Database records, secrets, domains, storage and event buses carry environment. No promotion by flipping a restored row. Customer and platform demos must not subscribe to a live strategy. Production events cannot be replayed into publishing during tests. Historical content can create a replay job, never an actionable entry event.

Infrastructure inherits the guarded primary/standby design. Supabase control-plane and commercial worker availability do not imply private execution failover. Independent backup, identity and fencing tests apply per financial writer. Free tiers are candidate development resources, not contractual uptime guarantees. Sources: SRC22, SRC24, SRC25; existing audited deployment obligations remain.
