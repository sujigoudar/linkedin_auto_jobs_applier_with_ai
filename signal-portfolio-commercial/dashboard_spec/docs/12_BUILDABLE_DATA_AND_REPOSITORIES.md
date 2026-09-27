# Concrete service work needed to make the screens buildable

The three current routes are not enough. Reuse the existing SQLAlchemy tenancy, rights, subscriptions, publication and policy components. Add missing fields/tables with reviewed migrations instead of returning fixed lists from a UI adapter.

## Minimum model set

Commercial records: Product, PortfolioVersion, PublishedProjection, VersionReview, CustomerSelection, DeliveryPreference, Connection, Mandate, UiDraft, UiPreference, UiOperation, ResearchRun, ResearchCandidate, ReportSnapshot, ExportJob, SupportCase, Attachment, ContentDocument, AuditEvent, IntegrationCapability, DealingRequest and RecoveryEvidence. Reuse existing Membership, CustomerProfile, RightsGrant, Subscription, PublicationIntent and ledger models. A row definition alone does not satisfy the workflow; each screen must exercise repository calls and actual service validation.

Every tenant-owned relation includes tenant identity and composite foreign keys where necessary. Version/revision fields are required for updates. Immutable evidence records reference their source hashes and parent IDs. Principal, membership and allowed scopes are server-derived; accepting a client tenant_id is not authorization. Use transaction-local RLS scope, never a pooled connection that retains the last customer's scope.

Private views read the existing order/execution/lifecycle/accounting stores. Where that store cannot establish a field, return an explicit data-quality reason. Never query commercial PostgreSQL to invent private broker state. Domain defects found while wiring the UI are separate release blockers, not patched by formatting a number.

## First actual vertical slice

Start two isolated applications and disposable databases without live credentials. Sign in as a staff test principal through the controlled issuer. Open Products; the repository returns zero rows. Save a real draft named by the test. Refresh/restart and read the same stored ID/revision. Open a private preview: show incomplete research/rights/platform prerequisites. Open the public catalog from an anonymous browser: still zero published products. Attempt a cross-tenant draft URL: scoped not-found. Add missing evidence only through test fixture/service APIs in the isolated database; never auto-seed production. This is a complete, useful implementation, not scaffolding around nothing.

## Remaining read paths

Each catalog/api_contracts.json READ entry delegates to an explicit registered query service. Page ID does not select arbitrary SQL. Parameters identify the exact object on detail pages, allowed filters, cursor, sort and context; path/query objects are authorized before revealing existence. Return the matching screen schema plus capabilities derived from current state. List, chart and export use the same snapshot and metric registry.

Empty query tests cover zero rows, one draft, one released fixture, many rows, filtered-to-zero, no permission and unavailable database. Only the first/fourth valid outcomes are list emptiness; operational failures retain error/partial state. Never swallow a backend exception and return an empty list.

## Command paths

Form draft/validate/preview/confirm endpoints are explicit registered handlers, not a generic arbitrary-service executor. Existing domain endpoints can remain canonical if their exact semantics match; document a single adapter mapping and remove duplicate effect paths. The contract catalog records planned paths and statuses, not deployed endpoints. Every successful response must correspond to a committed domain action or named asynchronous operation. All declared buttons have a binding in catalog/actions.json.

Positive local protocol integration is possible without live accounts: actual routes, issuer, database, queues and client serialization are tested against controlled external service behavior. The external live/test service qualification is a separate evidence record. Do not stub a success in production when that integration is unavailable; return its exact blocker while the rest of the interface remains usable.
