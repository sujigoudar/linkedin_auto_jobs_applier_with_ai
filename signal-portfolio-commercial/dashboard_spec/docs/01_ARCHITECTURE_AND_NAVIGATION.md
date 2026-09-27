# Architecture and navigation

## Four shells, three authorities

Private trading: keep the existing `signal-copier` process, database, owner session and sole account writer. Serve its trading pages at `/trade...` on a restricted private origin. No customer credential may reach it. Customer and staff private-account navigation links are absent; a commercial owner role does not inherit private execution authority.

Commercial public/customer: use the existing `signal-portfolio-commercial` FastAPI process with Jinja2 templates, local ES modules, existing PostgreSQL/auth/RLS services. Public pages are `/`, `/portfolios`, `/pricing`, `/methodology`, `/status`, `/help`. Customer pages are `/app...`; auth is `/auth...`. Browsers call same-origin scoped APIs, not the execution database.

Commercial staff: `/ops...`, using the same commercial service but explicit action membership and separately scoped projections. Researcher, reviewer, publisher_operator, billing_operator and support_readonly are not a role hierarchy. Unknown actions deny everyone. Cross-tenant staff operations require explicit purpose/scope; support cannot impersonate customer sessions or read private brokerage secrets. At larger scale the staff origin can be separately restricted without changing service contracts.

Reuse a small shared `ui-common` source directory for CSS tokens, icons, Jinja macros and ES modules. Build/copy versioned assets into each existing app. Do not run a separate frontend server, add a new UI database or dynamically import the other app's financial modules. Share rendering only, not credentials, authority or mutable domain state.

## Navigation structure

Private top groups: Overview; Trading (Positions, Signals, Orders); Setup (Accounts, Sources, Routing, Policies); Analysis (Performance, Backtests); Operations (Incidents, System).
Customer top groups: Overview; My Portfolios; Alerts; Performance; Connections; Billing; Settings. Managed programs and API access appear only when product applicability permits, with an explanatory unavailable view rather than false activation. Persistent safety/operation-status links remain accessible during payment/connection incidents.
Staff top groups: Overview; Research (Universe, Lab, Runs, Compare); Products (Versions, Reviews, Content); Distribution (Publishers, Publications); Customers (Support, Eligibility); Business (Billing, Economics); Managed Programs; Governance (Rights, Access, Audit); Operations (Integrations, Incidents, System, Settings).

Detail pages are deep-linkable. Drawers mirror a route/query state so reload and Back retain context. A row click opens details; selection checkboxes do not execute. Browser Back must not resubmit a POST. Changing account/tenant/environment cancels old requests and clears old data before new scoped data arrives.

Do not render all66 pages in the sidebar. Use these groups, context-specific subnavigation and detail routes. Public identity/eligibility screens form a step flow, not staff navigation.

## Dependency policy

Reuse current pinned Chart.js/Tabulator. Add Jinja2 templates if absent. Native HTML inputs and explicit ES modules are default. Tom Select is only for large remote-scoped selects. GridStack is an optional later visual-layout enhancement with equivalent move buttons. No React/Next.js/Dash/AdminLTE/SaaS boilerplate backend or arbitrary plugin catalog is required. Check component license and actual compatibility before copying assets. Do not copy unsafe innerHTML or inline action patterns from an existing dashboard.
