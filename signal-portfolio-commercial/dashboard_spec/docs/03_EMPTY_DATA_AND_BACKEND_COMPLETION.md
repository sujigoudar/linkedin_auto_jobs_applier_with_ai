# Empty-data behavior and minimum real backend

A production-safe first vertical slice is:empty database ->owner creates Product draft ->draft reloads after restart ->public catalog still empty ->private preview shows missing research/rights ->unauthorized release rejected. This is fully testable without a real investment product.

Implement these real persisted entities when absent, with current tenant conventions and migrations:Product;PortfolioVersion;SleeveMembership/weights;PublishedProjection;CustomerSelection;UiDraft;UiPreference;ScopedOperation;MetricArtifact/series;ExportJob;CustomerNotice;SupportCase;IntegrationQualification. Reuse existing RightsGrant,PublicationIntent,Membership,Subscription,ledger and approval structures. Each new multi-tenant FK includes tenant identity or an explicit authorized global-public relation. RLS is enforced under a non-superuser application role.

Product may be DRAFT with no sleeves,report or rights grant. Version can be DRAFT_INCOMPLETE. It cannot be ELIGIBLE_FOR_REVIEW until validation passes. Review approval requires exact evidence; publication requires separate current rights/audience and publication permission. Do not insert a fictitious approved version to make the public website look populated.

Public catalog query reads only published audience-safe projections, not raw product rows. No matching products yields200 items=[] and exact empty state. Failed database yields503 and error state, not200 empty. Restricted/withdrawn direct references use approved archived explanation or404 according to rights. Private draft preview has separate permission/no-store and explicit DRAFT labels. It cannot be cached or indexed as public content.

Metric artifacts carry source book,definition,currency,period,version,data cutoffs,quality and evidence. Incomplete data produces unavailable/partial, never computed optimistic performance. Simulation fixtures use separate database/issuer/adapters/domains and origin labels. Seeding production is prohibited by an environment guard, identity check and tests.

Customer identity can exist before any product. Overview offers eligible next steps and shows no copied positions. Saved preferences, support,verification and eligibility work normally. Hosted checkout and external authorization return clear NOT_CONFIGURED blockers until real entitlement exists. Those blockers do not justify omitting implemented forms, draft services,local protocol boundaries or their tests.

Stripe receipt storage must distinguish RECEIVED,VERIFIED,QUEUED,APPLIED,IGNORED,FAILED. The existing route's processed flag currently means a new event record; do not show it as applied billing. Implement canonical processor-state handling before entitlement UX claims active payment. External test-mode verification stays separate from local contract tests.

Required compatibility decision:retain `/api/v1/me` and `/api/v1/billing/webhook/stripe`. Update inherited unimplemented `/api/commercial/v1` bindings to the chosen prefix in the traceability map. Never add a duplicate effect route to satisfy a filename. A schema/model/helper is not complete until its actual screen/API/DB/worker call path is tested.
