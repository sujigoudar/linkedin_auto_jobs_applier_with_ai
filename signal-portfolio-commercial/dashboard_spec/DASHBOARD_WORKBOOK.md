# Complete dashboard implementation workbook

# Dashboard design and implementation package v2

For the existing Signal Copier and Signal Portfolio Commercial applications. Read-only baseline inspected: `2de2d7e2522af2554d438de580a5e0635e23899b`. This package does not modify either application.

1. Open `SCREEN_ATLAS.html` locally to inspect all 66 screen layouts, configuration fields, states, permissions and API bindings. It is an offline design reference, not the application or a source of performance data.
2. Open Claude Code in the genuine current repository. Put this directory under documentation/specification storage, not over app files. Paste `MASTER_PROMPT.md`.
3. Merge the namespaced `.claude/skills/build-signal-dashboards` and reviewer file only after inspecting existing instructions; never replace the whole `.claude` directory. The master prompt works without installing a skill.
4. Follow the 12 phases. Implement real query and command paths against legitimate empty datasets and private drafts immediately. Missing real released portfolios is not a UI blocker.
5. Bind and execute every selected case through the actual apps. `tests/test_pack.py` and the atlas tests validate only this delivery,not production behavior.

Important files: `screens/*.md`, `catalog/forms.json`, `catalog/actions.json`, `catalog/api_contracts.json`, `catalog/subaction_permissions.json`, `catalog/metrics.json`, `catalog/cp_traceability.json`, `catalog/journeys.json`, `catalog/test_cases.json` and `docs/00...14`.

Design counts: 66 screens; 41 forms; 284 individual form fields; 170 visible action bindings; 280 proposed/current API bindings; 1270 test specifications; 9 browser/viewport projects. These are design/testing obligations,not delivered application features or passed application tests.

Existing privacy,execution safety,source rights,merchant/platform/legal and owner release gates remain. No real orders,charges,publication,cloud purchases or production resets are authorized by installing this package.



---

# Baseline and interpretation correction

This is dashboard specification v2, reviewed27September2026 against PR1 head `2de2d7e2522af2554d438de580a5e0635e23899b`. The commercial main file has healthz, scoped me, and Stripe receipt routes. The inspected validation report records CP-091..114 unimplemented. The private copier has a different existing owner dashboard. This delivery does not re-audit its trading engine or install anything into the repository.

The lack of released products is NOT a backend or design prohibition. A real public catalog query can return an empty list. An operator can create a real Product draft with no research report. A private draft preview can show missing evidence. These are working domain behaviors, not fake performance. Zero is shown only for a counted empty list or verified zero metric, never a missing balance.

Build models/migrations/repositories and actual handlers for products, portfolio versions, projections, selections, drafts, operations, UI preferences and reports as needed. Reuse existing models when compatible. Do not return hardcoded empty JSON forever and call the screen complete. Test with real disposable PostgreSQL and labeled synthetic fixtures; production must never seed those fixtures. Fixtures cannot be published, charged or used by a live writer.

This pack defines66 screens and41 forms across private trading, public/identity, customer and commercial-admin contexts. It expands the original24 commercial GUI journeys and preserves CP-001..114. Its APIs are proposed internal application bindings, not vendor endpoints or proof of implementation. All application acceptance starts NOT_RUN.

The design atlas is an offline empty-state layout/specification viewer, not a functioning trading app, authenticated site, dataset or evidence of real metrics. Tests of the atlas establish only its rendering and navigation. Integrate the designs into actual services; do not ship the atlas as a replacement application.


---

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


---

# Visual and interaction design system

Use design/tokens.json and design/tokens.css as the exact initial design values. Dark and light themes share hierarchy; customer/public theme follows system by default, private trading may default dark but user choice persists. System fonts only; no font download dependency. Financial numbers use tabular numerals. Local icon subset, decorative icons aria-hidden; explicit text on financial controls.

At>=1200px:240px sidebar,64px topbar,24px gutters,12-column content max1600px. Public pages max1200px. At768..1199:collapsible sidebar,8-column content. At<=767:one-column main with16px gutters; context before cards; bottom safe-area padding; no offscreen command confirmations. Critical financial facts never disappear behind optional tabs. At320 CSSpx and200% zoom controls reflow; large tables use an explicit labeled horizontal scroll region, not page-wide overflow.

Base body16px; tables14px; captions12px minimum; page headings28px; section headings18px. Control hit area44x44px design target. Focus3px with sufficient contrast and offset. Sticky bars respect focus scroll margins. Dialogs return focus to the invoking control, allow Escape for noncommitting dismissal and never trap users behind a failed request. No animations that imply financial success; reduce-motion respected. No blinking tickers or decorative3D particle fields.

A workspace header always shows relevant account/customer scope, environment, site role, origin/book and as-of. No universal green 'ready' badge. Use separately named indicators:Authenticated;Source connected;Data current;Financially qualified;Mandate active;Publisher running;Protection confirmed. Missing prerequisite shows reason. Positive color means observed positive economics or verified success, not a selected tab.

Metric cards contain label, value or em dash, unit/currency, period, origin, quality and definition link. Missing value gives 'Not available' plus reason. Show0 only if explicitly returned as observed0. Ratios use decimal storage and percentage rendering once; count/quantity use instrument precision. Rounding for display cannot be re-used in an order request.

Tables use snapshot/cursor-backed paging25(default)/50/100, stable sort plus immutable ID, allowlisted filters and safe text. No 'All' to load unbounded data. Show 'at least' or omit total if total_rows unknown, never estimate a financial count from pages. Refresh offers 'N new records' rather than moving the selected row during action. Saved columns cannot remove instrument/account/origin/quantity units in financial tables.

Charts are server-generated economic series rendered by Chart.js. They include labeled axes/currency/timezone, accessible table, legend and quality flags. Drawdown and fee basis remain visible alongside returns. No synthetic extrapolation, forward-filled missing marks, browser-side financial totals or incompatible merged series. Decimation changes only pixels; metrics are calculated on full qualified source series. A valid empty chart displays the screen-specific explanation, not a flat zero line.

Forms use visible labels, help and inline field errors linked by aria-describedby, plus an error summary focused after failure. Required/optional is textual. On-blur validation begins after first interaction; all server checks repeat. No premature errors while typing a decimal. Enter may submit only current safe step, never final financial approval from any field. Financial final button names exact operation and scope; no generic 'OK'.

Customization separates personal display, workspace defaults and financial policy. Personal theme, density, timezone, optional panel positions and saved filters have allowlisted schemas. Financial policies always use versioned draft/diff/review. Mandatory identity,mode,fees,staleness and incident panels cannot hide. Reorder via Up/Down controls before optional drag. Do not accept arbitrary JS,CSS,HTML,SQL or Python as customization.


---

# Empty-data behavior and minimum real backend

A production-safe first vertical slice is:empty database ->owner creates Product draft ->draft reloads after restart ->public catalog still empty ->private preview shows missing research/rights ->unauthorized release rejected. This is fully testable without a real investment product.

Implement these real persisted entities when absent, with current tenant conventions and migrations:Product;PortfolioVersion;SleeveMembership/weights;PublishedProjection;CustomerSelection;UiDraft;UiPreference;ScopedOperation;MetricArtifact/series;ExportJob;CustomerNotice;SupportCase;IntegrationQualification. Reuse existing RightsGrant,PublicationIntent,Membership,Subscription,ledger and approval structures. Each new multi-tenant FK includes tenant identity or an explicit authorized global-public relation. RLS is enforced under a non-superuser application role.

Product may be DRAFT with no sleeves,report or rights grant. Version can be DRAFT_INCOMPLETE. It cannot be ELIGIBLE_FOR_REVIEW until validation passes. Review approval requires exact evidence; publication requires separate current rights/audience and publication permission. Do not insert a fictitious approved version to make the public website look populated.

Public catalog query reads only published audience-safe projections, not raw product rows. No matching products yields200 items=[] and exact empty state. Failed database yields503 and error state, not200 empty. Restricted/withdrawn direct references use approved archived explanation or404 according to rights. Private draft preview has separate permission/no-store and explicit DRAFT labels. It cannot be cached or indexed as public content.

Metric artifacts carry source book,definition,currency,period,version,data cutoffs,quality and evidence. Incomplete data produces unavailable/partial, never computed optimistic performance. Simulation fixtures use separate database/issuer/adapters/domains and origin labels. Seeding production is prohibited by an environment guard, identity check and tests.

Customer identity can exist before any product. Overview offers eligible next steps and shows no copied positions. Saved preferences, support,verification and eligibility work normally. Hosted checkout and external authorization return clear NOT_CONFIGURED blockers until real entitlement exists. Those blockers do not justify omitting implemented forms, draft services,local protocol boundaries or their tests.

Stripe receipt storage must distinguish RECEIVED,VERIFIED,QUEUED,APPLIED,IGNORED,FAILED. The existing route's processed flag currently means a new event record; do not show it as applied billing. Implement canonical processor-state handling before entitlement UX claims active payment. External test-mode verification stays separate from local contract tests.

Required compatibility decision:retain `/api/v1/me` and `/api/v1/billing/webhook/stripe`. Update inherited unimplemented `/api/commercial/v1` bindings to the chosen prefix in the traceability map. Never add a duplicate effect route to satisfy a filename. A schema/model/helper is not complete until its actual screen/API/DB/worker call path is tested.


---

# Security and authority

Commercial browser authentication uses the existing Supabase-compatible JWT issuer and tenant/auth services behind a same-origin BFF. The BFF validates issuer,audience,signature,expiry and mapped membership, then creates an opaque revocable HttpOnly Secure SameSite=Lax browser session. Store provider credentials/tokens server-side encrypted as required. Bind login/callback to server nonce/PKCE where applicable. Do not place access/refresh tokens in localStorage,URLs,page source or analytics. Test issuer is allowed only in sealed non-live environment; no query flag selects mock auth in production.

Browser mutations require session-bound CSRF plus exact Origin checks. Sign-in/reset endpoints use same-origin pre-auth protection and throttling. Provider callbacks and Stripe ingress use their own verified protocol, not browser CSRF, but are narrowly allowlisted exceptions. Cookies are host-only; no shared parent-domain session connecting private and commercial surfaces. No private writer API proxy or service-role database key in the public commercial process.

Every screen,query,export and action requires object-level authorization. Role allowlists in screen catalog authorize only specified read projections. Form/API permission is separate, plus tenant/object,environment,rights and state checks. Membership role OWNER has no blanket bypass. Customer IDs cannot be read from untrusted body tenant_id; support must hold audited case-scoped grant. Denied cross-tenant objects return404 without names/counts.

No raw credential reveal screen. UI manages labels,scope,expiry and secret references. Where a platform lacks a hosted flow, a dedicated audited credential-entry transport may be implemented after its schema is qualified; never use generic config JSON or support attachments. Do not add a privileged credential input merely to demonstrate the design.

Safe rendering is mandatory:server autoescape;client textContent/DOM creation;no untrusted innerHTML or inline onclick strings. Static CSP should allow only reviewed same-origin assets;prefer external scripts/styles. Hosted provider links are top-level allowlisted redirects;no broad wildcard connect-src. No public cached private projection or HTML carrying Set-Cookie. Private data and drafts use no-store and no service-worker offline caching. Test cached responses across two tenants and changed memberships.

Downloads/attachments:private quarantine,content/size verification,scan before access,non-executable served MIME,attachment disposition,scoped short-lived authorization. CSV cell formula neutralization must not change archived economic values; JSON remains exact. No uploaded support file becomes an executable page or user-supplied template.

Sensitive financial changes use recent step-up<=300s plus signed server preview<=120s. Preview is revalidated at submit and at effect. It may persist an audit/preview artifact but creates no position,buying-power reservation,publisher effect or entitlement. Late response/status lookup uses original operation ID; no blind retry. Existing-position management survives UI logout. Customer billing failures cannot bypass or destroy lawful safety obligations.


---

# API and state contracts

`catalog/api_contracts.json` is the complete proposed UI binding inventory; it is not a vendor API or a claim the routes already exist. Specific UI read handlers may call existing scoped domain queries rather than duplicating data. Do not implement arbitrary runtime screen->SQL or generic model CRUD. Each endpoint is a reviewed allowlisted function.

Screen reads accept only the listed filters,opaque cursor,limit25/50/100 and allowlisted sort. Detail read uses named object ID in the query/path binding corresponding to the page. The response conforms to its generated read-model schema:screen_id,state,snapshot_id,schema_version,generated_at,origin_label,summary,typed rows,metrics,capabilities,next_cursor. Fields are omitted only where the schema allows; missing economic values use null with quality/reason. Snapshot identity binds account/tenant,environment,filters,sort,scope and schema revision. Private identity is not exposed in public projections.

Columns use Cell values with kind/value/unit/currency/quality. Financial value is canonical decimal string or null. A frontend never accepts preformatted arbitrary HTML from a Cell. Known status vocabularies are server-reviewed enums;unknown statuses render Unknown and cannot imply success. Chart data is an additional typed domain series projection with per-point timestamp,value,quality and common metric definition,never a second financial calculation in JavaScript.

Draft POST request wrapper:{draft_id:null or owned ID,expected_revision:null or current revision,values:<partial form-shaped object>}. Response201(new) or200(updated):draft_id,revision,status=DRAFT,values,validation_errors,missing_fields,updated_at. Unknown properties fail422. Draft permits missing required business fields but rejects invalid present types. Draft updates with old revision fail409. Identity/password/challenge forms never use persisted draft endpoint.

Validate POST:{expected_revision}. Return200 with valid_fields,missing_fields,blocked_gates and ready_for_preview boolean. A valid form may have external gates blocked. Preview POST:{expected_revision,requested_action_id}. Return expiring ActionPreview with frozen object/scope hash,gates,costs,consequences,read snapshot and step-up requirements. It reserves no financial capacity. Confirm POST:{preview_id,expected_revision,idempotency_key,stepup_challenge_id if required,acknowledged_consequence_ids}. Reject arbitrary values that were not in preview. Server reloads authorized values and current state.

Operation response:200 only for synchronously committed local outcome;202 for durable pending operation. Operation states are accepted,queued,running,awaiting_external,unknown,reconciling,completed,rejected,failed,canceled. `completed` means this operation's actual responsibility completed, not that every external trade filled. Preserve external sent/accepted/working/filled states separately. Idempotency binds principal,tenant,account/environment,action,object/version,cohort and request fingerprint. Same key/different fingerprint ->409;repeat same ->same operation.

Errors:400 malformed syntax;401 expired identity;403 prohibited action;404 concealed object;409 revision/idempotency conflict;422 field/domain invalid;429 quota with retry hints;503 unavailable dependency. Return Error schema with safe code,message,field errors,correlation ID and existing operation ID when known. A business gate blocked should be a structured blocker,not500. No catch-all200{success:true}.

Stable pagination:server snapshots + cursor. Tabulator's page-number adapter can retain a local map of visited page->cursor;no arbitrary jump to unknown page. Unknown total omits pages/total counts;do not let Tabulator infer financial counts. Switching filters invalidates cursor. New live records show refresh indicator;selection remains bound to its original snapshot.

Request coordinator:abort previous GET on scope changes;discard responses with obsolete request generation even if abort races. One request per screen resource;at most4 concurrent visible-page reads by default. GET timeout10s,at most2 retries with jitter/rate-limits. No automatic effect retries. Hide tab stops UI polling,never workers. Unload aborts listeners and destroys chart/table instances. No refresh interrupting text entry or resetting selected cohort.

SSE is a later optional transport,not required for first complete UI. When enabled use same-origin session,revalidate membership,resource-scoped event IDs,cursor/replay and snapshot reset on gaps. Never claim an EventSource reconnect proves all events arrived. An equivalent polling implementation must pass the same snapshot and duplicate-effect tests.

Existing vendor contracts remain exact. Do not name proposed internal endpoints as actual Collective2/eToro/Stripe methods. Resolve provider hosted auth and mode from current authorized docs. External inability blocks that effect and qualification,not the local route implementation or browser tests.


---

# Metrics and financial display contract

Keep five record categories separate:SOURCE reports;MODEL replay;PLATFORM model/account records;FOLLOWER actual customer executions;BUSINESS subscription accounts. The UI book selector must not call MODEL results 'your return'. Customer actual data unavailable ->not available, even when model history exists. Historical composites stay hypothetical. Public use is restricted to approved report projections and substantiation.

Realized P&L uses the application's declared reporting lot convention on actual executions and actual known fees. Unrealized P&L needs qualified current marks and currency conversion. Gross,net trading and subscription-cost-adjusted results have separate definitions. Missing fees are not zero;no claim of net result unless cost scope is known or explicitly partial. Deposits/withdrawals change account equity,not trading profit. TWR/MWR require their declared valuation/cashflow timing;do not invent them from daily balances.

Trade win rate counts completed strategy episodes,not reducing fills or profitable bars. A100-unit trade closed40+30+30 is one completed episode. Display episode count and unresolved episodes. Profit factor with no completed trades is unavailable;with verified profits and zero gross losses label denominator-zero rather than a giant finite score. Maximum drawdown requires coherent cashflow-adjusted series and is computed before visual decimation. A stop exit can be profitable. Risk/reward R needs original declared risk,not later tightened stop.

Exposure distinguishes signed net,gross,allocated,cash,borrow/margin and pending commitments. Instrument multipliers,contract sizes,negative-price profiles and account currencies must remain exact. Underlying/sector clusters are context,not permission to net incompatible instruments. Generic dashboard formulas must not price options or inverse contracts as shares.

Required quantity components in TR-03/CU-05:owned_confirmed,entry_remaining_possible,exit_remaining_possible,native_covered,uncovered_under_recipe,working_order_family and data_as_of. No `protected=true` based only on a plan's stop price. Counterexample fixture:owned54,working stop47,pending TP remainder7;stop54 is ineligible until that remainder's outcome is resolved. Late extra3 fill ->owned51. Render those facts and the precise blocking operation,not a generic spinner.

Business revenue,refunds,royalties and infrastructure costs never aggregate with investor capital. Managed NAV/unit/HWM screens use qualified broker program rules;unknown fee/cashflow conventions block those calculations. No SaaS page collects managed investment deposits.

Chart formatting only:financial values stay canonical strings in API;rendering code converts validated finite values for coordinates without feeding them back into an economic calculation. Tooltips show exact value and quality. Negative/positive signs,currency and units retained in CSV/PDF/table alternatives. A stale last-good value is labeled last verified at a timestamp. Known-flat requires a complete authoritative inventory snapshot.


---

# Configuration precedence and customization

A setting has:setting_id,scope_type,scope_id,revision,declared_value,origin,effective_value,hard_bound,applies_to,state,review_id,updated_at. Product/account legal hard limits are intersected;lower-scoped settings can narrow permitted behavior but cannot widen it. Unset means inherit,not0,false or unlimited. Explicitly disabled is distinct from missing. Reset-to-inherit is a named operation with a diff.

Three tracks remain separate:
1.Personal view:theme,density,time zone,columns,optional panel order,filters and bookmarks. Save is local user-scoped state only.
2.Operational config:collector/account labels,notification destinations,quota profiles and source routing. Save draft;validate;review when it affects execution boundaries.
3.Financial/product config:sizing,stops,trail,target meanings,holding rules,portfolio weights,prices,mandates. Immutable released versions and explicit applicability. No 'Save' button silently activates new financial behavior.

Forms and284 field definitions are in catalog/forms.json. Their default numbers for research reflect inherited research conventions,not live approved limits. Live money/risk limits start unset unless loaded from an existing approved policy. Product draft defaults cash10000bps only as an incomplete empty composition;not published as a real investment portfolio.

Customer personalization cannot create a new strategy or alter a provider signal. Copying settings are constrained by exact account/platform/product approval. Product choice,billing entitlement,platform connection and mandate have independent state machines. Subscription upgrade does not start trades;payment failure does not cancel native protection. Marketing quiet hours do not imply safety alerts can be dropped.

Every operational change shows affected future entries,open episodes,rights,customers and external channels. Default financial-version change applies only to future entries. An existing-position adjustment has its own preview and execution recipe. Credentials are references selected from verified server metadata,not free-text exposed secrets.

Layout permissions:fixed safety block is always first;optional widgets can be reordered,hidden or reset from allowlist. Each layout has schema_version and migration from older preferences. Do not erase saved view on additive column changes. Unknown widget IDs are ignored with a visible layout reset suggestion,never used to import code. Desktop/tablet/mobile layouts independent within same role. GridStack optional but keyboard alternatives compulsory.


---

# Build order and definition of completion

Phase0:inspect actual HEAD and current commercial/private APIs/models/templates. Preserve newer changes. Import original CP requirements unchanged;map endpoint-prefix change and missing domains. No repeated architecture questionnaire.
Phase1:shared tokens/components + Jinja shells + authentication/session/role boundary. Pages use real app routes and real scoped empty queries. Build identity errors and unavailable-provider states.
Phase2:Product/PortfolioVersion/Projection/Draft minimal domain. Owner creates draft;reload proves persistence;public stays empty;private preview shows genuine blockers. This removes the circular 'no product so no page;no page to create product' failure.
Phase3:public catalog/detail/compare/pricing/documents/status/help and accessible responsive baseline. Nonpublic data never leaks. Build zero/one/many fixtures only in test database.
Phase4:customer onboarding,eligibility,selections,alerts,performance,preferences/support/exports. Each uses real schema/repository and permission checks. Missing numeric evidence remains unavailable.
Phase5:private trading views and configuration against existing actual execution services;do not change financial algorithms just to suit a widget. Bind stop/exit UI to same safe writer and current capability.
Phase6:research universe,Lab,run/results,comparison,product/rights/review. Implement durable query/job boundaries and visible all-candidate denominator. Execute local data fixtures,not real history fabrication.
Phase7:billing/customer connection/mandate/publisher workflows. Build server clients and isolated external protocol adapters,full local scenarios,then separate actual test-mode qualification where authorized. Unavailable vendor access does not stop page construction.
Phase8:PAMM/MAM investor/operator views and qualified simulations. No custody,no automatic fees or onboarding claims. Build fields,read models,request/status flows with explicit blocked effect.
Phase9:customization,charts,tables,search,accessibility/performance. Do not defer security/a11y until polish. GridStack/Tom Select only if baseline native implementation demonstrably benefits.
Phase10:full applicable screen/state/browser matrix +journeys+form+authorization/effect tests. Fix consumers,retain failed evidence,rerun intended release scope. Partitioning allowed,representative sampling not full completion.
Phase11:review real deployment-inactive URLs,auth/cookies,CSP,role isolation,monitors and artifact identities. Cloud access/merchant/legal/platform live steps remain named owner cards. No changes to live accounts,signals,charges,subscriptions or infrastructure without their own authority.

No stub rule:do not call an inert hardcoded endpoint 'implemented'. Build actual models,migrations,query/command handlers and state transitions. Honest empty data is a successful legitimate application state. Test fixtures are authorized in isolated environments and cannot be published or seeded production. An unsupported capability has a working explanation and secure rejection,while the positive feature remains BLOCKED until implemented/qualified;a negative test does not certify it.

Completion states:SPECCED;UI_IMPLEMENTED;API_DB_INTEGRATED;ISOLATED_E2E_PASSED;EXTERNAL_TEST_QUALIFIED;DEPLOYED_INACTIVE;LIVE_RELEASED. UI completion can occur without real portfolios;live qualification cannot. Final report lists every screen/form/action and data source. No deferred self-declared 'out of scope' while claiming all dashboards complete.


---

# Testing the actual screens and workflows

Inherit all CP-091..114 commercial GUI scenarios and the execution/privacy/rights rules they consume. The package adds explicit66-screen tests,41 form contracts and phase-specific journeys. catalog/test_cases.json is an application test specification,not a passing suite. tests/test_pack.py checks this design artifact only. The design atlas is not a substitute for actual HTTP/auth/database/browser tests.

Full declared matrix:all66 screens x12 specified states x Chromium/Firefox/WebKit x desktop1440x900/tablet768x1024/phone390x844. Include supplementary320px,200% zoom,keyboard and reduced-motion checks. Native mobile Safari/hardware tests are separate external device qualification;WebKit desktop is not an iPhone hardware claim.

Each materialized state-case needs real application setup through fixture factories and service boundaries. Named states must be elicited,not selected by an insecure production query parameter. Guest/protected,wrong tenant/role,empty valid datasets,slow reads,expired auth,stale snapshots,partial panels,conflict and external outage all assert backend effects as well as visible DOM.

Mandatory fixture families:empty clean database;private draft only;released synthetic test product;customerA/customerB;staff role matrix;valid and expired rights;active/past-due/canceled subscriptions;connected but unmandated account;mandated demo route;open partial-fill transfer;outstanding UNKNOWN command;missing fee/mark;contradictory/revised billing events;old/incomplete report;managed NAV correction;standby role. All synthetic fixtures have explicit test origin,isolated issuer/database and no live credentials/egress. Production fixture loading must fail.

Use actual PostgreSQL under non-superuser RLS;actual FastAPI browser routes;real session/CSRF handling;independent protocol stubs for vendor responses. Identity and payment protocol simulators must not become production fallbacks. Query/command logs assert no effects on GET/preview/nav,one operation on duplicate submit,correct scope on delayed response,zero cross-tenant rows. Do not have application import reference expected answers.

Each schema-bound form tests valid create/update,missing required fields,invalid types/enum/currency/numeric values,extra keys,stale revision,duplicate idempotency key,different body same key,token expiry and unauthorized IDs. Conditional requiredness is checked by selected mode. Empty values are not coerced to0/false. Test lost response after commit and before UI response by status lookup rather than resubmit.

A11y:axe on real rendered states;visible labels,error focus,keyboard tables/dialogs,reorder alternatives,focus restoration,no hover-only controls,text equivalent for charts,contrast,dynamic regions,reduced motion and200% zoom. Automated checks supplement manual AT tasks. Browser artifacts must redact PII/secrets.

Scope/result registry records case,screen,form,journey,browser,viewport,fixture,commit,config,schema,driver,Junit,status and evidence paths. Completion requires exact union of selected expected IDs,nonempty drivers,actual observations and zero unaccounted skips. Failing/blocked cases stay in denominator. UIs with unavailable external credentials may pass isolated tests but cannot claim external qualification. Unknown code changes invalidate relevant evidence. No test count guarantees all future defects absent.


---

# Gaps the UI implementation must close

1.Circular data dependency:missing portfolios must not prevent Product drafts,empty read models or screens. Implement actual domain slice first.
2.Interface/backend mismatch:existing three routes do not support customer configuration or admin work. Wire current services and new persisted models;do not point UI at nonexistent URLs.
3.API prefix drift:old design/api/commercial/v1 differs from current/api/v1;one canonical new namespace and mapping,not competing side-effect aliases.
4.Token/session gap:JWT /me alone is not browser login;implement verified BFF sessions,CSRF,expiry,revocation and per-action role/object authorization.
5.Privilege confusion:private_owner,commercial owner and customer are separate authorities. No cross-origin private account proxy.
6.Raw claims versus applied behavior:Stripe processed receipt,new role,connected account or successful webhook are not paid entitlement,live mandate or executed fill.
7.Drafts versus published projections:public query must never expose raw draft product,rights contract,source text or unapproved price/performance.
8.Metrics:fees,marks,origin,episode definition,cashflow and currency quality must travel through API,chart,table and exports. No beautiful false curve.
9.Data races:abort plus generation checks stop late accountA data painting under accountB. Stable snapshots prevent pagination duplicates and cohort drift.
10.Actions:preview/confirm/operation roles must preserve idempotency,scope,version and unknown outcomes. No optimistic financial success or general automatic retries.
11.Customization:layout changes cannot conceal safety/risk/cost information,change hard limits or execute user HTML/JS/code.
12.Integration capability screens must distinguish configured/entitled/tested/deployed/released;unavailable positive feature is not complete because denial works.
13.Social copying:join new-only by default;sync old trades separately;pause,billing cancel,revocation and handoff retain open obligations.
14.PAMM/MAM:broker-native programs have exact NAV/fee/dealing rules. No SaaS custody or automatically guessed accounting.
15.Accessibility:actual browser/table/canvas behavior needs verification;templates and screenshot aesthetics do not establish usable forms.
16.Permissions in worker paths:queue/export/research jobs revalidate their principal/service scope;no background tenant leakage.
17.Evidence:actual queries and side effects tested. Screenshots alone,helper tests,syntax pass and this atlas are not application acceptance.
18.Deployment:inactive private/public/staff origins,secure cookies,cache isolation and no live service authority in test environments.

All are design/implementation obligations,not a fresh claim that every corresponding code defect was executed in this delivery. Current code can have advanced;inspect HEAD and preserve working integrations. Where original commercial legal/platform owner cards remain missing,keep effects disabled and finish all independent local screen work.


---

# State machines, not disconnected forms

## Universal draft and command workflow

`ABSENT -> DRAFT -> VALIDATED -> PREVIEWED -> SUBMITTED -> IN_PROGRESS -> SUCCEEDED / FAILED / UNKNOWN`.

Saving a partial draft is allowed if every supplied field is typed and scoped. Readiness validation returns all known blockers. Editing invalidates validation and preview. A preview binds object, version, proposed action, exact scope, audience, data/price/rights snapshot and expiry. Confirmation revalidates current prerequisites and claims an idempotency record before any financial or billing action. A duplicate request returns the same operation. A changed body using that key conflicts. No failed or unknown operation becomes successful because the browser navigated to a success page.

`UNKNOWN -> RECONCILING -> SUCCEEDED / FAILED / UNKNOWN` is the only ordinary recovery path for a possibly accepted effect. Browser retries cannot directly send it again. User can leave and reopen the operation using a stable URL. Logout stops personal polling, not the already authorized worker's safety management.

## Product and portfolio lifecycle

A Product draft may exist with no PortfolioVersion. A PortfolioVersion draft may contain no released research and may be entirely cash while being edited. Those are incomplete internal records, never a published track record. `DRAFT -> READY_FOR_REVIEW -> APPROVED -> PUBLISHED -> PAUSED_FOR_NEW -> RETIRED`. Edits create a successor draft; no silent mutation of a published version. Publishing additionally checks all rights, legal, channel, cost, audience and performance-evidence conditions. A public projection is a separate whitelist, not direct serialization of internal models.

## Customer onboarding

`IDENTITY_PENDING -> VERIFIED -> ELIGIBILITY_PENDING -> ELIGIBLE/REVIEW_REQUIRED/UNAVAILABLE`. Portfolio selection, paid entitlement, connection and mandate follow separate dimensions, not one global “active” bit. Selection does not copy. Subscription does not create a mandate. A mandate does not prove the connected account is currently executable. The customer overview presents the exact next step and every independent blocker.

Connection: `NOT_CONFIGURED -> AUTHORIZING -> VERIFYING -> CONNECTED / NEEDS_REAUTH / DEGRADED / DISCONNECTED`. A hosted callback is checked before binding identity; exact account/environment is re-read. Disconnect is a workflow with open-obligation checks, not deleting a credential row.

Mandate: `DRAFT -> VALIDATED -> CONSENTED -> ACTIVATION_PENDING -> ACTIVE -> PAUSED_NEW -> HANDOFF_PENDING -> ENDED`. Actual program names can map to existing domain enums with a reviewed migration; do not create aliases that change semantics. Existing-position sync is a separate explicit proposal. Revocation ends authority according to the governing terms while triggering required notices and handoff; it is not permission to continue arbitrary new management indefinitely.

## Billing and rights

Billing is driven by verified canonical processor state, not UI state, webhook order or redirect. Current receipt deduplication must gain actual apply/reconciliation state before “processed” is displayed. Upgrades/downgrades show future entitlement, effective date, invoice impact and open obligations. Same subscription does not simultaneously map to test and live price IDs.

Rights: `DRAFT -> IN_REVIEW -> ACTIVE -> EXPIRED/REVOKED`. Each sleeve, channel, geography, asset, audience and time must qualify. Cosmetic product naming, role changes, subscriptions or an LLM summary cannot override those conditions. Existing exposure has a separate legal/operational wind-down state, not disappearance from screens.

## Research and publication

Research: `DRAFT -> QUEUED -> RUNNING -> PARTIAL/COMPLETED/FAILED/CANCELED`. Show complete candidate denominator and all outcomes. Resume preserves input hashes and completed shards; modifying inputs creates a new run. A replay can correctly conclude insufficient evidence. Missing implementation cannot claim that conclusion.

Publication: `PLANNED -> AUTHORIZED -> SUBMITTING -> ACKNOWLEDGED/UNKNOWN -> RECONCILING -> VERIFIED/FAILED`. An acknowledgment is not a follower fill. Customer-visible publication and actual account execution remain different books. Cohorts are frozen for a given action; joining later does not receive an old entry as a new one.

## Managed programs

Program eligibility, broker mandate, subscribed capital, dealing schedule, units/NAV and fees are separate records. UI offers a permitted broker-bound request and status, never an unapproved investment-payment form. Deposit/withdrawal cutoffs use broker timestamps. A corrected NAV produces a restated version and auditable downstream adjustment. Cashflow-sensitive performance fees require a specified valid convention before display or execution.

## Screen-to-screen workflow definitions

The exact 24 inherited commercial journeys plus 12 private trading journeys are in catalog/journeys.json. Test every stated step and all relevant events against the real application. Screens display current domain state; they do not create a parallel browser state machine with financial authority.


---

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


---

# Content, exports, notifications and operation details

Content defaults to plain text/structured blocks. Server templates autoescape and client components use textContent or reviewed text renderers. No arbitrary user HTML, template expressions, inline handlers, stylesheet injection or pasted JavaScript. Links are server route IDs or allowlisted destinations; no javascript/data URLs. An SVG icon source is packaged and trusted, not uploaded executable content.

Attachments start private/quarantined with size/type limits and hashed object identity. Download rechecks current tenant, role, purpose and content permission. Rights agreements are not public product documents. Email and API secrets never appear in screenshots, logs, cassettes or exported read models. Short-lived signed URLs are not permanent possession-based authority.

CSV exports escape formula-leading cells and preserve strings, monetary units, timestamps, quality and source origin. JSON exports preserve decimal strings. A generated document includes scope, period, generation time, definitions, costs and incomplete-data notes. An export job captures immutable filters and current authorization, then rechecks access at retrieval. Public export never uses a privileged internal dataset. Temporary exports expire under explicit retention policy.

Notifications have severity, audience, consent category, template version, delivery state, retry schedule and deduplication identity. A browser toast is not an incident record. Ordinary toast lasts six seconds; critical safety and blocked-operation notices remain visible and reachable from the action center. Acknowledging a notice does not repair its financial cause. Informational/marketing quiet hours cannot silently suppress mandatory safety notices.

Search and command palette, when added, use a role-scoped registry. The palette initially navigates only; it does not submit financial or bulk operations. Saved searches retain allowed filters, not raw SQL. Browser account switch aborts prior requests and also checks a generation token before rendering because abort alone may race an already completed response.

Shared components have dedicated regression contracts: ContextBar,OriginBadge,MetricCard,QualityNotice,DataGrid,TimeSeriesChart,EmptyState,ErrorSummary,Field,ScopedSelector,StepWizard,DiffPanel,ActionPreview,OperationStatus,CommandReceipt,IncidentBanner,DisclosureBlock,ExportControl and LayoutPreferences. Implement them once per versioned shared asset/template package, not once per screen.

Operational settings validate canonical origins, secure cookie policy, trusted proxy set, CSP, static version hashes, request/body limits, polling bounds, cache scope, maximum result size, attachment/export retention and resource budgets. A deployment can serve legitimate empty states while all financial controls remain disabled. Do not call an HTTP liveness response investment or broker readiness.


---

# Reuse decisions and scope boundaries

Use catalog/components.json as an adoption plan, not an installation list. Inspect actual current versions already vendored by signal-copier before fetching replacements. Record canonical repository, release, commit/hash, license and all notices. The compiled shared assets go into each application's own static namespace with immutable version URLs. Do not copy application code across the private/commercial boundary merely to reuse a template.

Jinja2 supplies layouts, includes and macro reuse through FastAPI's template integration. Chart.js renders visualizations. Tabulator supplies table interaction; the application supplies scoped stable snapshots, safe fields and pagination. Lucide provides only the icon subset actually used. Native HTML selects are enough for small lists. Tom Select may serve large remote-scoped selectors. A fixed allowlisted CSS grid plus accessible move buttons is the baseline; GridStack is optional once persistence and accessibility are tested. Avoid another full admin starter with its own auth, database or role assumptions.

Existing Supabase authentication and PostgreSQL tenant enforcement remain server-side. The BFF must verify real issuer/audience/signature and bind an opaque server session; browser-readable service-role or trade credentials are prohibited. Stripe SDK/hosted pages are reused for payments, not recreated with card-entry fields. Hosted-provider return links and JWT /me do not themselves establish paid entitlement or copying authority.

Playwright's actual app tests cover all browser projects. axe adds automated checks; manual keyboard, focus, zoom, assistive-technology and text-equivalent checks remain. Schemathesis extends API tests only against isolated effects. No live LLM is needed to render a screen, compute a financial value, authorize a tenant or select a mandate. Any future grounded explanatory assistant is a separately scoped feature with rights and evidence review.

A page's read_roles does not grant every form on it. catalog/subaction_permissions.json narrows shared form operations, especially support incidents, report books, release reviews and recovery cards. The backend repeats those predicates at enqueue and effect boundaries. Unsupported or unknown actions deny all roles, including owner.

The UI can display all requested asset classes and channels as capability-bound choices. It cannot certify an unimplemented financial algorithm or external adapter. The broker/program definition controls decimal units, settlement/expiry, trigger basis, amendment support and permitted actions; the UI must not provide enabled generic controls that bypass those differences.


# Complete screen specifications


---

# AD-01: Commercial operations overview

**Purpose:** Prioritize publisher, rights, customer and business actions across authorized scopes.

Page: `/ops`. Service: `commercial`. Read model: `/api/v1/ui/ad-01`. Access: owner, researcher, reviewer, publisher_operator, billing_operator, support_readonly, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Release blockers.
2. Publisher health.
3. Customers/entitlements.
4. Business indicators.
5. Incidents.

## Table and query behavior

Columns in default order: Object, Service mode, State, Owner, Required action, Evidence.

Filters: Service, Platform, Tenant scope.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-01-A01 | Create product draft | AD-07; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| AD-01-A02 | Open approval queue | AD-08; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-AD-01-01 | Eligible products | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-01-02 | Active subscriptions | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-01-03 | Unknown publications | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-01-04 | Open incidents | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No commercial products or subscriptions exist yet.

Next permitted route: `AD-07` (Products and portfolio versions). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No commercial products or subscriptions exist yet.; next AD-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No trading P&L mixed into revenue. Counts use scoped business rows, never synthetic demo metrics.

Run every SC-AD-01-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-109, CP-110, CP-111, CP-112.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-01-P01 | Release blockers | checklist | Release blockers enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD01ReadModel.panels.01 |
| AD-01-P02 | Publisher health | information | Publisher health presents typed facts or approved explanatory content for Commercial operations overview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD01ReadModel.panels.02 |
| AD-01-P03 | Customers/entitlements | information | Customers/entitlements presents typed facts or approved explanatory content for Commercial operations overview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD01ReadModel.panels.03 |
| AD-01-P04 | Business indicators | information | Business indicators presents typed facts or approved explanatory content for Commercial operations overview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD01ReadModel.panels.04 |
| AD-01-P05 | Incidents | information | Incidents presents typed facts or approved explanatory content for Commercial operations overview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD01ReadModel.panels.05 |


---

# AD-02: Rights and service approvals

**Purpose:** Record licensed use, audience, expiry and legal determinations as evidence-backed versions.

Page: `/ops/rights`. Service: `commercial`. Read model: `/api/v1/ui/ad-02`. Access: owner, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Grant register.
2. Scope intersection.
3. Evidence documents.
4. Expiry/wind-down preview.
5. Review.

## Table and query behavior

Columns in default order: Grant, Source, Use, Assets, Audience, Channel, Effective/expiry, State.

Filters: Source, Use, Jurisdiction, Expiry.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-02-A01 | Create grant draft | F-RIGHTS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-02-A02 | Attach evidence | ACTION-AD-02-A02; Create bounded private upload intent. Verify MIME/size, hash, malware/content status and tenant. Never execute attachment content; clean server-generated basename. |
| AD-02-A03 | Submit approval | F-RIGHTS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No commercial rights grants have been approved.

Next permitted route: `AD-03` (Research universe and sleeves). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-RIGHTS: Rights-grant evidence

Steps: Source → Allowed uses → Audience/channel → Term → Evidence → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| source_id | Source | id | yes | unset | registered source/product; Exact rights counterparty |
| uses | Uses | enum_list:private_trading,research,redistribution,model_processing | yes | unset | explicit written grant scope; Combining signals does not grant rights |
| jurisdictions | Jurisdictions | country_list | yes | unset | explicit approved audience; No implicit worldwide |
| channels | Channels | id_list | yes | unset | approved destinations; No arbitrary all-channel |
| assets | Asset profiles | id_list | yes | unset | covered by agreement; Exact use scope |
| effective_at | Effective | datetime | yes | unset | UTC;before expiry; No retroactive fake permission |
| expires_at | Expires | datetime | yes | unset | after effective;ongoing requires separately approved convention; Expiration evaluated at effect |
| evidence_ids | Contract/evidence | id_list | yes | unset | private reviewed immutable documents; No public raw contract |
| attribution_policy_id | Attribution | id | yes | unset | reviewed policy; Trade secrets/private sources respected |

**Save:** Create draft grant and evidence records

**Preview:** Show intersection and affected products/obligations

**Confirm:** Submit or record owner-approved decision with audit; researcher cannot approve

Revocation triggers scoped wind-down review, not instant deletion of history or abandonment of existing management.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No commercial rights grants have been approved.; next AD-03 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

A paid subscription is not a grant. Researcher cannot approve rights; uploaded assertion is not legal verification. Public endpoints never expose contracts.

Run every SC-AD-02-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-111.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-02-P01 | Grant register | table | Grant register is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD02ReadModel.panels.01 |
| AD-02-P02 | Scope intersection | information | Scope intersection presents typed facts or approved explanatory content for Rights and service approvals, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD02ReadModel.panels.02 |
| AD-02-P03 | Evidence documents | information | Evidence documents presents typed facts or approved explanatory content for Rights and service approvals, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD02ReadModel.panels.03 |
| AD-02-P04 | Expiry/wind-down preview | checklist | Expiry/wind-down preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD02ReadModel.panels.04 |
| AD-02-P05 | Review | checklist | Review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD02ReadModel.panels.05 |


---

# AD-03: Research universe and sleeves

**Purpose:** Build qualified sleeve universe with exact lineage and usable histories.

Page: `/ops/research/universe`. Service: `commercial`. Read model: `/api/v1/ui/ad-03`. Access: owner, researcher, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Sleeve catalog.
2. Rights/data gates.
3. Overlap groups.
4. Dataset versions.

## Table and query behavior

Columns in default order: Sleeve, Provider/analyst, Strategy, Parser/policy, History window, Rights, Coverage, Capacity.

Filters: Asset, Strategy, Rights, Coverage.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-03-A01 | Create sleeve draft | F-SLEEVE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-03-A02 | Inspect coverage | local_coverage; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| AD-03-A03 | Open portfolio lab | AD-04; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No qualified strategy sleeves are available.

Next permitted route: `AD-02` (Rights and service approvals). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-SLEEVE: Strategy sleeve

Steps: Lineage → History → Product → Management → Qualification.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| provider_id | Provider | id | yes | unset | registered source; Rights checked separately |
| analyst_id | Analyst | id | yes | unset | verified mapping; Stable ID |
| strategy_id | Strategy | id | yes | unset | reviewed strategy identity; Not arbitrary marketing label |
| parser_version_id | Parser | id | yes | unset | reviewed exact version; No latest alias |
| policy_version_id | Management policy | id | yes | unset | compatible released recipe; Original risk semantics retained |
| product_profile_id | Product | id | yes | unset | verified instrument family; No mixed financial math |
| dataset_version_id | History | id | conditional/optional | unset | required for research eligibility, not saving draft; No fabricated performance |
| cluster_id | Exposure cluster | id | conditional/optional | unset | reviewed context label; Does not merge source events |

**Save:** Create real sleeve draft

**Preview:** Report rights/data/capacity blockers

**Confirm:** Save qualified status only from evidence-backed evaluation

Draft with no history permitted. History study blocks until data, never blocks form construction.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No qualified strategy sleeves are available.; next AD-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Known flat and missing observations distinct; lineage immutable per version; source selection cannot override rights.

Run every SC-AD-03-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-106.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-03-P01 | Sleeve catalog | information | Sleeve catalog presents typed facts or approved explanatory content for Research universe and sleeves, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD03ReadModel.panels.01 |
| AD-03-P02 | Rights/data gates | information | Rights/data gates presents typed facts or approved explanatory content for Research universe and sleeves, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD03ReadModel.panels.02 |
| AD-03-P03 | Overlap groups | information | Overlap groups presents typed facts or approved explanatory content for Research universe and sleeves, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD03ReadModel.panels.03 |
| AD-03-P04 | Dataset versions | information | Dataset versions presents typed facts or approved explanatory content for Research universe and sleeves, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD03ReadModel.panels.04 |


---

# AD-04: Portfolio Lab builder

**Purpose:** Declare the complete candidate universe, constraints and walk-forward design.

Page: `/ops/research/new`. Service: `commercial`. Read model: `/api/v1/ui/ad-04`. Access: owner, researcher, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Universe selection.
2. Complementarity/capacity.
3. Weights/cash bounds.
4. Training/holdout.
5. Cost stress.
6. Budget and preflight.

## Table and query behavior

Columns in default order: Sleeve, Weight bound, Cluster, Data coverage, Eligibility.

Filters: Universe version.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-04-A01 | Save run draft | F-RESEARCH; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-04-A02 | Validate complete scope | F-RESEARCH; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-04-A03 | Start bounded research | F-RESEARCH; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-AD-04-01 | Declared candidates | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-04-02 | Expected resource budget | resource_units | Deterministic declared job candidate count and calibrated resource estimate, not a guaranteed runtime. Unmeasured runtime estimate labeled estimated; cost/credit allowance checked before launch. |


## Empty state

Select a rights-qualified universe before preparing a run.

Next permitted route: `AD-03` (Research universe and sleeves). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-RESEARCH: Portfolio research run

Steps: Universe → Candidates → Capital constraints → Data split → Costs → Budget → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| universe_version_id | Universe | id | yes | unset | rights-qualified version; Whole approved universe enumerated |
| recipes | Recipes | enum_list:equal_capital,inverse_volatility,hrp,min_cvar | yes | unset | implemented deterministic recipes; No runtime LLM required |
| subset_min | Minimum sleeves | integer | yes | 2 | >=1 and<=subset_max; Research-only constraint |
| subset_max | Maximum sleeves | integer | yes | 5 | <=universe size and approved job cap; No silently reduced search |
| cash_bps | Cash allocation | integer | yes | 1500 | 0..10000 and existing research policy; Not live account allocation |
| max_sleeve_bps | Sleeve ceiling | integer | yes | 3500 | 0..10000 and feasible; Weights plus cash exactly10000 |
| max_cluster_bps | Cluster ceiling | integer | yes | 5000 | 0..10000 and approved study; No hidden concentration |
| train_sessions | Training sessions | integer | yes | 252 | positive;available window sufficient; Chronological only |
| test_sessions | Test sessions | integer | yes | 63 | positive;nonoverlapping holdout; No hindsight information |
| holdout_fraction | Holdout fraction | decimal | yes | 0.20 | strictly0..1;lock dataset before search; No repeated tuning on holdout |
| cost_scenario_ids | Costs/stress | id_list | yes | unset | verified available scenarios; No zero-cost assumption |
| resource_profile_id | Resources | id | yes | unset | approved memory/time/disk budget; Cannot starve execution |

**Save:** Persist complete study definition

**Preview:** Compute full candidate denominator, data gaps, resource quote and immutable manifest

**Confirm:** Enqueue research job only; not publish/weight live portfolio

Defaults inherited research drafts only, not evidence of optimal parameters; infeasible candidates retained with reasons.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | Select a rights-qualified universe before preparing a run.; next AD-03 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

All candidate count computed before run; holdout locked; resource limits not silently changed to accelerate; no live effects.

Run every SC-AD-04-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-107.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-04-P01 | Universe selection | information | Universe selection presents typed facts or approved explanatory content for Portfolio Lab builder, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD04ReadModel.panels.01 |
| AD-04-P02 | Complementarity/capacity | information | Complementarity/capacity presents typed facts or approved explanatory content for Portfolio Lab builder, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD04ReadModel.panels.02 |
| AD-04-P03 | Weights/cash bounds | information | Weights/cash bounds presents typed facts or approved explanatory content for Portfolio Lab builder, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD04ReadModel.panels.03 |
| AD-04-P04 | Training/holdout | information | Training/holdout presents typed facts or approved explanatory content for Portfolio Lab builder, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD04ReadModel.panels.04 |
| AD-04-P05 | Cost stress | information | Cost stress presents typed facts or approved explanatory content for Portfolio Lab builder, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD04ReadModel.panels.05 |
| AD-04-P06 | Budget and preflight | information | Budget and preflight presents typed facts or approved explanatory content for Portfolio Lab builder, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD04ReadModel.panels.06 |


---

# AD-05: Research run and full results

**Purpose:** Track every candidate/shard and expose gaps rather than cherry-picking successful runs.

Page: `/ops/research/runs/{run_id}`. Service: `commercial`. Read model: `/api/v1/ui/ad-05`. Access: owner, researcher, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Manifest and versions.
2. Progress/shards.
3. Every result.
4. Failures/retries.
5. Reproducibility.

## Table and query behavior

Columns in default order: Candidate, Recipe, State, Coverage, Net, Drawdown, Capacity, Rejection reason.

Filters: Recipe, State, Feasibility.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-05-A01 | Inspect candidate | AD-06; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| AD-05-A02 | Resume failed shard | ACTION-AD-05-A02; Resume only incomplete shards of the same frozen job scope. Reauthorize dataset use and compute budget; do not resubmit external publications. |
| AD-05-A03 | Cancel research job | ACTION-AD-05-A03; Request cancellation of owned research job; join workers and retain completed results and failure record; do not alter live strategy. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-AD-05-01 | Expected candidates | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-05-02 | Completed | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-05-03 | Failed | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-05-04 | Blocked | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

This run has not started; no performance results exist.

Next permitted route: `AD-04` (Portfolio Lab builder). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | This run has not started; no performance results exist.; next AD-04 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Expected=terminal+remaining denominator preserved. Failed shards retained. Cancel is scoped to job, not financial loops.

Run every SC-AD-05-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-107.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-05-P01 | Manifest and versions | information | Manifest and versions presents typed facts or approved explanatory content for Research run and full results, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD05ReadModel.panels.01 |
| AD-05-P02 | Progress/shards | information | Progress/shards presents typed facts or approved explanatory content for Research run and full results, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD05ReadModel.panels.02 |
| AD-05-P03 | Every result | information | Every result presents typed facts or approved explanatory content for Research run and full results, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD05ReadModel.panels.03 |
| AD-05-P04 | Failures/retries | information | Failures/retries presents typed facts or approved explanatory content for Research run and full results, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD05ReadModel.panels.04 |
| AD-05-P05 | Reproducibility | information | Reproducibility presents typed facts or approved explanatory content for Research run and full results, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD05ReadModel.panels.05 |


---

# AD-06: Candidate comparison and shadow report

**Purpose:** Compare complete candidates on compatible holdout/forward data and feasible capital.

Page: `/ops/research/compare`. Service: `commercial`. Read model: `/api/v1/ui/ad-06`. Access: owner, researcher, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Common-period validation.
2. Return/drawdown.
3. Correlations/co-loss.
4. Cost/capacity.
5. Rejected results.
6. Shadow qualification.

## Table and query behavior

Columns in default order: Candidate, Dataset, Holdout status, Net, Tail loss, Turnover, Coverage, Capacity, Decision.

Filters: Candidate versions, Book, Period, Scenario.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-06-A01 | Choose candidate for draft | ACTION-AD-06-A01; Create an unreleased portfolio version draft referencing one completed candidate and its immutable evidence; do not publish or activate. |
| AD-06-A02 | Inspect counterexample | local_counterexample; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-AD-06-01 | Comparable coverage | ratio | Available eligible observations / specified expected observations or explicitly defined overlap duration. Denominator and gap policy recorded; missing bars not automatically zero return. |
| M-AD-06-02 | Forward duration | seconds | Difference between identified server times with timezone/clock semantics and start/end evidence. Missing either end => ongoing or unavailable, not0. |


## Empty state

No comparable completed candidates selected.

Next permitted route: `AD-05` (Research run and full results). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No comparable completed candidates selected.; next AD-05 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Do not rank mismatched actual/hypothetical or non-overlapping periods; favorable trials alone cannot support selection.

Run every SC-AD-06-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-108.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-06-P01 | Common-period validation | checklist | Common-period validation enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD06ReadModel.panels.01 |
| AD-06-P02 | Return/drawdown | chart | Return/drawdown uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: AD06ReadModel.panels.02 |
| AD-06-P03 | Correlations/co-loss | chart | Correlations/co-loss uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: AD06ReadModel.panels.03 |
| AD-06-P04 | Cost/capacity | information | Cost/capacity presents typed facts or approved explanatory content for Candidate comparison and shadow report, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD06ReadModel.panels.04 |
| AD-06-P05 | Rejected results | table | Rejected results is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD06ReadModel.panels.05 |
| AD-06-P06 | Shadow qualification | checklist | Shadow qualification enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD06ReadModel.panels.06 |


---

# AD-07: Products and portfolio versions

**Purpose:** Create real draft Product and PortfolioVersion rows before research/release exists.

Page: `/ops/products`. Service: `commercial`. Read model: `/api/v1/ui/ad-07`. Access: owner, researcher, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Product catalog.
2. Version builder.
3. Sleeve weights/cash.
4. Channel compatibility.
5. Copy and disclosures.
6. Preview.

## Table and query behavior

Columns in default order: Product, Version, Lifecycle, Research evidence, Rights, Audience, Published.

Filters: State, Assets, Channel.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-07-A01 | Create product draft | F-PRODUCT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-07-A02 | Edit version | F-PRODUCT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-07-A03 | Preview audience-safe page | F-PRODUCT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No products exist. Create a draft; it will not appear publicly.

Next permitted route: `AD-07` (Products and portfolio versions). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-PRODUCT: Product and portfolio version

Steps: Identity → Sleeves/weights → Channels/audience → Evidence → Disclosures → Preview.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| product_name | Product name | text | yes | unset | 1..100 plain text; No unsupported promotional claims |
| slug | Public slug | slug | yes | unset | unique lower-case URL slug; Draft slug not public |
| portfolio_version_id | Existing version | id | conditional/optional | unset | editable draft only; Released changes create new version |
| sleeve_weights | Sleeve weights | weight_map | conditional/optional | unset | nonnegative integer bps;plus cash10000 when complete; Draft can be incomplete |
| cash_bps | Cash weight | integer | yes | 10000 | 0..10000;weight conservation before eligibility; New empty draft all cash is not a trading recommendation |
| service_modes | Modes | enum_list:alerts,copying,managed_program | yes | unset | rights/platform/legal intersections; No auto-enable unsupported mode |
| audience_policy_id | Audience | id | conditional/optional | unset | required before release; No inferred jurisdictions |
| research_report_id | Evidence | id | conditional/optional | unset | required for claims;can save without; No invented track record |
| methodology_document_id | Methodology | id | conditional/optional | unset | required before public publication; Versioned approved content |

**Save:** Create real draft rows even without completed research

**Preview:** Render private no-store audience-safe preview with missing-data messages

**Confirm:** Submit version for review; not directly publish

A version with no sleeves/evidence may be saved but cannot pass release or appear publicly. State badges separate edited, validated, approved, published.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No products exist. Create a draft; it will not appear publicly.; next AD-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Draft persists immediately and has no performance until evidence exists. Public catalog excludes it until all gates and projection approval pass.

Run every SC-AD-07-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-091, CP-092, CP-109.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-07-P01 | Product catalog | information | Product catalog presents typed facts or approved explanatory content for Products and portfolio versions, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD07ReadModel.panels.01 |
| AD-07-P02 | Version builder | information | Version builder presents typed facts or approved explanatory content for Products and portfolio versions, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD07ReadModel.panels.02 |
| AD-07-P03 | Sleeve weights/cash | information | Sleeve weights/cash presents typed facts or approved explanatory content for Products and portfolio versions, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD07ReadModel.panels.03 |
| AD-07-P04 | Channel compatibility | checklist | Channel compatibility enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD07ReadModel.panels.04 |
| AD-07-P05 | Copy and disclosures | information | Copy and disclosures presents typed facts or approved explanatory content for Products and portfolio versions, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD07ReadModel.panels.05 |
| AD-07-P06 | Preview | checklist | Preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD07ReadModel.panels.06 |


---

# AD-08: Release and change approvals

**Purpose:** Approve an immutable product/policy/audience/artifact only with independent required evidence.

Page: `/ops/reviews`. Service: `commercial`. Read model: `/api/v1/ui/ad-08`. Access: owner, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Review queue.
2. Version diff.
3. Evidence gates.
4. Conflict/self-review.
5. Decision.
6. Effective schedule.

## Table and query behavior

Columns in default order: Review, Version hash, Proposer, Reviewer, Gates, Audience, State.

Filters: Type, State, Due.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-08-A01 | Request changes | F-RELEASE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-08-A02 | Approve scoped release | F-RELEASE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-08-A03 | Reject review | F-RELEASE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No release reviews are queued.

Next permitted route: `AD-07` (Products and portfolio versions). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-RELEASE: Release review

Steps: Object/hash → Evidence → Gates → Audience → Independent review → Decision.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| object_version_id | Version | id | yes | unset | immutable current review target; Exact hash shown |
| evidence_manifest_id | Evidence | id | yes | unset | complete mapped tests/rights/data/report; Structural hashes not truth by themselves |
| audience_policy_id | Audience | id | yes | unset | approved exact service/channel/geography; No broadened audience |
| scheduled_at | Effective time | datetime | conditional/optional | unset | future within approval validity; Open allocations retain original binding |
| decision | Decision | enum:request_changes,reject,approve | yes | request_changes | per-role reviewer authorization; Cannot approve own proposal where independence required |
| reason | Decision reason | text | yes | unset | 1..4000 characters; Audit evidence |

**Save:** Record review notes

**Preview:** Recompute all gates and display changes since proposal

**Confirm:** Persist decision after fresh step-up; publication is separate

No checkbox declares legal advice complete; evidence-bound approvals only. Financial permission cannot arise from role switch in same identity.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No release reviews are queued.; next AD-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Draft author cannot satisfy independent reviewer gate with another role toggle. Approval expires/invalidates on material changes; release is not publication.

Run every SC-AD-08-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-109.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-08-P01 | Review queue | checklist | Review queue enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD08ReadModel.panels.01 |
| AD-08-P02 | Version diff | information | Version diff presents typed facts or approved explanatory content for Release and change approvals, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD08ReadModel.panels.02 |
| AD-08-P03 | Evidence gates | information | Evidence gates presents typed facts or approved explanatory content for Release and change approvals, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD08ReadModel.panels.03 |
| AD-08-P04 | Conflict/self-review | checklist | Conflict/self-review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD08ReadModel.panels.04 |
| AD-08-P05 | Decision | checklist | Decision enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD08ReadModel.panels.05 |
| AD-08-P06 | Effective schedule | information | Effective schedule presents typed facts or approved explanatory content for Release and change approvals, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD08ReadModel.panels.06 |


---

# AD-09: Publisher channels and strategies

**Purpose:** Map one publication authority to each external strategy and qualification.

Page: `/ops/publishers`. Service: `commercial`. Read model: `/api/v1/ui/ad-09`. Access: owner, publisher_operator, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Channel registry.
2. External strategy identity.
3. Account/mode.
4. Capabilities/quotas.
5. Authority.
6. Reconciliation.

## Table and query behavior

Columns in default order: Channel, External strategy, Mode, Writer, Capability evidence, Quota, State.

Filters: Platform, Environment, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-09-A01 | Save inactive destination | F-PUBLISHER; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-09-A02 | Verify read-only identity | F-PUBLISHER; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-09-A03 | Prepare qualification | F-PUBLISHER; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No publishing destinations are configured.

Next permitted route: `AD-09` (Publisher channels and strategies). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-PUBLISHER: Publisher destination

Steps: Platform → External strategy → Identity/scope → Capabilities → Qualification.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| platform | Platform | enum:collective2,etoro,copyfactory,broker_native | yes | unset | implemented approved adapter; Not proprietary SignalStack self-hosting |
| external_strategy_id | External strategy | id | yes | unset | read-verified account strategy; Unique financial authority binding |
| environment | Mode | enum:local_simulation,external_test,demo,live | yes | local_simulation | vendor-specific evidence;C2 external_test not sandbox; No subscribers/autotrade for test without verification |
| credential_ref | Credential reference | secret_ref | conditional/optional | unset | scoped preprovisioned reference; Never disclose key |
| capability_manifest_id | Capabilities | id | conditional/optional | unset | required before external action; Exact platform version/evidence |
| publication_mode | Mode | enum:api_strategy_publisher,approved_master_copy | yes | api_strategy_publisher | one approved path per external account/strategy; No double publisher |

**Save:** Save inactive destination

**Preview:** Run read-only identity/capability checks where allowed

**Confirm:** Request qualification/release; not send signal

No real C2 test request without external explicit authorization; unsupported operations stay blocked while local protocol tests exercise all outcomes.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No publishing destinations are configured.; next AD-09 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Collective2 tests are not assumed sandbox. eToro account/app/program approvals separate; no two integrations publish same strategy.

Run every SC-AD-09-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-110.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-09-P01 | Channel registry | information | Channel registry presents typed facts or approved explanatory content for Publisher channels and strategies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD09ReadModel.panels.01 |
| AD-09-P02 | External strategy identity | information | External strategy identity presents typed facts or approved explanatory content for Publisher channels and strategies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD09ReadModel.panels.02 |
| AD-09-P03 | Account/mode | information | Account/mode presents typed facts or approved explanatory content for Publisher channels and strategies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD09ReadModel.panels.03 |
| AD-09-P04 | Capabilities/quotas | information | Capabilities/quotas presents typed facts or approved explanatory content for Publisher channels and strategies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD09ReadModel.panels.04 |
| AD-09-P05 | Authority | information | Authority presents typed facts or approved explanatory content for Publisher channels and strategies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD09ReadModel.panels.05 |
| AD-09-P06 | Reconciliation | information | Reconciliation presents typed facts or approved explanatory content for Publisher channels and strategies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD09ReadModel.panels.06 |


---

# AD-10: Publication intent and cohort detail

**Purpose:** Follow publication, recipient cohort and external order family without replaying financial effects.

Page: `/ops/publications/{intent_id}`. Service: `commercial`. Read model: `/api/v1/ui/ad-10`. Access: owner, publisher_operator, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Intent/revision.
2. Eligibility and frozen cohort.
3. Delivery attempts.
4. External state.
5. Unknown resolution.
6. Safety follow-up.

## Table and query behavior

Columns in default order: Recipient/cohort, Entitlement, Mandate, Sent, Accepted, Filled observation, Reason.

Filters: State, Channel, Outcome.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-10-A01 | Reconcile unknown | F-PUBLISH-ACTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-10-A02 | Preview permitted correction | F-PUBLISH-ACTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-AD-10-01 | Cohort size | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-10-02 | Delivered | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-AD-10-03 | Unknown | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No publication record exists for this reference.

Next permitted route: `AD-09` (Publisher channels and strategies). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-PUBLISH-ACTION: Publisher recovery/correction

Steps: Intent → Current external state → Allowed operation → Preview → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| intent_id | Intent | id | yes | unset | scope-owned existing intent; Never fabricate new identity for retry |
| operation | Operation | enum:reconcile,propose_price_change,propose_cancel | yes | reconcile | supported exact recipe; Quantity changes cannot masquerade as price edit |
| new_price | Proposed price | decimal | conditional/optional | unset | required only valid price modification; Instrument/tick/side validation |
| reason | Reason | text | yes | unset | 1..2000 characters; Audit |

**Save:** Record correction proposal

**Preview:** Recheck external family, rights and permitted mutation

**Confirm:** Enqueue approved operation with durable identity; no blind retry

Cohort unchanged after unknown send; new subscribers receive no late old entry through repair.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No publication record exists for this reference.; next AD-09 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Audit retry joins old intent; fresh recipients do not attach to old entry; delivered count cannot become filled count.

Run every SC-AD-10-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-110.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-10-P01 | Intent/revision | information | Intent/revision presents typed facts or approved explanatory content for Publication intent and cohort detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD10ReadModel.panels.01 |
| AD-10-P02 | Eligibility and frozen cohort | checklist | Eligibility and frozen cohort enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD10ReadModel.panels.02 |
| AD-10-P03 | Delivery attempts | table | Delivery attempts is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD10ReadModel.panels.03 |
| AD-10-P04 | External state | information | External state presents typed facts or approved explanatory content for Publication intent and cohort detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD10ReadModel.panels.04 |
| AD-10-P05 | Unknown resolution | information | Unknown resolution presents typed facts or approved explanatory content for Publication intent and cohort detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD10ReadModel.panels.05 |
| AD-10-P06 | Safety follow-up | information | Safety follow-up presents typed facts or approved explanatory content for Publication intent and cohort detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD10ReadModel.panels.06 |


---

# AD-11: Customers and scoped support record

**Purpose:** Support permitted customer workflows without impersonation or financial authority.

Page: `/ops/customers`. Service: `commercial`. Read model: `/api/v1/ui/ad-11`. Access: owner, support_readonly, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Customer search.
2. Profile/eligibility.
3. Subscription.
4. Mandates read view.
5. Cases.
6. Audit.

## Table and query behavior

Columns in default order: Customer, Eligibility, Plan, Copy state, Open cases, Consent.

Filters: State, Service, Case.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-11-A01 | Open case | AD-11; Open scoped support drawer under /ops/customers, not the customer session. Never impersonate /app. |
| AD-11-A02 | Send approved help | ACTION-AD-11-A02; Send only an approved template to scoped customer via consent-aware support channel; no investment recommendation or account impersonation. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No customers have signed up.

Next permitted route: `AD-01` (Commercial operations overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-SUPPORT: Support case

Steps: Category → Affected record → Details → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| category | Category | enum:billing,delivery,connection,performance,safety,access | yes | unset | known category; Safety priority triaged separately |
| related_object_id | Related record | id | conditional/optional | unset | caller can access object; No cross-tenant IDs |
| subject | Subject | text | yes | unset | 1..120 characters; Plain text |
| description | Description | text | yes | unset | 1..8000 characters;inert rendering; Do not enter passwords or API keys |
| attachment_ids | Attachments | id_list | conditional/optional | unset | owned quarantine-scanned objects;max5; max10MiB each;CSV/PNG/JPEG/PDF;no executable HTML |

**Save:** Persist scoped case

**Preview:** Show redaction/attachment scan status

**Confirm:** Create case and approved notification; no financial command

Staff scope least privilege; private execution secrets unavailable; customer may submit before subscribing.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No customers have signed up.; next AD-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Support cannot list secrets or alter mandates; cross-tenant access requires explicit authorized support scope and audited purpose.

Run every SC-AD-11-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-105.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-11-P01 | Customer search | information | Customer search presents typed facts or approved explanatory content for Customers and scoped support record, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD11ReadModel.panels.01 |
| AD-11-P02 | Profile/eligibility | checklist | Profile/eligibility enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD11ReadModel.panels.02 |
| AD-11-P03 | Subscription | information | Subscription presents typed facts or approved explanatory content for Customers and scoped support record, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD11ReadModel.panels.03 |
| AD-11-P04 | Mandates read view | information | Mandates read view presents typed facts or approved explanatory content for Customers and scoped support record, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD11ReadModel.panels.04 |
| AD-11-P05 | Cases | information | Cases presents typed facts or approved explanatory content for Customers and scoped support record, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD11ReadModel.panels.05 |
| AD-11-P06 | Audit | information | Audit presents typed facts or approved explanatory content for Customers and scoped support record, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD11ReadModel.panels.06 |


---

# AD-12: Business economics and royalties

**Purpose:** Report revenue, platform/provider costs and contribution margin separately from investment returns.

Page: `/ops/business`. Service: `commercial`. Read model: `/api/v1/ui/ad-12`. Access: owner, billing_operator, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Revenue/retention.
2. Fees/refunds.
3. Provider royalties.
4. Cost attribution.
5. Margin.
6. Statement exceptions.

## Table and query behavior

Columns in default order: Period, Revenue, Refunds, Processor cost, Provider royalty, Platform/data/cloud cost, Margin.

Filters: Period, Product, Currency, Booked/accrued.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-12-A01 | Export business report | F-REPORT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-12-A02 | Inspect invoice/royalty | local_business_record; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-AD-12-01 | Booked revenue | money | Booked subscription revenue/refund/cost items under documented accrual/cash convention. Separate currencies or approved conversion; investment profits are not revenue. |
| M-AD-12-02 | Refunds | money | Booked subscription revenue/refund/cost items under documented accrual/cash convention. Separate currencies or approved conversion; investment profits are not revenue. |
| M-AD-12-03 | Net contribution | money | Booked subscription revenue/refund/cost items under documented accrual/cash convention. Separate currencies or approved conversion; investment profits are not revenue. |
| M-AD-12-04 | Unresolved costs | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No financial business records are available.

Next permitted route: `AD-13` (Pricing, entitlements and billing operations). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-REPORT: Report / export request

Steps: Scope → Period/book → Format → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| object_ids | Scoped objects | id_list | yes | unset | server-authorized nonempty IDs;no all-tenants wildcard; Scope is immutable in job |
| period_start | Start | datetime | yes | unset | UTC instant;before end;within retention; User timezone converted explicitly |
| period_end | End | datetime | yes | unset | after start;not future for actual report; End-exclusive interval documented |
| book | Book | enum:actual,model,platform,source,business | yes | actual | supported for caller and report;business never investment; Do not merge origins |
| currency | Reporting currency | currency | conditional/optional | unset | supported conversion policy or show separate currencies; Reference rate basis shown |
| format | Format | enum:csv,json,pdf | yes | csv | server supports generation;PDF only after renderer implemented; No formula injection in CSV |
| include_sensitive | Include sensitive fields | boolean | conditional/optional | false | only explicitly permitted by current scope; Secrets always excluded |

**Save:** Persist export definition

**Preview:** Show coverage, cost basis and rights without file generation

**Confirm:** Start bounded export job; reauthorize at download

No public unapproved hypothetical export; expiring download token scoped to principal and record; failed job not empty report.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No financial business records are available.; next AD-13 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No subtraction of customer deposits as business refunds, no booking estimated royalties as paid; incomplete costs labeled.

Run every SC-AD-12-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-112.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-12-P01 | Revenue/retention | information | Revenue/retention presents typed facts or approved explanatory content for Business economics and royalties, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD12ReadModel.panels.01 |
| AD-12-P02 | Fees/refunds | information | Fees/refunds presents typed facts or approved explanatory content for Business economics and royalties, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD12ReadModel.panels.02 |
| AD-12-P03 | Provider royalties | information | Provider royalties presents typed facts or approved explanatory content for Business economics and royalties, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD12ReadModel.panels.03 |
| AD-12-P04 | Cost attribution | information | Cost attribution presents typed facts or approved explanatory content for Business economics and royalties, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD12ReadModel.panels.04 |
| AD-12-P05 | Margin | information | Margin presents typed facts or approved explanatory content for Business economics and royalties, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD12ReadModel.panels.05 |
| AD-12-P06 | Statement exceptions | information | Statement exceptions presents typed facts or approved explanatory content for Business economics and royalties, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD12ReadModel.panels.06 |


---

# AD-13: Pricing, entitlements and billing operations

**Purpose:** Manage test/live price versions and entitlement policies with merchant approval.

Page: `/ops/billing`. Service: `commercial`. Read model: `/api/v1/ui/ad-13`. Access: owner, billing_operator, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Price catalog.
2. Feature entitlements.
3. Processor identity.
4. Subscription changes.
5. Webhook reconciliation.

## Table and query behavior

Columns in default order: Price version, SKU, Currency, Interval, Mode, Approval, Active subscriptions.

Filters: SKU, Mode, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-13-A01 | Save test price draft | F-PRICE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-13-A02 | Preview entitlement change | F-PRICE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-13-A03 | Review processor event | local_processor_event; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No approved price versions exist; draft/test prices are private.

Next permitted route: `AD-07` (Products and portfolio versions). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-PRICE: Price and entitlement draft

Steps: Product/SKU → Interval/currency → Entitlements → Processor mode → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| sku | SKU | slug | yes | unset | immutable unique product plan key; No arbitrary client prices |
| currency | Currency | currency | yes | USD | supported merchant approved currency; Currency exponent from currency metadata |
| amount_minor | Amount in minor units | integer | yes | unset | >=0 within approved test/live pricing; Not float cents |
| interval | Interval | enum:month,year | yes | month | approved plan interval; No hidden renewal |
| portfolio_limit | Portfolio count | integer | yes | unset | nonnegative;defined unlimited only as explicit policy; No unexplained null unlimited |
| features | Feature IDs | id_list | yes | unset | reviewed entitlement registry; No implicit trading right |
| mode | Mode | enum:test,live | yes | test | live requires merchant/owner price approval; No auto paid launch |

**Save:** Create test-mode price version

**Preview:** Show total costs, entitlement diff and active subscriber impact

**Confirm:** Submit approved processor price request only in explicit mode

Existing subscriptions bind original price/version; downgrade has effective date and safe open-obligation treatment.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No approved price versions exist; draft/test prices are private.; next AD-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No arbitrary frontend amount or price ID. Admin cannot silently convert test to live or disable existing financial safety management after payment failure.

Run every SC-AD-13-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-095, CP-096, CP-112.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-13-P01 | Price catalog | information | Price catalog presents typed facts or approved explanatory content for Pricing, entitlements and billing operations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD13ReadModel.panels.01 |
| AD-13-P02 | Feature entitlements | information | Feature entitlements presents typed facts or approved explanatory content for Pricing, entitlements and billing operations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD13ReadModel.panels.02 |
| AD-13-P03 | Processor identity | information | Processor identity presents typed facts or approved explanatory content for Pricing, entitlements and billing operations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD13ReadModel.panels.03 |
| AD-13-P04 | Subscription changes | information | Subscription changes presents typed facts or approved explanatory content for Pricing, entitlements and billing operations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD13ReadModel.panels.04 |
| AD-13-P05 | Webhook reconciliation | information | Webhook reconciliation presents typed facts or approved explanatory content for Pricing, entitlements and billing operations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD13ReadModel.panels.05 |


---

# AD-14: Managed-program setup

**Purpose:** Configure broker-native PAMM/MAM program semantics and evidence without custody.

Page: `/ops/managed-programs`. Service: `commercial`. Read model: `/api/v1/ui/ad-14`. Access: owner, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Program identity.
2. Jurisdiction/broker gates.
3. Allocation method.
4. Mandates.
5. Dealing/fee conventions.
6. Version review.

## Table and query behavior

Columns in default order: Program, Broker, Mode, Allocation convention, NAV policy, Approval, State.

Filters: Broker, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-14-A01 | Create inactive program | F-MANAGED-PROGRAM; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-14-A02 | Attach broker agreement | ACTION-AD-14-A02; Create bounded private upload intent. Verify MIME/size, hash, malware/content status and tenant. Never execute attachment content; clean server-generated basename. |
| AD-14-A03 | Request review | F-MANAGED-PROGRAM; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No managed-account program is approved.

Next permitted route: `AD-02` (Rights and service approvals). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-MANAGED-PROGRAM: PAMM/MAM program config

Steps: Broker agreement → Allocation → Dealing → NAV/fees → Audience → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| program_name | Name | text | yes | unset | 1..100 plain text; No implication launched |
| broker_program_id | Broker program | id | yes | unset | broker-verified exact program; No internally invented pool |
| mode | Structure | enum:pamm,mam | yes | unset | approved contract semantics; Not interchangeable accounting |
| allocation_policy_id | Allocation policy | id | yes | unset | precommitted deterministic rule; No after-outcome selection |
| nav_policy_id | NAV policy | id | yes | unset | broker-approved generation/restatement convention; No incomplete estimate sold as NAV |
| dealing_schedule_id | Dealing schedule | id | yes | unset | cutoffs/timezone verified; No browser wall-clock authority |
| fee_policy_id | Fee policy | id | conditional/optional | unset | approved cashflow/HWM convention; Performance fees disabled without full convention |
| agreement_evidence_ids | Evidence | id_list | yes | unset | rights/legal/broker approvals; No automated signing |

**Save:** Save inactive program

**Preview:** Validate completeness and unresolved conventions

**Confirm:** Submit program release review, not custody or deposit action

UI built even when approvals absent; real cash handling remains through broker.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No managed-account program is approved.; next AD-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Cannot activate using local percentage table alone; fees/cashflows with unsupported convention remain blocked, not approximated.

Run every SC-AD-14-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-113.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-14-P01 | Program identity | information | Program identity presents typed facts or approved explanatory content for Managed-program setup, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD14ReadModel.panels.01 |
| AD-14-P02 | Jurisdiction/broker gates | information | Jurisdiction/broker gates presents typed facts or approved explanatory content for Managed-program setup, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD14ReadModel.panels.02 |
| AD-14-P03 | Allocation method | information | Allocation method presents typed facts or approved explanatory content for Managed-program setup, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD14ReadModel.panels.03 |
| AD-14-P04 | Mandates | information | Mandates presents typed facts or approved explanatory content for Managed-program setup, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD14ReadModel.panels.04 |
| AD-14-P05 | Dealing/fee conventions | information | Dealing/fee conventions presents typed facts or approved explanatory content for Managed-program setup, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD14ReadModel.panels.05 |
| AD-14-P06 | Version review | checklist | Version review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD14ReadModel.panels.06 |


---

# AD-15: Managed allocations, NAV and dealing

**Purpose:** Review broker-originated allocations, NAV generations and allowed dealing requests.

Page: `/ops/managed-operations`. Service: `commercial`. Read model: `/api/v1/ui/ad-15`. Access: owner, publisher_operator, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Dealing queue.
2. Precommitted allocation.
3. NAV generation.
4. Cash flows/units.
5. Fee/HWM review.
6. Restatements.

## Table and query behavior

Columns in default order: Account, Allocation basis, NAV generation, Units, Cashflow cutoff, Fee, Discrepancy.

Filters: Program, Dealing date, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-15-A01 | Preview allocation | F-DEALING; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-15-A02 | Review NAV discrepancy | local_nav_review; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| AD-15-A03 | Export broker-bound instructions | F-DEALING; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No qualified dealing or NAV records exist.

Next permitted route: `AD-14` (Managed-program setup). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-DEALING: Dealing and allocation review

Steps: Program/generation → Requests → Allocation → Evidence → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| program_id | Program | id | yes | unset | approved scoped program; No generic account multiplier |
| nav_generation_id | NAV generation | id | yes | unset | verified current broker generation; Immutable |
| request_ids | Requests | id_list | yes | unset | eligible cutoff cohort; No add after allocation outcome known |
| allocation_manifest_id | Allocation | id | yes | unset | deterministic policy and tie-break bound; Conserves units/amounts |
| reason | Reason | text | yes | unset | 1..2000 plain text; Required audit |

**Save:** Save review

**Preview:** Show broker-defined allocation/fees/rounding and unresolved conditions

**Confirm:** Prepare approved broker instruction or await external authority

No local invented transfer. Corrected NAV causes new generation and revised report, not overwrite.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No qualified dealing or NAV records exist.; next AD-14 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No post-outcome favoritism. Requests do not move money; corrections version prior reports and preserve original records.

Run every SC-AD-15-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-113.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-15-P01 | Dealing queue | table | Dealing queue is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD15ReadModel.panels.01 |
| AD-15-P02 | Precommitted allocation | information | Precommitted allocation presents typed facts or approved explanatory content for Managed allocations, NAV and dealing, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD15ReadModel.panels.02 |
| AD-15-P03 | NAV generation | information | NAV generation presents typed facts or approved explanatory content for Managed allocations, NAV and dealing, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD15ReadModel.panels.03 |
| AD-15-P04 | Cash flows/units | information | Cash flows/units presents typed facts or approved explanatory content for Managed allocations, NAV and dealing, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD15ReadModel.panels.04 |
| AD-15-P05 | Fee/HWM review | checklist | Fee/HWM review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD15ReadModel.panels.05 |
| AD-15-P06 | Restatements | information | Restatements presents typed facts or approved explanatory content for Managed allocations, NAV and dealing, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD15ReadModel.panels.06 |


---

# AD-16: Staff roles and access reviews

**Purpose:** Manage explicit commercial-role memberships and scoped temporary support grants.

Page: `/ops/access`. Service: `commercial`. Read model: `/api/v1/ui/ad-16`. Access: owner, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Staff/memberships.
2. Action permissions.
3. Access review.
4. Revocations.
5. Session audit.

## Table and query behavior

Columns in default order: User, Role, Scope, Expiry, Grantor, Last review.

Filters: Role, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-16-A01 | Invite scoped staff | F-ACCESS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-16-A02 | Revoke membership | F-ACCESS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-16-A03 | Review access | F-ACCESS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No delegated staff memberships exist.

Next permitted route: `AD-01` (Commercial operations overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-ACCESS: Commercial role membership

Steps: Identity → Role → Scope → Expiry → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| user_id | User | id | yes | unset | verified existing or invited identity; No arbitrary JWT claims |
| role | Role | enum:researcher,reviewer,publisher_operator,billing_operator,support_readonly | yes | unset | explicit named role; Owner grants not through self-service form |
| scope_ids | Scope | id_list | yes | unset | minimum necessary objects/tenants; No implicit global scope |
| expires_at | Expiry | datetime | conditional/optional | unset | required for temporary support grant; Current time server-side |
| purpose | Purpose | text | yes | unset | 1..1000 characters; Audited access |

**Save:** Save proposed grant

**Preview:** Display exact read and effect capabilities

**Confirm:** Apply owner-authorized membership and session invalidation as appropriate

Customer cannot access admin role route; support impersonation and private-secret access absent.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No delegated staff memberships exist.; next AD-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Owner is not implicit universal action bypass; unknown permission denies. Role change cannot self-approve release or reveal private owner accounts.

Run every SC-AD-16-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-105, CP-109, CP-111.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-16-P01 | Staff/memberships | information | Staff/memberships presents typed facts or approved explanatory content for Staff roles and access reviews, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD16ReadModel.panels.01 |
| AD-16-P02 | Action permissions | information | Action permissions presents typed facts or approved explanatory content for Staff roles and access reviews, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD16ReadModel.panels.02 |
| AD-16-P03 | Access review | checklist | Access review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD16ReadModel.panels.03 |
| AD-16-P04 | Revocations | information | Revocations presents typed facts or approved explanatory content for Staff roles and access reviews, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD16ReadModel.panels.04 |
| AD-16-P05 | Session audit | information | Session audit presents typed facts or approved explanatory content for Staff roles and access reviews, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD16ReadModel.panels.05 |


---

# AD-17: Integrations, data rights and quotas

**Purpose:** Track actual endpoint capabilities, data entitlements, quotas and secret references.

Page: `/ops/integrations`. Service: `commercial`. Read model: `/api/v1/ui/ad-17`. Access: owner, publisher_operator, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Integration inventory.
2. Entitlements/use.
3. Freshness/history.
4. Quota/cost.
5. Credential lifecycle.

## Table and query behavior

Columns in default order: Provider, Purpose, Mode, Entitlement, Usable observation, Quota/reset, Cost, State.

Filters: Purpose, Provider, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-17-A01 | Save inactive configuration | F-INTEGRATION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-17-A02 | Verify permitted reads | F-INTEGRATION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-17-A03 | Inspect quota | local_quota; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No commercial integrations are configured.

Next permitted route: `AD-17` (Integrations, data rights and quotas). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-INTEGRATION: Integration configuration

Steps: Registry → Purpose → Mode → Entitlement → Credentials → Quotas.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| provider_registry_id | Provider | id | yes | unset | reviewed allowlist; No arbitrary service URL |
| purpose | Purpose | enum:research,quotes,reference,publication,billing,monitoring | yes | unset | approved provider use; Research credentials have no trade powers |
| environment | Environment | enum:test,demo,live | yes | test | purpose-specific mapping; Explicit live approval |
| credential_ref | Secret reference | secret_ref | conditional/optional | unset | role-scoped vault reference; No secret echo |
| entitlement_evidence_id | Entitlement/use terms | id | conditional/optional | unset | required for selected data use; Free software is not free data rights |
| quota_profile_id | Quota/cost profile | id | yes | unset | known request weights/reset/cost cap; No paid fallback |

**Save:** Save inactive config

**Preview:** Permitted read-only check and quota forecast

**Confirm:** Apply approved configuration only

Get/read endpoint must still enforce host/redirect authorization and payload limits.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No commercial integrations are configured.; next AD-17 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

API reachable is not entitled; generic GET is not permission to fetch arbitrary hosts. No paid fallback without approval.

Run every SC-AD-17-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-100, CP-110, CP-111.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-17-P01 | Integration inventory | table | Integration inventory is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD17ReadModel.panels.01 |
| AD-17-P02 | Entitlements/use | information | Entitlements/use presents typed facts or approved explanatory content for Integrations, data rights and quotas, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD17ReadModel.panels.02 |
| AD-17-P03 | Freshness/history | table | Freshness/history is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD17ReadModel.panels.03 |
| AD-17-P04 | Quota/cost | information | Quota/cost presents typed facts or approved explanatory content for Integrations, data rights and quotas, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD17ReadModel.panels.04 |
| AD-17-P05 | Credential lifecycle | information | Credential lifecycle presents typed facts or approved explanatory content for Integrations, data rights and quotas, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD17ReadModel.panels.05 |


---

# AD-18: Audit log and release evidence

**Purpose:** Search immutable events and evidence bundles with exact scope and revisions.

Page: `/ops/audit`. Service: `commercial`. Read model: `/api/v1/ui/ad-18`. Access: owner, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Event search.
2. Object timeline.
3. Evidence manifest.
4. Export queue.

## Table and query behavior

Columns in default order: Event, Actor, Tenant/scope, Action, Before/after hash, Outcome, Correlation, Time.

Filters: Actor, Object, Action, Time, Outcome.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-18-A01 | Inspect event | local_audit_event; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| AD-18-A02 | Export scoped bundle | F-REPORT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No audit events match these filters.

Next permitted route: `AD-01` (Commercial operations overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-REPORT: Report / export request

Steps: Scope → Period/book → Format → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| object_ids | Scoped objects | id_list | yes | unset | server-authorized nonempty IDs;no all-tenants wildcard; Scope is immutable in job |
| period_start | Start | datetime | yes | unset | UTC instant;before end;within retention; User timezone converted explicitly |
| period_end | End | datetime | yes | unset | after start;not future for actual report; End-exclusive interval documented |
| book | Book | enum:actual,model,platform,source,business | yes | actual | supported for caller and report;business never investment; Do not merge origins |
| currency | Reporting currency | currency | conditional/optional | unset | supported conversion policy or show separate currencies; Reference rate basis shown |
| format | Format | enum:csv,json,pdf | yes | csv | server supports generation;PDF only after renderer implemented; No formula injection in CSV |
| include_sensitive | Include sensitive fields | boolean | conditional/optional | false | only explicitly permitted by current scope; Secrets always excluded |

**Save:** Persist export definition

**Preview:** Show coverage, cost basis and rights without file generation

**Confirm:** Start bounded export job; reauthorize at download

No public unapproved hypothetical export; expiring download token scoped to principal and record; failed job not empty report.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No audit events match these filters.; next AD-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Audit cannot be edited through UI; redacted exports retain evidence hashes and scope; export access rechecked at retrieval.

Run every SC-AD-18-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-105, CP-109, CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-18-P01 | Event search | information | Event search presents typed facts or approved explanatory content for Audit log and release evidence, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD18ReadModel.panels.01 |
| AD-18-P02 | Object timeline | timeline | Object timeline shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: AD18ReadModel.panels.02 |
| AD-18-P03 | Evidence manifest | information | Evidence manifest presents typed facts or approved explanatory content for Audit log and release evidence, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD18ReadModel.panels.03 |
| AD-18-P04 | Export queue | table | Export queue is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD18ReadModel.panels.04 |


---

# AD-19: Content and disclosure publishing

**Purpose:** Author plain text/approved structured content and publish audience-safe projections.

Page: `/ops/content`. Service: `commercial`. Read model: `/api/v1/ui/ad-19`. Access: owner, reviewer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Content register.
2. Editor.
3. Disclosure references.
4. Review.
5. Versioned preview.

## Table and query behavior

Columns in default order: Document/page, Version, Audience, Approval, Effective, State.

Filters: Type, Language, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-19-A01 | Save draft | F-CONTENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-19-A02 | Preview | F-CONTENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-19-A03 | Submit content review | F-CONTENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No public content has been approved.

Next permitted route: `AD-19` (Content and disclosure publishing). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-CONTENT: Approved public content

Steps: Document → Plain content → Audience → Sources → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| document_type | Type | enum:methodology,risk,billing_terms,privacy,help,status | yes | unset | known structured template; No arbitrary HTML |
| locale | Language | locale | yes | en-US | supported language; Translations reviewed for factual consistency |
| title | Title | text | yes | unset | 1..120 plain text; No unsupported claims |
| body | Content | text | yes | unset | bounded20000 plain text or safe structured nodes; No scripts/iframes/event handlers |
| audience_policy_id | Audience | id | yes | unset | approved scope; No raw licenses |
| source_evidence_ids | Supporting evidence | id_list | conditional/optional | unset | required for factual performance claims; No model invented citations |

**Save:** Save private content draft

**Preview:** Render using same safe renderer under no-store private route

**Confirm:** Submit for review; publishing distinct approved command

No unreviewed AI marketing text; consent signed on exact published document version.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No public content has been approved.; next AD-19 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No raw HTML/script editor, unsupported return claims or customer testimonials inserted by automation. Draft preview is private/no-store.

Run every SC-AD-19-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-091, CP-092, CP-094.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-19-P01 | Content register | table | Content register is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD19ReadModel.panels.01 |
| AD-19-P02 | Editor | form | Editor uses the fields of F-CONTENT. Group by the specified steps; show inherited/effective values and field validation. Persist only scoped explicit drafts. Query: AD19ReadModel.panels.02 |
| AD-19-P03 | Disclosure references | information | Disclosure references presents typed facts or approved explanatory content for Content and disclosure publishing, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD19ReadModel.panels.03 |
| AD-19-P04 | Review | checklist | Review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD19ReadModel.panels.04 |
| AD-19-P05 | Versioned preview | checklist | Versioned preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD19ReadModel.panels.05 |


---

# AD-20: Workspace customization and configuration

**Purpose:** Manage shared appearance, notification routing and typed operator defaults.

Page: `/ops/settings`. Service: `commercial`. Read model: `/api/v1/ui/ad-20`. Access: owner, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Brand/display.
2. Layouts.
3. Notification routing.
4. Feature availability.
5. Effective settings diff.

## Table and query behavior

Columns in default order: Setting, Scope, Inherited, Draft, Effective, Approval.

Filters: Scope, Category.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-20-A01 | Save personal view | F-WORKSPACE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-20-A02 | Save admin draft | F-WORKSPACE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-20-A03 | Reset display | F-WORKSPACE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

Default workspace settings are active.

Next permitted route: `AD-01` (Commercial operations overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-WORKSPACE: Workspace and saved views

Steps: Display → Navigation → Notifications → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| workspace_name | Workspace label | text | yes | unset | 1..80 plain text; No impersonating another entity |
| theme | Theme | enum:system,dark,light | yes | system | known theme; Shared visual tokens only |
| density | Density | enum:comfortable,compact | yes | comfortable | accessible minimum controls; Not a risk policy |
| visible_panel_ids | Optional panels | id_list | conditional/optional | unset | allowlisted current role panels; Mandatory safety/fees/origin cannot hide |
| column_order | Table columns | id_list | conditional/optional | unset | allowlisted;required identity columns retained; Move via buttons not drag-only |
| notification_route_id | Operations notifications | id | conditional/optional | unset | verified recipient/channel policy; Test notice non-actionable |

**Save:** Persist personal view or workspace draft

**Preview:** Show exact cosmetic scope and mandatory panels

**Confirm:** Save display configuration; financial configuration separate review

No custom CSS/JS/SQL/plugin imports. Reordering accessible and versioned per principal/workspace/environment.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | Default workspace settings are active.; next AD-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Cosmetic configuration never changes policy, financial permissions or required disclosure visibility; arbitrary CSS/JS prohibited.

Run every SC-AD-20-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-099, CP-109.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-20-P01 | Brand/display | information | Brand/display presents typed facts or approved explanatory content for Workspace customization and configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD20ReadModel.panels.01 |
| AD-20-P02 | Layouts | information | Layouts presents typed facts or approved explanatory content for Workspace customization and configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD20ReadModel.panels.02 |
| AD-20-P03 | Notification routing | information | Notification routing presents typed facts or approved explanatory content for Workspace customization and configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD20ReadModel.panels.03 |
| AD-20-P04 | Feature availability | information | Feature availability presents typed facts or approved explanatory content for Workspace customization and configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD20ReadModel.panels.04 |
| AD-20-P05 | Effective settings diff | information | Effective settings diff presents typed facts or approved explanatory content for Workspace customization and configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD20ReadModel.panels.05 |


---

# AD-21: Commercial incidents and obligations

**Purpose:** Coordinate incidents across rights, payment, publication and customer exposure.

Page: `/ops/incidents`. Service: `commercial`. Read model: `/api/v1/ui/ad-21`. Access: owner, publisher_operator, support_readonly, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Priority queue.
2. Affected obligations.
3. Timeline.
4. Containment and notices.
5. Resolution evidence.

## Table and query behavior

Columns in default order: Incident, Service, Affected cohort, Severity, Open obligations, Assigned, State.

Filters: Service, Severity, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-21-A01 | Acknowledge | F-INCIDENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-21-A02 | Assign | F-INCIDENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-21-A03 | Preview approved notice | F-INCIDENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No active commercial incidents.

Next permitted route: `AD-01` (Commercial operations overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-INCIDENT: Incident management

Steps: Evidence → Responsibility → Containment → Resolution.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| incident_id | Incident | id | yes | unset | within authorized scope; Persisted incident |
| operation | Operation | enum:acknowledge,assign,reconcile,propose_resolution | yes | acknowledge | explicit per-role permission; No execute arbitrary command |
| assignee_id | Assignee | id | conditional/optional | unset | active role eligible for incident; Cannot assign financial authority |
| note | Note | text | yes | unset | 1..2000 plain text; No secrets |
| evidence_ids | Evidence | id_list | conditional/optional | unset | scoped immutable objects; required for resolution proposal |

**Save:** Save comment/action intent

**Preview:** Show affected scope and proposed operation

**Confirm:** Acknowledge/assign/readback job or submit resolution review

Acknowledge never marks resolved. Reconcile is read-only broker action unless separately approved corrective command.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No active commercial incidents.; next AD-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

One incident can affect billing without revoking permitted safety management. Support/marketing acknowledgment never resolves publisher uncertainty.

Run every SC-AD-21-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-105, CP-110, CP-111, CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-21-P01 | Priority queue | table | Priority queue is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD21ReadModel.panels.01 |
| AD-21-P02 | Affected obligations | information | Affected obligations presents typed facts or approved explanatory content for Commercial incidents and obligations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD21ReadModel.panels.02 |
| AD-21-P03 | Timeline | timeline | Timeline shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: AD21ReadModel.panels.03 |
| AD-21-P04 | Containment and notices | information | Containment and notices presents typed facts or approved explanatory content for Commercial incidents and obligations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD21ReadModel.panels.04 |
| AD-21-P05 | Resolution evidence | information | Resolution evidence presents typed facts or approved explanatory content for Commercial incidents and obligations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD21ReadModel.panels.05 |


---

# AD-22: Commercial deployments and recovery

**Purpose:** Inspect API, research and publisher health and controlled recovery readiness.

Page: `/ops/system`. Service: `commercial`. Read model: `/api/v1/ui/ad-22`. Access: owner, publisher_operator, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Role/site/artifact.
2. Workers and queues.
3. Backup generations.
4. Fencing evidence.
5. Qualification.
6. Owner action cards.

## Table and query behavior

Columns in default order: Role, Site, Artifact, Latest complete task, Database generation, Authority, Blocker.

Filters: Site, Role, Service.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| AD-22-A01 | Open recovery evidence | local_recovery; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| AD-22-A02 | Run isolated restore check | F-RECOVERY-OPS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| AD-22-A03 | Prepare release card | F-RECOVERY-OPS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No deployment has been qualified for this service.

Next permitted route: `AD-01` (Commercial operations overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-RECOVERY-OPS: Recovery review

Steps: Scope → Artifact/data → Old writer fencing → Reconciliation → Approvals.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| service_role | Role | enum:private_writer,publisher,api,research | yes | unset | operator allowed for role; Website role not trading permission |
| source_site_id | Old site | id | yes | unset | registered site; No arbitrary SSH host |
| target_site_id | Target site | id | yes | unset | qualified distinct site; Standby initially incapable of effects |
| release_manifest_id | Release | id | yes | unset | verified immutable manifest; No mutable latest tag |
| backup_generation_id | Data generation | id | yes | unset | verified compatible restore; Known RPO/unknown commands visible |
| fencing_evidence_ids | Fencing evidence | id_list | conditional/optional | unset | required before writer promotion; Lease expiry is insufficient |
| reconciliation_evidence_id | Reconciliation | id | conditional/optional | unset | required for promotion;fresh and complete; Positions alone may not recover ownership |

**Save:** Persist review checklist

**Preview:** Evaluate required evidence; no infrastructure effects

**Confirm:** Prepare signed release/handoff card; no unattended promote from GUI in this scope

Only separate authorized deployment runbook applies effect. Default no auto failover and no storing root credentials in UI.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No deployment has been qualified for this service.; next AD-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Web health cannot imply financial readiness; no enabled automatic promotion before independent old-writer fencing.

Run every SC-AD-22-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| AD-22-P01 | Role/site/artifact | context | Role/site/artifact binds the exact selected object, revision, environment, audience and data origin for Commercial deployments and recovery. Context changes clear old scoped responses. Query: AD22ReadModel.panels.01 |
| AD-22-P02 | Workers and queues | table | Workers and queues is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: AD22ReadModel.panels.02 |
| AD-22-P03 | Backup generations | information | Backup generations presents typed facts or approved explanatory content for Commercial deployments and recovery, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD22ReadModel.panels.03 |
| AD-22-P04 | Fencing evidence | information | Fencing evidence presents typed facts or approved explanatory content for Commercial deployments and recovery, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD22ReadModel.panels.04 |
| AD-22-P05 | Qualification | checklist | Qualification enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: AD22ReadModel.panels.05 |
| AD-22-P06 | Owner action cards | information | Owner action cards presents typed facts or approved explanatory content for Commercial deployments and recovery, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: AD22ReadModel.panels.06 |


---

# CU-01: Customer overview

**Purpose:** Summarize subscriptions, copying state, actual results and required actions.

Page: `/app`. Service: `commercial`. Read model: `/api/v1/ui/cu-01`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Context bar.
2. Subscribed portfolios.
3. Actual performance card.
4. Alerts.
5. Connection status.
6. Action center.

## Table and query behavior

Columns in default order: Portfolio, Subscription, Mandate, Platform, Last usable update, Required action.

Filters: Portfolio, Time range, Environment.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-01-A01 | Browse portfolios | PU-02; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| CU-01-A02 | Review setup | CU-07; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-CU-01-01 | Selected portfolios | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-CU-01-02 | Observed open episodes | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-CU-01-03 | Net result if available | money | Sum identified realized and eligible unrealized P&L minus applicable costs, with cashflows excluded and all conversions versioned. Missing fees/marks/basis => partial or unavailable, not net verification. |


## Empty state

You have not selected a portfolio.

Next permitted route: `PU-02` (Portfolio catalog). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | You have not selected a portfolio.; next PU-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Subscription, connection and live copying badges are independent. No connection means actual performance unavailable, not model return.

Run every SC-CU-01-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-097, CP-098, CP-102.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-01-P01 | Context bar | context | Context bar binds the exact selected object, revision, environment, audience and data origin for Customer overview. Context changes clear old scoped responses. Query: CU01ReadModel.panels.01 |
| CU-01-P02 | Subscribed portfolios | table | Subscribed portfolios is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU01ReadModel.panels.02 |
| CU-01-P03 | Actual performance card | chart | Actual performance card uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: CU01ReadModel.panels.03 |
| CU-01-P04 | Alerts | information | Alerts presents typed facts or approved explanatory content for Customer overview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU01ReadModel.panels.04 |
| CU-01-P05 | Connection status | checklist | Connection status enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU01ReadModel.panels.05 |
| CU-01-P06 | Action center | checklist | Action center enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU01ReadModel.panels.06 |


---

# CU-02: My portfolios

**Purpose:** Manage selections and versions without creating unintended orders.

Page: `/app/portfolios`. Service: `commercial`. Read model: `/api/v1/ui/cu-02`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Selections table.
2. Included-plan capacity.
3. Version notices.
4. Add action.

## Table and query behavior

Columns in default order: Portfolio, Selected version, Plan entitlement, Publication status, Copy state, Joined.

Filters: State, Channel, Origin.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-02-A01 | Select portfolio | F-SELECTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-02-A02 | Open selection | CU-03; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

You have no portfolio selections.

Next permitted route: `PU-02` (Portfolio catalog). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-SELECTION: Select a portfolio

Steps: Choose product/version → Eligibility → Plan capacity → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| product_id | Product | id | yes | unset | published and audience-visible; Draft IDs rejected |
| portfolio_version_id | Version | id | yes | unset | belongs to product;released; Version binding preserved |
| subscription_id | Subscription | id | conditional/optional | unset | owned and entitlement-compatible; required for paid access only |
| start_mode | Start mode | enum:new_entries_only | yes | new_entries_only | only new_entries_only here; Existing-position sync is a separate mandate flow |

**Save:** Persist selection draft

**Preview:** Show entitlement/rights/capacity and missing gates

**Confirm:** Create scoped selection only; never publish or trade

Duplicate selection returns existing record; version material change requires fresh preview.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | You have no portfolio selections.; next PU-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Saving a selection creates only a selection row; no historical entries are replayed.

Run every SC-CU-02-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-097.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-02-P01 | Selections table | table | Selections table is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU02ReadModel.panels.01 |
| CU-02-P02 | Included-plan capacity | information | Included-plan capacity presents typed facts or approved explanatory content for My portfolios, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU02ReadModel.panels.02 |
| CU-02-P03 | Version notices | information | Version notices presents typed facts or approved explanatory content for My portfolios, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU02ReadModel.panels.03 |
| CU-02-P04 | Add action | information | Add action presents typed facts or approved explanatory content for My portfolios, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU02ReadModel.panels.04 |


---

# CU-03: Selected portfolio detail

**Purpose:** Show the customer’s exact selection, alerts, version and copy obligations.

Page: `/app/portfolios/{selection_id}`. Service: `commercial`. Read model: `/api/v1/ui/cu-03`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Identity/version.
2. Actual versus model tabs.
3. Alerts/trades.
4. Effective settings.
5. Changes and safety actions.

## Table and query behavior

Columns in default order: Episode, Source-publication time, Customer delivery, Customer fill state, Cost, Remaining quantity.

Filters: Period, Actual/model, Lifecycle state.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-03-A01 | Open connection | CU-07; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| CU-03-A02 | Change future selection | CU-02; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| CU-03-A03 | Pause new copying | CU-10; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-CU-03-01 | Observed net P&L | money | Sum identified realized and eligible unrealized P&L minus applicable costs, with cashflows excluded and all conversions versioned. Missing fees/marks/basis => partial or unavailable, not net verification. |
| M-CU-03-02 | Open episodes | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-CU-03-03 | Delivery lag | seconds | Difference between identified server times with timezone/clock semantics and start/end evidence. Missing either end => ongoing or unavailable, not0. |


## Empty state

No activity is recorded for this selection.

Next permitted route: `CU-07` (Platform connections). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No activity is recorded for this selection.; next CU-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

New version notice does not silently switch open episodes; approved customer actual read scope survives correct plan wind-down.

Run every SC-CU-03-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-097, CP-101, CP-102, CP-104.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-03-P01 | Identity/version | information | Identity/version presents typed facts or approved explanatory content for Selected portfolio detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU03ReadModel.panels.01 |
| CU-03-P02 | Actual versus model tabs | information | Actual versus model tabs presents typed facts or approved explanatory content for Selected portfolio detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU03ReadModel.panels.02 |
| CU-03-P03 | Alerts/trades | information | Alerts/trades presents typed facts or approved explanatory content for Selected portfolio detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU03ReadModel.panels.03 |
| CU-03-P04 | Effective settings | information | Effective settings presents typed facts or approved explanatory content for Selected portfolio detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU03ReadModel.panels.04 |
| CU-03-P05 | Changes and safety actions | information | Changes and safety actions presents typed facts or approved explanatory content for Selected portfolio detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU03ReadModel.panels.05 |


---

# CU-04: Alerts and delivery history

**Purpose:** Read entitled alerts in correct lifecycle sequence.

Page: `/app/alerts`. Service: `commercial`. Read model: `/api/v1/ui/cu-04`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Filters.
2. Live indicator.
3. Alert timeline/table.
4. Delivery receipts.

## Table and query behavior

Columns in default order: Alert, Portfolio version, Action, Instrument, Origin, Published, Delivered, Expires, Lifecycle state.

Filters: Portfolio, Action, Instrument, Delivery, Period.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-04-A01 | Open alert | CU-05; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| CU-04-A02 | Pause display updates | local_pause_display; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

There are no alerts available for this selection and period.

Next permitted route: `CU-02` (My portfolios). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | There are no alerts available for this selection and period.; next CU-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Delivered does not mean traded; expired entries cannot be activated or recast as current. Marketing text cannot replace action meaning.

Run every SC-CU-04-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-098.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-04-P01 | Filters | information | Filters presents typed facts or approved explanatory content for Alerts and delivery history, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU04ReadModel.panels.01 |
| CU-04-P02 | Live indicator | information | Live indicator presents typed facts or approved explanatory content for Alerts and delivery history, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU04ReadModel.panels.02 |
| CU-04-P03 | Alert timeline/table | timeline | Alert timeline/table shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: CU04ReadModel.panels.03 |
| CU-04-P04 | Delivery receipts | table | Delivery receipts is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU04ReadModel.panels.04 |


---

# CU-05: Alert, trade and order-family detail

**Purpose:** Explain one customer-visible episode and actual observed execution state.

Page: `/app/activity/{episode_id}`. Service: `commercial`. Read model: `/api/v1/ui/cu-05`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Identity and origin.
2. Instruction revisions.
3. Publication/delivery timeline.
4. Execution and fees.
5. Protection coverage.
6. Errors and help.

## Table and query behavior

Columns in default order: Order family, Status, Requested, Filled, May still fill, Working protection, As of.

Filters: Event type, Actual/model.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-05-A01 | Download scoped record | ACTION-CU-05-A01; Create redacted export job from frozen permitted filters and object versions; reauthorize download; no financial action. |
| CU-05-A02 | Report discrepancy | CU-14; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-CU-05-01 | Owned quantity | instrument_quantity | Authoritative execution/position/order-family ledger for selected allocation. Never derive ownership from requested order quantity; null/unknown distinct from known zero. |
| M-CU-05-02 | Possible remaining closes | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-CU-05-03 | Native-covered quantity | instrument_quantity | Authoritative execution/position/order-family ledger for selected allocation. Never derive ownership from requested order quantity; null/unknown distinct from known zero. |


## Empty state

This episode has no available broker observations.

Next permitted route: `CU-07` (Platform connections). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | This episode has no available broker observations.; next CU-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Source raw text is redacted when not licensed. Unknown stop coverage is never rendered protected. Customer sees only own allocations.

Run every SC-CU-05-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-098, CP-103.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-05-P01 | Identity and origin | information | Identity and origin presents typed facts or approved explanatory content for Alert, trade and order-family detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU05ReadModel.panels.01 |
| CU-05-P02 | Instruction revisions | information | Instruction revisions presents typed facts or approved explanatory content for Alert, trade and order-family detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU05ReadModel.panels.02 |
| CU-05-P03 | Publication/delivery timeline | timeline | Publication/delivery timeline shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: CU05ReadModel.panels.03 |
| CU-05-P04 | Execution and fees | information | Execution and fees presents typed facts or approved explanatory content for Alert, trade and order-family detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU05ReadModel.panels.04 |
| CU-05-P05 | Protection coverage | information | Protection coverage presents typed facts or approved explanatory content for Alert, trade and order-family detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU05ReadModel.panels.05 |
| CU-05-P06 | Errors and help | information | Errors and help presents typed facts or approved explanatory content for Alert, trade and order-family detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU05ReadModel.panels.06 |


---

# CU-06: Performance and costs

**Purpose:** Report reconciled customer results separately from model or platform records.

Page: `/app/performance`. Service: `commercial`. Read model: `/api/v1/ui/cu-06`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Book selector.
2. Return/drawdown charts.
3. Costs and cash flows.
4. Attribution.
5. Data-quality table.

## Table and query behavior

Columns in default order: Period, Gross, Trading costs, Subscription cost basis, Net, Deposits/withdrawals, Quality.

Filters: Portfolio, Book, Period, Currency, Cost basis.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-06-A01 | Export report | F-REPORT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-06-A02 | Inspect reconciliation | local_reconciliation; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-CU-06-01 | Net P&L | money | Sum identified realized and eligible unrealized P&L minus applicable costs, with cashflows excluded and all conversions versioned. Missing fees/marks/basis => partial or unavailable, not net verification. |
| M-CU-06-02 | Maximum drawdown | ratio | max over chronological normalized equity of (running_peak-equity)/running_peak. Requires positive comparable equity and full stated history; use full-resolution data before chart decimation; unknown gaps qualify coverage. |
| M-CU-06-03 | Completed-episode win rate | ratio | count(net completed strategy episodes >0) / count(all eligible completed episodes). Zero completed episodes => unavailable; reducing fill count is separate; no exclusion of losses or zero-result episodes. |
| M-CU-06-04 | Unresolved items | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No qualified performance series is available.

Next permitted route: `CU-07` (Platform connections). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-REPORT: Report / export request

Steps: Scope → Period/book → Format → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| object_ids | Scoped objects | id_list | yes | unset | server-authorized nonempty IDs;no all-tenants wildcard; Scope is immutable in job |
| period_start | Start | datetime | yes | unset | UTC instant;before end;within retention; User timezone converted explicitly |
| period_end | End | datetime | yes | unset | after start;not future for actual report; End-exclusive interval documented |
| book | Book | enum:actual,model,platform,source,business | yes | actual | supported for caller and report;business never investment; Do not merge origins |
| currency | Reporting currency | currency | conditional/optional | unset | supported conversion policy or show separate currencies; Reference rate basis shown |
| format | Format | enum:csv,json,pdf | yes | csv | server supports generation;PDF only after renderer implemented; No formula injection in CSV |
| include_sensitive | Include sensitive fields | boolean | conditional/optional | false | only explicitly permitted by current scope; Secrets always excluded |

**Save:** Persist export definition

**Preview:** Show coverage, cost basis and rights without file generation

**Confirm:** Start bounded export job; reauthorize at download

No public unapproved hypothetical export; expiring download token scoped to principal and record; failed job not empty report.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No qualified performance series is available.; next CU-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Zero values require evidence; unknown values stay null; invalid currency aggregation blocked. Partial trims do not become multiple completed trades.

Run every SC-CU-06-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-102.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-06-P01 | Book selector | information | Book selector presents typed facts or approved explanatory content for Performance and costs, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU06ReadModel.panels.01 |
| CU-06-P02 | Return/drawdown charts | chart | Return/drawdown charts uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: CU06ReadModel.panels.02 |
| CU-06-P03 | Costs and cash flows | information | Costs and cash flows presents typed facts or approved explanatory content for Performance and costs, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU06ReadModel.panels.03 |
| CU-06-P04 | Attribution | information | Attribution presents typed facts or approved explanatory content for Performance and costs, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU06ReadModel.panels.04 |
| CU-06-P05 | Data-quality table | table | Data-quality table is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU06ReadModel.panels.05 |


---

# CU-07: Platform connections

**Purpose:** Manage customer-owned platform identities and permission status.

Page: `/app/connections`. Service: `commercial`. Read model: `/api/v1/ui/cu-07`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Connection cards/table.
2. Capability report.
3. Eligibility.
4. Reauthorization tasks.

## Table and query behavior

Columns in default order: Connection, Platform, Environment, Masked account, Scope, Readback freshness, Mandates, State.

Filters: Platform, Environment, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-07-A01 | Connect platform | CU-08; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| CU-07-A02 | Reauthorize | CU-08; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| CU-07-A03 | Review disconnect | CU-10; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No platform account is connected.

Next permitted route: `CU-08` (Connection wizard). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No platform account is connected.; next CU-08 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Connected does not mean authorized to copy. Disconnect checks pending exposure and preserves scoped handoff.

Run every SC-CU-07-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-100.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-07-P01 | Connection cards/table | table | Connection cards/table is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU07ReadModel.panels.01 |
| CU-07-P02 | Capability report | information | Capability report presents typed facts or approved explanatory content for Platform connections, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU07ReadModel.panels.02 |
| CU-07-P03 | Eligibility | checklist | Eligibility enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU07ReadModel.panels.03 |
| CU-07-P04 | Reauthorization tasks | information | Reauthorization tasks presents typed facts or approved explanatory content for Platform connections, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU07ReadModel.panels.04 |


---

# CU-08: Connection wizard

**Purpose:** Use the platform’s supported authorization flow and validate returned account identity.

Page: `/app/connections/new`. Service: `commercial`. Read model: `/api/v1/ui/cu-08`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Platform and mode.
2. Eligibility.
3. Hosted authorization.
4. Account verification.
5. Readback/capability check.
6. Save.

## Table and query behavior

Columns in default order: Capability, Evidence, Availability, Reason.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-08-A01 | Begin authorization | F-CONNECTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-08-A02 | Verify connection | F-CONNECTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-08-A03 | Save verified connection | F-CONNECTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No eligible platform connection is configured.

Next permitted route: `CU-07` (Platform connections). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-CONNECTION: Platform connection

Steps: Choose platform → Mode → Eligibility → Hosted consent → Verify account.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| platform | Platform | enum:collective2,etoro,copyfactory,broker_native | yes | unset | configured and service-eligible; No inference of provider program approval |
| environment | Environment | enum:demo,live | yes | demo | live needs distinct approved flow;C2 test strategy is not blanket sandbox; Displayed at every step |
| account_selection | Returned account | id | conditional/optional | unset | only IDs in server-verified provider response; Cannot type arbitrary account ID |
| connection_label | Label | text | yes | unset | 1..80 plain-text characters; No secrets in labels |
| consent_version | Connection consent | id | yes | unset | approved current document ID; Connection is not mandate |

**Save:** Persist connection draft and server-held auth state

**Preview:** Verify account/scopes using allowed read methods

**Confirm:** Save verified connection metadata; tokens remain encrypted server-side

No live financial API calls during connection verification; OAuth/PKCE only where actual platform supports it; unsupported platform method visible.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No eligible platform connection is configured.; next CU-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

State/PKCE/nonce verified where supported; tokens stay server-side; no arbitrary callback URLs or default live mode.

Run every SC-CU-08-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-100.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-08-P01 | Platform and mode | information | Platform and mode presents typed facts or approved explanatory content for Connection wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU08ReadModel.panels.01 |
| CU-08-P02 | Eligibility | checklist | Eligibility enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU08ReadModel.panels.02 |
| CU-08-P03 | Hosted authorization | information | Hosted authorization presents typed facts or approved explanatory content for Connection wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU08ReadModel.panels.03 |
| CU-08-P04 | Account verification | information | Account verification presents typed facts or approved explanatory content for Connection wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU08ReadModel.panels.04 |
| CU-08-P05 | Readback/capability check | information | Readback/capability check presents typed facts or approved explanatory content for Connection wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU08ReadModel.panels.05 |
| CU-08-P06 | Save | information | Save presents typed facts or approved explanatory content for Connection wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU08ReadModel.panels.06 |


---

# CU-09: Copy setup and mandate wizard

**Purpose:** Create an explicit versioned mandate; activation is separate from draft and payment.

Page: `/app/copy/new`. Service: `commercial`. Read model: `/api/v1/ui/cu-09`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Portfolio version.
2. Connection/account.
3. Allocation constraints.
4. New-only versus sync.
5. Preview.
6. Agreements/step-up.
7. Operation status.

## Table and query behavior

Columns in default order: Constraint, Customer choice, Platform/hard limit, Effective result.

Filters: Service mode.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-09-A01 | Save mandate draft | F-MANDATE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-09-A02 | Preview activation | F-MANDATE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-09-A03 | Confirm authorized activation | F-MANDATE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

A verified eligible connection and released portfolio are required.

Next permitted route: `CU-07` (Platform connections). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-MANDATE: Copy mandate

Steps: Select version and account → Constraints → Start mode → Preview → Consent and confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| selection_id | Portfolio selection | id | yes | unset | owned and eligible; Locked in preview |
| connection_id | Connection | id | yes | unset | owned;current verified scope/mode; Account cannot change after preview |
| allocation_amount | Allocation amount | decimal | yes | unset | positive within released min/max and verified capacity; Currency separate, no browser sizing |
| allocation_currency | Currency | currency | yes | unset | matches approved account profile; Cross-currency unsupported ->blocked |
| max_trade_risk | Maximum per-trade risk | decimal | conditional/optional | unset | only values within released account/product ceiling; No guessed default |
| max_loss | Loss limit | decimal | conditional/optional | unset | unit and period from approved profile; required when profile requires |
| start_mode | Start mode | enum:new_entries_only,sync_existing | yes | new_entries_only | sync_existing requires separate fresh price/exposure preview; No historical replay |
| policy_version_id | Policy | id | yes | unset | released and compatible with portfolio/account; No editable raw algorithm |
| consent_version | Mandate document | id | yes | unset | exact current approved version; Payment is not consent |
| acknowledge_scope | Confirm account and scope | boolean | yes | false | required true only at confirmation; Step-up is separate challenge |

**Save:** Persist mandate draft without effect

**Preview:** Read current gates/positions/price and return expiring frozen action preview

**Confirm:** Enqueue approved activation through scoped publisher; return operation not instant success

Rights, entitlement, mandate, environment, platform and risk all rechecked at effect boundary. No actual user signing through design tooling.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | A verified eligible connection and released portfolio are required.; next CU-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Default NEW_ENTRIES_ONLY; existing-position sync needs fresh scoped preview and consent. Activation fails closed if rights/eligibility/price/mandate changes.

Run every SC-CU-09-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-101.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-09-P01 | Portfolio version | information | Portfolio version presents typed facts or approved explanatory content for Copy setup and mandate wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU09ReadModel.panels.01 |
| CU-09-P02 | Connection/account | information | Connection/account presents typed facts or approved explanatory content for Copy setup and mandate wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU09ReadModel.panels.02 |
| CU-09-P03 | Allocation constraints | information | Allocation constraints presents typed facts or approved explanatory content for Copy setup and mandate wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU09ReadModel.panels.03 |
| CU-09-P04 | New-only versus sync | information | New-only versus sync presents typed facts or approved explanatory content for Copy setup and mandate wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU09ReadModel.panels.04 |
| CU-09-P05 | Preview | checklist | Preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU09ReadModel.panels.05 |
| CU-09-P06 | Agreements/step-up | information | Agreements/step-up presents typed facts or approved explanatory content for Copy setup and mandate wizard, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU09ReadModel.panels.06 |
| CU-09-P07 | Operation status | checklist | Operation status enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU09ReadModel.panels.07 |


---

# CU-10: Pause copying and position handoff

**Purpose:** Separate pausing entries, closing an owned cohort and revoking authority.

Page: `/app/copy/{mandate_id}/manage`. Service: `commercial`. Read model: `/api/v1/ui/cu-10`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Current mandate.
2. Owned/open episodes.
3. Available safety operations.
4. Scope preview.
5. Confirmation.
6. Progress.

## Table and query behavior

Columns in default order: Episode, Owned, May still execute, Protection, Allowed disposition.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-10-A01 | Pause new entries | F-WINDDOWN; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-10-A02 | Review owned-position wind-down | F-WINDDOWN; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-10-A03 | Request handoff | F-WINDDOWN; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No active copy mandate is available to manage.

Next permitted route: `CU-07` (Platform connections). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-WINDDOWN: Copy pause, exit and handoff

Steps: Choose safety action → Inspect cohort → Preview obligations → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| mandate_id | Mandate | id | yes | unset | owned and existing; Can be accessible during valid wind-down after billing expiry |
| action | Action | enum:pause_new_entries,drain,close_owned_cohort,request_handoff,revoke | yes | pause_new_entries | operation compatible with mandate and platform; No general account flatten |
| cohort_revision | Owned cohort revision | revision | yes | unset | fresh server-issued immutable cohort; No newly opened/manual positions added |
| reason | Reason | text | yes | unset | 1..500 plain-text characters; Audit only |
| acknowledge_remaining | Acknowledge remaining obligations | boolean | yes | false | required for non-pause actions; No claim closed until confirmed |

**Save:** Persist requested disposition

**Preview:** Compute outstanding obligations and executable quantities

**Confirm:** Enqueue exact approved scope; UNKNOWN stays reconciling

Billing cancellation never implicitly calls this action. Platform revocation may require external handoff; show actual outcome.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No active copy mandate is available to manage.; next CU-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No whole-account flatten, no payment-triggered cancellation of protection. Unknown commands remain visible until reconciled.

Run every SC-CU-10-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-104.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-10-P01 | Current mandate | information | Current mandate presents typed facts or approved explanatory content for Pause copying and position handoff, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU10ReadModel.panels.01 |
| CU-10-P02 | Owned/open episodes | information | Owned/open episodes presents typed facts or approved explanatory content for Pause copying and position handoff, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU10ReadModel.panels.02 |
| CU-10-P03 | Available safety operations | information | Available safety operations presents typed facts or approved explanatory content for Pause copying and position handoff, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU10ReadModel.panels.03 |
| CU-10-P04 | Scope preview | checklist | Scope preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU10ReadModel.panels.04 |
| CU-10-P05 | Confirmation | information | Confirmation presents typed facts or approved explanatory content for Pause copying and position handoff, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU10ReadModel.panels.05 |
| CU-10-P06 | Progress | information | Progress presents typed facts or approved explanatory content for Pause copying and position handoff, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU10ReadModel.panels.06 |


---

# CU-11: Billing, invoices and plan changes

**Purpose:** Manage hosted billing and show canonical entitlement state.

Page: `/app/billing`. Service: `commercial`. Read model: `/api/v1/ui/cu-11`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Current plan.
2. Pending changes.
3. Invoices.
4. Usage entitlement.
5. Platform fee distinction.

## Table and query behavior

Columns in default order: Invoice, Period, Amount/currency, Payment state, Refund, Download.

Filters: Period, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-11-A01 | Start hosted checkout | F-BILLING; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-11-A02 | Manage subscription | F-BILLING; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-11-A03 | Download invoice | ACTION-CU-11-A03; Read short-lived allowlisted hosted invoice URL belonging to current customer; no arbitrary redirect or other customer invoice. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

You have no subscription or invoices.

Next permitted route: `PU-05` (Pricing and service compatibility). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-BILLING: Subscription checkout or change

Steps: Plan and interval → Costs/terms → Hosted provider → Return and verify.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| price_version_id | Price | id | yes | unset | server allowlist;approved for audience and mode; No client-supplied charge amount |
| interval | Interval | enum:month,year | yes | month | matches price version; Actual proration from processor |
| subscription_id | Subscription | id | conditional/optional | unset | owned;required for changes; No other-customer billing ID |
| change_mode | Change | enum:start,upgrade,downgrade,cancel | yes | start | current subscription transition valid; No financial-account effect |
| return_route | Return destination | route_key | yes | billing | server allowlist; No arbitrary external redirect |
| accept_billing_terms | Accept terms | boolean | yes | false | required at change confirmation; No stored card input in app |

**Save:** Store change intent

**Preview:** Show canonical processor quote or clearly unavailable quote

**Confirm:** Create hosted checkout/portal session in approved mode; webhook confirms canonical state

HTTP redirect success does not grant access. Missing merchant approval blocks live session but not local workflow implementation.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | You have no subscription or invoices.; next PU-05 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Checkout return cannot grant entitlement; out-of-order webhooks reconcile canonical state; downgrade preserves open management obligations.

Run every SC-CU-11-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-095, CP-096.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-11-P01 | Current plan | information | Current plan presents typed facts or approved explanatory content for Billing, invoices and plan changes, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU11ReadModel.panels.01 |
| CU-11-P02 | Pending changes | information | Pending changes presents typed facts or approved explanatory content for Billing, invoices and plan changes, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU11ReadModel.panels.02 |
| CU-11-P03 | Invoices | information | Invoices presents typed facts or approved explanatory content for Billing, invoices and plan changes, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU11ReadModel.panels.03 |
| CU-11-P04 | Usage entitlement | information | Usage entitlement presents typed facts or approved explanatory content for Billing, invoices and plan changes, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU11ReadModel.panels.04 |
| CU-11-P05 | Platform fee distinction | information | Platform fee distinction presents typed facts or approved explanatory content for Billing, invoices and plan changes, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU11ReadModel.panels.05 |


---

# CU-12: Alert delivery preferences

**Purpose:** Configure verified channels, timezone and notification categories.

Page: `/app/settings/notifications`. Service: `commercial`. Read model: `/api/v1/ui/cu-12`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Delivery endpoints.
2. Category controls.
3. Quiet hours.
4. Verification/test.
5. Delivery failures.

## Table and query behavior

Columns in default order: Destination, Verification, Category, Last delivery, State.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-12-A01 | Save preferences | F-DELIVERY; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-12-A02 | Verify endpoint | F-DELIVERY; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-12-A03 | Send labeled test | F-DELIVERY; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No verified delivery destination is configured.

Next permitted route: `CU-12` (Alert delivery preferences). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-DELIVERY: Delivery preferences

Steps: Destinations → Categories → Timezone/schedule → Verification.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| email | Email destination | email | conditional/optional | unset | verified identity-owned email or verification workflow; No blind forward to arbitrary recipient |
| webhook_endpoint_id | Webhook destination | id | conditional/optional | unset | owned preverified endpoint;SSRF-safe registry; Raw URL uses separate secure validation |
| categories | Categories | enum_list:entry,update,exit,safety,billing,marketing | yes | unset | known category IDs; Safety follows agreed policy, not marketing toggle |
| timezone | Timezone | timezone | yes | UTC | IANA zone; Schedule uses local wall time with DST rules |
| quiet_start | Quiet hours start | time | conditional/optional | unset | HH:mm plus explicit wrap behavior; Safety exceptions shown |
| quiet_end | Quiet hours end | time | conditional/optional | unset | required with quiet_start; Digest never revives expired entries |
| marketing_consent | Marketing consent | boolean | conditional/optional | false | separate optional consent; No preselected consent |

**Save:** Persist preferences with revision

**Preview:** Show effective channel policy and safety exceptions

**Confirm:** Save preference version; verification/test jobs only when explicitly requested

Test message labeled TEST and cannot be routed to financial engine. Endpoint private networks/redirects not allowed.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No verified delivery destination is configured.; next CU-12 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Safety notices follow agreed policy independently of marketing preferences; tests contain no actionable live trade.

Run every SC-CU-12-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-099.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-12-P01 | Delivery endpoints | table | Delivery endpoints is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU12ReadModel.panels.01 |
| CU-12-P02 | Category controls | information | Category controls presents typed facts or approved explanatory content for Alert delivery preferences, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU12ReadModel.panels.02 |
| CU-12-P03 | Quiet hours | information | Quiet hours presents typed facts or approved explanatory content for Alert delivery preferences, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU12ReadModel.panels.03 |
| CU-12-P04 | Verification/test | information | Verification/test presents typed facts or approved explanatory content for Alert delivery preferences, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU12ReadModel.panels.04 |
| CU-12-P05 | Delivery failures | table | Delivery failures is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU12ReadModel.panels.05 |


---

# CU-13: Profile, security and display preferences

**Purpose:** Manage own profile, sessions, privacy and visual preferences.

Page: `/app/settings`. Service: `commercial`. Read model: `/api/v1/ui/cu-13`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Profile.
2. Sessions/security.
3. Theme/density/timezone.
4. Data export/deletion.
5. Disclosure history.

## Table and query behavior

Columns in default order: Session, Device label, Last activity, Expiry, Revoked.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-13-A01 | Save preferences | F-PREFERENCES; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-13-A02 | Revoke session | ACTION-CU-13-A02; Revoke selected owned session after scoped confirmation; current worker mandate remains independent; invalid session immediately rejected. |
| CU-13-A03 | Request data export | ACTION-CU-13-A03; Create redacted export job from frozen permitted filters and object versions; reauthorize download; no financial action. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No additional profile preferences are saved.

Next permitted route: `CU-01` (Customer overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-PREFERENCES: Profile/security/display

Steps: Profile → Display → Sessions → Privacy.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| display_name | Display name | text | conditional/optional | unset | 0..80 plain-text characters; Not published by default |
| timezone | Timezone | timezone | yes | UTC | IANA zone; UTC retained in stored events |
| theme | Theme | enum:system,dark,light | yes | system | known values only; No custom CSS |
| density | Density | enum:comfortable,compact | yes | comfortable | compact target sizes remain accessible; Never hide safety notices |
| number_locale | Number locale | locale | yes | en-US | supported locale; Stored amounts remain decimal strings |
| view_currency | Preferred currency | currency | conditional/optional | unset | display only;no implicit conversion without data; No account currency change |
| reduce_motion | Reduce motion | enum:system,on | yes | system | never override user reduced-motion preference off; No blinking tickers |

**Save:** Persist profile/display preferences

**Preview:** Show display-only diff

**Confirm:** Commit user preference version

Session revocation, deletion and data export use separate audited action contracts; no arbitrary JSON settings blob.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No additional profile preferences are saved.; next CU-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Layout preferences cannot hide mandatory risk/origin/safety fields; deletion does not erase legally retained execution evidence or orphan mandates.

Run every SC-CU-13-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-093, CP-099, CP-105.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-13-P01 | Profile | information | Profile presents typed facts or approved explanatory content for Profile, security and display preferences, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU13ReadModel.panels.01 |
| CU-13-P02 | Sessions/security | information | Sessions/security presents typed facts or approved explanatory content for Profile, security and display preferences, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU13ReadModel.panels.02 |
| CU-13-P03 | Theme/density/timezone | information | Theme/density/timezone presents typed facts or approved explanatory content for Profile, security and display preferences, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU13ReadModel.panels.03 |
| CU-13-P04 | Data export/deletion | information | Data export/deletion presents typed facts or approved explanatory content for Profile, security and display preferences, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU13ReadModel.panels.04 |
| CU-13-P05 | Disclosure history | table | Disclosure history is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: CU13ReadModel.panels.05 |


---

# CU-14: Support and incident case

**Purpose:** Submit a scoped issue with a redacted diagnostic bundle.

Page: `/app/support`. Service: `commercial`. Read model: `/api/v1/ui/cu-14`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Cases.
2. Case form.
3. Timeline.
4. Attachment scan/status.
5. Service incidents.

## Table and query behavior

Columns in default order: Case, Category, Related object, Severity, Status, Updated.

Filters: State, Category.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-14-A01 | Create case | F-SUPPORT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-14-A02 | Attach redacted evidence | ACTION-CU-14-A02; Create bounded private upload intent. Verify MIME/size, hash, malware/content status and tenant. Never execute attachment content; clean server-generated basename. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

You have no support cases.

Next permitted route: `PU-08` (Help and compatibility guide). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-SUPPORT: Support case

Steps: Category → Affected record → Details → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| category | Category | enum:billing,delivery,connection,performance,safety,access | yes | unset | known category; Safety priority triaged separately |
| related_object_id | Related record | id | conditional/optional | unset | caller can access object; No cross-tenant IDs |
| subject | Subject | text | yes | unset | 1..120 characters; Plain text |
| description | Description | text | yes | unset | 1..8000 characters;inert rendering; Do not enter passwords or API keys |
| attachment_ids | Attachments | id_list | conditional/optional | unset | owned quarantine-scanned objects;max5; max10MiB each;CSV/PNG/JPEG/PDF;no executable HTML |

**Save:** Persist scoped case

**Preview:** Show redaction/attachment scan status

**Confirm:** Create case and approved notification; no financial command

Staff scope least privilege; private execution secrets unavailable; customer may submit before subscribing.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | You have no support cases.; next PU-08 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No secret attachment, cross-tenant record or raw provider material outside rights. Support acknowledgment does not resolve financial incident.

Run every SC-CU-14-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-105.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-14-P01 | Cases | information | Cases presents typed facts or approved explanatory content for Support and incident case, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU14ReadModel.panels.01 |
| CU-14-P02 | Case form | information | Case form presents typed facts or approved explanatory content for Support and incident case, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU14ReadModel.panels.02 |
| CU-14-P03 | Timeline | timeline | Timeline shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: CU14ReadModel.panels.03 |
| CU-14-P04 | Attachment scan/status | checklist | Attachment scan/status enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU14ReadModel.panels.04 |
| CU-14-P05 | Service incidents | information | Service incidents presents typed facts or approved explanatory content for Support and incident case, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU14ReadModel.panels.05 |


---

# CU-15: Managed program investor report

**Purpose:** Show approved broker-native PAMM/MAM status and reporting without SaaS custody.

Page: `/app/managed-programs`. Service: `commercial`. Read model: `/api/v1/ui/cu-15`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Program eligibility.
2. Broker mandate.
3. NAV/units/allocation.
4. Fees/cashflow requests.
5. Documents.

## Table and query behavior

Columns in default order: Program, Broker status, Units or allocation method, NAV date, Fees, Cashflow request state.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-15-A01 | Open broker program | ACTION-CU-15-A01; Return a verified platform/broker allowlisted URL for this connected account; no tokens in page URL or arbitrary redirect. |
| CU-15-A02 | Review statement | local_statement; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| CU-15-A03 | Request permitted dealing action | F-MANAGED-REQUEST; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No approved managed-account program is available to you.

Next permitted route: `PU-08` (Help and compatibility guide). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-MANAGED-REQUEST: Managed-account dealing request

Steps: Program → Broker convention → Amount → Cutoff → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| program_id | Program | id | yes | unset | owned participant in approved broker-native program; No local pooled account |
| request_type | Request | enum:broker_onboarding,subscription,redemption,statement | yes | unset | allowed by program; Requests are not transfers |
| amount | Amount | decimal | conditional/optional | unset | required positive for cashflow requests;program bound; No dollar defaults |
| currency | Currency | currency | conditional/optional | unset | required with amount; Matches program |
| cutoff_id | Dealing cutoff | id | conditional/optional | unset | server-issued;still open; Late requests roll by approved rule |
| mandate_version | Mandate | id | yes | unset | current approved participant mandate; Explicit request consent |

**Save:** Persist request draft

**Preview:** Show broker schedule, fees, restrictions and open-exposure effect

**Confirm:** Submit only through approved broker program flow; otherwise record awaiting owner action

No Stripe cash deposits, invented NAV or fee arithmetic. Missing convention blocks dealing.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No approved managed-account program is available to you.; next PU-08 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Not-eligible/program-unconfigured states remain functional. Cash moves through broker-approved flow; no investment deposits through Stripe.

Run every SC-CU-15-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-113.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-15-P01 | Program eligibility | checklist | Program eligibility enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: CU15ReadModel.panels.01 |
| CU-15-P02 | Broker mandate | information | Broker mandate presents typed facts or approved explanatory content for Managed program investor report, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU15ReadModel.panels.02 |
| CU-15-P03 | NAV/units/allocation | information | NAV/units/allocation presents typed facts or approved explanatory content for Managed program investor report, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU15ReadModel.panels.03 |
| CU-15-P04 | Fees/cashflow requests | information | Fees/cashflow requests presents typed facts or approved explanatory content for Managed program investor report, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU15ReadModel.panels.04 |
| CU-15-P05 | Documents | information | Documents presents typed facts or approved explanatory content for Managed program investor report, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU15ReadModel.panels.05 |


---

# CU-16: API delivery, keys and exports

**Purpose:** Manage scoped read/delivery API access only where entitled and licensed.

Page: `/app/developer`. Service: `commercial`. Read model: `/api/v1/ui/cu-16`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. API scopes.
2. Credential metadata.
3. Webhook destinations.
4. Usage/quotas.
5. Export jobs.

## Table and query behavior

Columns in default order: Key label, Scope, Created, Last used, Expires, Revoked.

Filters: State, Scope.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| CU-16-A01 | Create scoped key | F-API-ACCESS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-16-A02 | Revoke key | F-API-ACCESS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| CU-16-A03 | Request scoped export | F-API-ACCESS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No API access is active for this subscription.

Next permitted route: `CU-11` (Billing, invoices and plan changes). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-API-ACCESS: Scoped API delivery access

Steps: Scope → Expiry → Destination → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| label | Key label | text | yes | unset | 1..80 plain text; Not a secret |
| scopes | Scopes | enum_list:alerts_read,reports_read,delivery_receive | yes | unset | subset of entitlement and rights; No trading/admin scope |
| expires_at | Expiry | datetime | yes | unset | future within server maximum; No never-expire by default |
| destination_id | Delivery endpoint | id | conditional/optional | unset | owned validated endpoint; No arbitrary outbound URL |

**Save:** Persist requested access metadata

**Preview:** Show allowed subset/quota and expiry

**Confirm:** Generate scoped secret once only; store hash/server secret as appropriate

Key reveal not in audit/export/cache. Revocation idempotent and affects reads/delivery only.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No API access is active for this subscription.; next CU-11 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No generic trading endpoint, SQL tool or arbitrary URL fetch. Generated key shown once only; browser/session exports never include provider secrets.

Run every SC-CU-16-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-099, CP-105.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| CU-16-P01 | API scopes | information | API scopes presents typed facts or approved explanatory content for API delivery, keys and exports, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU16ReadModel.panels.01 |
| CU-16-P02 | Credential metadata | information | Credential metadata presents typed facts or approved explanatory content for API delivery, keys and exports, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU16ReadModel.panels.02 |
| CU-16-P03 | Webhook destinations | information | Webhook destinations presents typed facts or approved explanatory content for API delivery, keys and exports, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU16ReadModel.panels.03 |
| CU-16-P04 | Usage/quotas | information | Usage/quotas presents typed facts or approved explanatory content for API delivery, keys and exports, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU16ReadModel.panels.04 |
| CU-16-P05 | Export jobs | information | Export jobs presents typed facts or approved explanatory content for API delivery, keys and exports, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: CU16ReadModel.panels.05 |


---

# ID-01: Sign in and create account

**Purpose:** Create or access a commercial identity without granting subscription or trade permissions.

Page: `/auth`. Service: `commercial`. Read model: `/api/v1/ui/id-01`. Access: anonymous, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Sign-in/signup tabs.
2. Email/password fields.
3. Recovery link.
4. Terms and privacy links.

## Table and query behavior

No primary data table. Structured facts use labeled definition lists rather than empty tables.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| ID-01-A01 | Sign in | F-IDENTITY; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| ID-01-A02 | Create account | F-IDENTITY; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| ID-01-A03 | Recover account | ID-03; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

Authentication is not configured for this deployment.

Next permitted route: `PU-08` (Help and compatibility guide). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-IDENTITY: Commercial sign-in / sign-up

Steps: Choose sign in or sign up → Enter credentials → Terms for signup → Submit.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| email | Email | email | yes | unset | valid email;max254;normalize domain not provider-specific aliases; Used by the configured identity provider |
| password | Password | password | yes | unset | configured identity-provider policy;max1024 bytes;allow Unicode and password managers; Never log or persist in app state |
| terms_version | Terms version | id | conditional/optional | unset | server-supplied approved document ID;required on signup; Version accepted is stored as evidence |
| accept_terms | Accept terms | boolean | conditional/optional | false | required true for signup only; Does not authorize trading |
| return_route | Return destination | route_key | conditional/optional | customer_overview | server route-key allowlist only; No external redirect accepted |

**Save:** Create/verify identity through existing provider; create tenant/membership only after validated identity

**Preview:** No financial preview

**Confirm:** Create session or verification-pending state; never select/pay/copy

No email enumeration. Bound attempts by trusted client and identity; signup uses bot defenses only when implemented accessibly.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | Authentication is not configured for this deployment.; next PU-08 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Inputs are bounded and labeled; successful signup starts verification, not payment or copying. Unknown email failures do not enumerate accounts.

Run every SC-ID-01-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-093.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| ID-01-P01 | Sign-in/signup tabs | information | Sign-in/signup tabs presents typed facts or approved explanatory content for Sign in and create account, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID01ReadModel.panels.01 |
| ID-01-P02 | Email/password fields | form | Email/password fields uses the fields of F-IDENTITY. Group by the specified steps; show inherited/effective values and field validation. Persist only scoped explicit drafts. Query: ID01ReadModel.panels.02 |
| ID-01-P03 | Recovery link | information | Recovery link presents typed facts or approved explanatory content for Sign in and create account, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID01ReadModel.panels.03 |
| ID-01-P04 | Terms and privacy links | information | Terms and privacy links presents typed facts or approved explanatory content for Sign in and create account, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID01ReadModel.panels.04 |


---

# ID-02: Email verification and auth callback

**Purpose:** Consume an intended auth callback once and show its actual identity/session result.

Page: `/auth/verify`. Service: `commercial`. Read model: `/api/v1/ui/id-02`. Access: anonymous, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Verification state.
2. Account confirmation.
3. Next step.
4. Resend controls.

## Table and query behavior

No primary data table. Structured facts use labeled definition lists rather than empty tables.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| ID-02-A01 | Resend verification | ACTION-ID-02-A01; Request one bounded provider verification resend. Use generic response and anti-enumeration; no local email fabrication. |
| ID-02-A02 | Continue to eligibility | ID-04; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

This verification link is missing, expired or already consumed.

Next permitted route: `ID-01` (Sign in and create account). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | This verification link is missing, expired or already consumed.; next ID-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Auth code and state are verified, redirects allowlisted; callback replays cannot select another tenant.

Run every SC-ID-02-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-093.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| ID-02-P01 | Verification state | information | Verification state presents typed facts or approved explanatory content for Email verification and auth callback, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID02ReadModel.panels.01 |
| ID-02-P02 | Account confirmation | information | Account confirmation presents typed facts or approved explanatory content for Email verification and auth callback, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID02ReadModel.panels.02 |
| ID-02-P03 | Next step | checklist | Next step enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: ID02ReadModel.panels.03 |
| ID-02-P04 | Resend controls | information | Resend controls presents typed facts or approved explanatory content for Email verification and auth callback, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID02ReadModel.panels.04 |


---

# ID-03: Recovery, MFA and session verification

**Purpose:** Recover an identity or satisfy a scoped step-up without weakening sessions or mandates.

Page: `/auth/recovery`. Service: `commercial`. Read model: `/api/v1/ui/id-03`. Access: anonymous, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Recovery options.
2. Verification challenge.
3. New credential.
4. Active-session decision.

## Table and query behavior

No primary data table. Structured facts use labeled definition lists rather than empty tables.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| ID-03-A01 | Verify identity | F-RECOVERY; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| ID-03-A02 | Return to sign in | ID-01; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No valid recovery or step-up request is active.

Next permitted route: `ID-01` (Sign in and create account). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-RECOVERY: Credential recovery / MFA

Steps: Validate challenge → Verify factor → Set new credential → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| challenge_id | Challenge | challenge | yes | unset | short-lived;origin/audience/action bound;single use; Issued server-side |
| verification_code | Verification code | challenge | conditional/optional | unset | provider-specific length;paste allowed; Use provider challenge verifier |
| new_password | New password | password | conditional/optional | unset | provider policy;max1024 bytes;only password-reset mode; Do not echo value |
| revoke_other_sessions | Revoke other browser sessions | boolean | conditional/optional | true | actual boolean; Does not stop worker management |

**Save:** No raw challenge saved to drafts

**Preview:** Show session consequences only

**Confirm:** Provider-verified credential change and session epoch update

Recovery cannot change mandate scope; expired step-up returns to requesting action without automatic replay.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No valid recovery or step-up request is active.; next ID-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Password-manager paste allowed; current step-up scope, expiry and origin verified; old recovery tokens cannot authorize financial commands.

Run every SC-ID-03-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-093.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| ID-03-P01 | Recovery options | information | Recovery options presents typed facts or approved explanatory content for Recovery, MFA and session verification, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID03ReadModel.panels.01 |
| ID-03-P02 | Verification challenge | form | Verification challenge uses the fields of F-RECOVERY. Group by the specified steps; show inherited/effective values and field validation. Persist only scoped explicit drafts. Query: ID03ReadModel.panels.02 |
| ID-03-P03 | New credential | form | New credential uses the fields of F-RECOVERY. Group by the specified steps; show inherited/effective values and field validation. Persist only scoped explicit drafts. Query: ID03ReadModel.panels.03 |
| ID-03-P04 | Active-session decision | checklist | Active-session decision enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: ID03ReadModel.panels.04 |


---

# ID-04: Service eligibility onboarding

**Purpose:** Determine permitted product/channel access from approved jurisdiction rules.

Page: `/onboarding/eligibility`. Service: `commercial`. Read model: `/api/v1/ui/id-04`. Access: customer, with principal-derived tenant and explicit membership/object scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Residence/service facts.
2. Policy evaluation.
3. Consent versions.
4. Decision and next steps.

## Table and query behavior

Columns in default order: Service, Channel, Eligibility, Reason, Required document.

Filters: Requested service.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| ID-04-A01 | Save facts | F-ELIGIBILITY; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| ID-04-A02 | Review decision | local_eligibility_result; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

Complete eligibility before selecting a paid or copy service.

Next permitted route: `CU-01` (Customer overview). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-ELIGIBILITY: Residence and service eligibility

Steps: Identity facts → Requested service → Documents → Decision.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| residence_country | Country of residence | country | yes | unset | ISO country supported by approved policy; User fact, not approval |
| tax_residence | Tax residence | country_list | conditional/optional | unset | unique ISO countries;policy decides requirement; Not inferred from IP |
| customer_type | Customer type | enum:individual,entity | yes | individual | allowed by service approval; Entities need their actual approved onboarding flow |
| service_modes | Requested services | enum_list:research,alerts,copying,managed_program | yes | unset | nonempty;published compatible services only; Payment and mandate gates remain separate |
| document_versions | Acknowledged documents | id_list | yes | unset | exact server-published versions; Document acceptance timestamp persisted |
| facts_confirmed | I confirm these facts | boolean | yes | false | required true; No eligibility flags accepted from browser |

**Save:** Persist submitted facts and consent versions

**Preview:** Evaluate approved eligibility policy without charging or creating mandate

**Confirm:** Record fact submission; server result eligible/pending/unsupported with reason

Residence change invalidates affected service eligibility and triggers review; does not silently orphan open obligations.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | Complete eligibility before selecting a paid or copy service.; next CU-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Customer supplies facts, not approval flags. IP location alone does not grant eligibility. Pending review is not declined or approved.

Run every SC-ID-04-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-094.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| ID-04-P01 | Residence/service facts | form | Residence/service facts uses the fields of F-ELIGIBILITY. Group by the specified steps; show inherited/effective values and field validation. Persist only scoped explicit drafts. Query: ID04ReadModel.panels.01 |
| ID-04-P02 | Policy evaluation | information | Policy evaluation presents typed facts or approved explanatory content for Service eligibility onboarding, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID04ReadModel.panels.02 |
| ID-04-P03 | Consent versions | information | Consent versions presents typed facts or approved explanatory content for Service eligibility onboarding, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: ID04ReadModel.panels.03 |
| ID-04-P04 | Decision and next steps | checklist | Decision and next steps enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: ID04ReadModel.panels.04 |


---

# PU-01: Public home

**Purpose:** Explain the service and direct visitors to approved products without manufactured performance.

Page: `/`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-01`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Service description.
2. Available products.
3. How alerts and copying differ.
4. Risk and fee notice.
5. Service status.

## Table and query behavior

Columns in default order: Product, Asset scope, Holding horizon, Performance origin, Availability.

Filters: Asset class, Service mode.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-01-A01 | Browse portfolios | PU-02; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| PU-01-A02 | Read methodology | PU-06; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No portfolios are currently available.

Next permitted route: `PU-02` (Portfolio catalog). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No portfolios are currently available.; next PU-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Empty production database renders truthful explanatory content; no fake cards, returns, customer counts or checkout links.

Run every SC-PU-01-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-091.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-01-P01 | Service description | information | Service description presents typed facts or approved explanatory content for Public home, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU01ReadModel.panels.01 |
| PU-01-P02 | Available products | information | Available products presents typed facts or approved explanatory content for Public home, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU01ReadModel.panels.02 |
| PU-01-P03 | How alerts and copying differ | information | How alerts and copying differ presents typed facts or approved explanatory content for Public home, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU01ReadModel.panels.03 |
| PU-01-P04 | Risk and fee notice | information | Risk and fee notice presents typed facts or approved explanatory content for Public home, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU01ReadModel.panels.04 |
| PU-01-P05 | Service status | checklist | Service status enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: PU01ReadModel.panels.05 |


---

# PU-02: Portfolio catalog

**Purpose:** Find eligible published portfolio products; no ranking by invented or incomplete profit.

Page: `/portfolios`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-02`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Search and filters.
2. Portfolio cards/table.
3. Metric definitions.
4. Pagination.

## Table and query behavior

Columns in default order: Portfolio, Version, Assets, Strategy horizon, Net return, Maximum drawdown, History window, Origin, Service modes, Price basis.

Filters: Asset class, Holding horizon, Channel, Origin, Availability.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-02-A01 | Open portfolio | PU-03; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| PU-02-A02 | Compare selected | PU-04; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-PU-02-01 | Published product count | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No portfolios have been released for this audience.

Next permitted route: `PU-06` (Methodology, risk and legal documents). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No portfolios have been released for this audience.; next PU-06 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Only approved version projections are returned. Empty is distinct from fetch failure or jurisdiction restriction.

Run every SC-PU-02-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-091.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-02-P01 | Search and filters | information | Search and filters presents typed facts or approved explanatory content for Portfolio catalog, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU02ReadModel.panels.01 |
| PU-02-P02 | Portfolio cards/table | table | Portfolio cards/table is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: PU02ReadModel.panels.02 |
| PU-02-P03 | Metric definitions | information | Metric definitions presents typed facts or approved explanatory content for Portfolio catalog, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU02ReadModel.panels.03 |
| PU-02-P04 | Pagination | information | Pagination presents typed facts or approved explanatory content for Portfolio catalog, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU02ReadModel.panels.04 |


---

# PU-03: Portfolio detail

**Purpose:** Explain one released version, its record, risks, fees and supported implementation paths.

Page: `/portfolios/{slug}`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-03`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Identity and version.
2. Performance and drawdown charts.
3. Methodology and change history.
4. Risk/capacity/fees.
5. Compatibility.
6. Enrollment action.

## Table and query behavior

Columns in default order: Date, Net return, Gross return if allowed, Drawdown, Origin, Quality, Revision.

Filters: Date range, Origin, Base currency, Comparison benchmark.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-03-A01 | Check eligibility | ID-04; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| PU-03-A02 | View disclosures | PU-06; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-PU-03-01 | Net return | ratio | (ending_unit_NAV / starting_unit_NAV) - 1, or chain-linked subperiod return after external cashflow boundaries; exact method in definition version. No valid starting NAV, comparable period or sufficient marks => unavailable; do not use realized P&L / deposits. |
| M-PU-03-02 | Maximum drawdown | ratio | max over chronological normalized equity of (running_peak-equity)/running_peak. Requires positive comparable equity and full stated history; use full-resolution data before chart decimation; unknown gaps qualify coverage. |
| M-PU-03-03 | Completed episodes | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-PU-03-04 | History length | seconds | Difference between identified server times with timezone/clock semantics and start/end evidence. Missing either end => ongoing or unavailable, not0. |


## Empty state

A released track record is not available for this version.

Next permitted route: `PU-06` (Methodology, risk and legal documents). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | A released track record is not available for this version.; next PU-06 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No guessed zero curve. Every chart shares the approved period, fee basis and origin; draft/retired restricted slugs return scoped not-found.

Run every SC-PU-03-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-092.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-03-P01 | Identity and version | context | Identity and version binds the exact selected object, revision, environment, audience and data origin for Portfolio detail. Context changes clear old scoped responses. Query: PU03ReadModel.panels.01 |
| PU-03-P02 | Performance and drawdown charts | chart | Performance and drawdown charts uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: PU03ReadModel.panels.02 |
| PU-03-P03 | Methodology and change history | timeline | Methodology and change history shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: PU03ReadModel.panels.03 |
| PU-03-P04 | Risk/capacity/fees | information | Risk/capacity/fees presents typed facts or approved explanatory content for Portfolio detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU03ReadModel.panels.04 |
| PU-03-P05 | Compatibility | checklist | Compatibility enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: PU03ReadModel.panels.05 |
| PU-03-P06 | Enrollment action | information | Enrollment action presents typed facts or approved explanatory content for Portfolio detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU03ReadModel.panels.06 |


---

# PU-04: Portfolio comparison

**Purpose:** Compare up to four approved portfolios over a disclosed common period.

Page: `/compare`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-04`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Selection tray.
2. Comparable metric matrix.
3. Normalized return chart.
4. Co-drawdown and exposure.
5. Missing-data explanation.

## Table and query behavior

Columns in default order: Metric, Portfolio A, Portfolio B, Portfolio C, Portfolio D, Definition.

Filters: Portfolio versions, Common period, Origin.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-04-A01 | Add portfolio | PU-02; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| PU-04-A02 | Remove selection | local_remove_compare; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-PU-04-01 | Common history coverage | ratio | Available eligible observations / specified expected observations or explicitly defined overlap duration. Denominator and gap policy recorded; missing bars not automatically zero return. |


## Empty state

Select at least two published portfolios to compare.

Next permitted route: `PU-02` (Portfolio catalog). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | Select at least two published portfolios to compare.; next PU-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No mixing actual and hypothetical series in an unlabeled ranking. Incomparable periods block ranking but preserve separate labeled records.

Run every SC-PU-04-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-091, CP-092.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-04-P01 | Selection tray | information | Selection tray presents typed facts or approved explanatory content for Portfolio comparison, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU04ReadModel.panels.01 |
| PU-04-P02 | Comparable metric matrix | information | Comparable metric matrix presents typed facts or approved explanatory content for Portfolio comparison, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU04ReadModel.panels.02 |
| PU-04-P03 | Normalized return chart | chart | Normalized return chart uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: PU04ReadModel.panels.03 |
| PU-04-P04 | Co-drawdown and exposure | chart | Co-drawdown and exposure uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: PU04ReadModel.panels.04 |
| PU-04-P05 | Missing-data explanation | information | Missing-data explanation presents typed facts or approved explanatory content for Portfolio comparison, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU04ReadModel.panels.05 |


---

# PU-05: Pricing and service compatibility

**Purpose:** Explain approved subscription prices and separate external-platform fees and mandates.

Page: `/pricing`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-05`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Plan cards.
2. Feature comparison.
3. External platform fee notice.
4. Eligibility and refunds.
5. FAQ.

## Table and query behavior

Columns in default order: Plan, Interval, Currency, Price, Included portfolios, Delivery channels, External fees, Availability.

Filters: Billing interval, Display currency.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-05-A01 | Check eligibility | ID-04; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| PU-05-A02 | Read billing terms | PU-06; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

Subscriptions are not open for purchase yet.

Next permitted route: `PU-06` (Methodology, risk and legal documents). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | Subscriptions are not open for purchase yet.; next PU-06 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Unapproved price drafts are absent from public responses. Free research must not imply trading authorization.

Run every SC-PU-05-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-095, CP-096.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-05-P01 | Plan cards | information | Plan cards presents typed facts or approved explanatory content for Pricing and service compatibility, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU05ReadModel.panels.01 |
| PU-05-P02 | Feature comparison | information | Feature comparison presents typed facts or approved explanatory content for Pricing and service compatibility, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU05ReadModel.panels.02 |
| PU-05-P03 | External platform fee notice | information | External platform fee notice presents typed facts or approved explanatory content for Pricing and service compatibility, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU05ReadModel.panels.03 |
| PU-05-P04 | Eligibility and refunds | checklist | Eligibility and refunds enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: PU05ReadModel.panels.04 |
| PU-05-P05 | FAQ | information | FAQ presents typed facts or approved explanatory content for Pricing and service compatibility, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU05ReadModel.panels.05 |


---

# PU-06: Methodology, risk and legal documents

**Purpose:** Publish approved explanations and immutable legal-document versions.

Page: `/methodology`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-06`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Document navigation.
2. Methodology.
3. Performance definitions.
4. Risks/costs.
5. Document version and date.

## Table and query behavior

Columns in default order: Document, Version, Effective date, Audience, Download.

Filters: Document type, Language.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-06-A01 | Read document | local_document; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| PU-06-A02 | Download permitted document | local_download; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No approved document is available for this selection.

Next permitted route: `PU-08` (Help and compatibility guide). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No approved document is available for this selection.; next PU-08 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No analyst raw content, source contract or unreviewed AI prose is served; old signed versions remain retrievable to authorized signatories.

Run every SC-PU-06-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-092, CP-094.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-06-P01 | Document navigation | information | Document navigation presents typed facts or approved explanatory content for Methodology, risk and legal documents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU06ReadModel.panels.01 |
| PU-06-P02 | Methodology | information | Methodology presents typed facts or approved explanatory content for Methodology, risk and legal documents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU06ReadModel.panels.02 |
| PU-06-P03 | Performance definitions | information | Performance definitions presents typed facts or approved explanatory content for Methodology, risk and legal documents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU06ReadModel.panels.03 |
| PU-06-P04 | Risks/costs | information | Risks/costs presents typed facts or approved explanatory content for Methodology, risk and legal documents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU06ReadModel.panels.04 |
| PU-06-P05 | Document version and date | information | Document version and date presents typed facts or approved explanatory content for Methodology, risk and legal documents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU06ReadModel.panels.05 |


---

# PU-07: Public service status

**Purpose:** Report approved service incidents without exposing account or infrastructure secrets.

Page: `/status`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-07`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Public availability summary.
2. Active incidents.
3. Maintenance.
4. History.

## Table and query behavior

Columns in default order: Incident, Affected public service, Started, Status, Last update.

Filters: Date range, Service.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-07-A01 | Open incident | local_public_incident; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| PU-07-A02 | Open support | CU-14; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No published service incidents.

Next permitted route: `PU-08` (Help and compatibility guide). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No published service incidents.; next PU-08 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Public reachability is not labeled brokerage readiness or proof that customer stops exist.

Run every SC-PU-07-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-105, CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-07-P01 | Public availability summary | information | Public availability summary presents typed facts or approved explanatory content for Public service status, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU07ReadModel.panels.01 |
| PU-07-P02 | Active incidents | timeline | Active incidents shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: PU07ReadModel.panels.02 |
| PU-07-P03 | Maintenance | timeline | Maintenance shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: PU07ReadModel.panels.03 |
| PU-07-P04 | History | table | History is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: PU07ReadModel.panels.04 |


---

# PU-08: Help and compatibility guide

**Purpose:** Explain getting started, supported channels and how to request assistance.

Page: `/help`. Service: `commercial`. Read model: `/api/v1/public/ui/pu-08`. Access: anonymous, customer, with approved audience projection.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Task-based help.
2. Compatibility directory.
3. Support entry.
4. Security guidance.

## Table and query behavior

Columns in default order: Topic, Channel, Supported service, Limitation, Updated.

Filters: Topic, Platform.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| PU-08-A01 | Read article | local_help_article; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| PU-08-A02 | Contact support | CU-14; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No help article matches these filters.

Next permitted route: `PU-06` (Methodology, risk and legal documents). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No help article matches these filters.; next PU-06 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Only verified capabilities appear as available; no promises of universal broker coverage.

Run every SC-PU-08-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-100, CP-105.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| PU-08-P01 | Task-based help | information | Task-based help presents typed facts or approved explanatory content for Help and compatibility guide, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU08ReadModel.panels.01 |
| PU-08-P02 | Compatibility directory | checklist | Compatibility directory enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: PU08ReadModel.panels.02 |
| PU-08-P03 | Support entry | information | Support entry presents typed facts or approved explanatory content for Help and compatibility guide, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU08ReadModel.panels.03 |
| PU-08-P04 | Security guidance | information | Security guidance presents typed facts or approved explanatory content for Help and compatibility guide, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: PU08ReadModel.panels.04 |


---

# TR-01: Trading command center

**Purpose:** Monitor actual own-account exposure, protection and unresolved commands.

Page: `/trade`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-01`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Identity/environment.
2. Safety summary.
3. Account risk cards.
4. P&L and exposure.
5. Priority incidents.
6. Recent activity.

## Table and query behavior

Columns in default order: Account, Instrument, Owned, Committed closes, Covered, Deficit, Observation age.

Filters: Account, Asset, Source, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-01-A01 | Open account | TR-07; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| TR-01-A02 | Open incident | TR-13; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-TR-01-01 | Verified net liquidation | money | Account-provider net liquidation observation at the displayed as-of and base currency. Do not sum incompatible account currencies or substitute realized P&L. |
| M-TR-01-02 | Reserved risk | money_or_risk_units | Sum committed released per-trade risk and outstanding reservations under stated product convention. Stop loss is not guaranteed max loss; stress/contractual risk shown separately; unknown commitments retain budget. |
| M-TR-01-03 | Protection deficit | quantity_and_risk | Actual owned exposure without required verified native coverage, classified by transfer/incident and nonoverlapping commitments. Do not aggregate unlike share/contract/base units into one unlabeled scalar; show count and per-allocation breakdown. |
| M-TR-01-04 | Unknown operations | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No brokerage account is configured for this workspace.

Next permitted route: `TR-07` (Broker accounts and capabilities). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No brokerage account is configured for this workspace.; next TR-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Account values have timestamps and basis. Empty does not mean flat when readback is unavailable. No chart or toggle sends a trade.

Run every SC-TR-01-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-103, CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-01-P01 | Identity/environment | information | Identity/environment presents typed facts or approved explanatory content for Trading command center, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR01ReadModel.panels.01 |
| TR-01-P02 | Safety summary | information | Safety summary presents typed facts or approved explanatory content for Trading command center, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR01ReadModel.panels.02 |
| TR-01-P03 | Account risk cards | information | Account risk cards presents typed facts or approved explanatory content for Trading command center, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR01ReadModel.panels.03 |
| TR-01-P04 | P&L and exposure | information | P&L and exposure presents typed facts or approved explanatory content for Trading command center, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR01ReadModel.panels.04 |
| TR-01-P05 | Priority incidents | information | Priority incidents presents typed facts or approved explanatory content for Trading command center, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR01ReadModel.panels.05 |
| TR-01-P06 | Recent activity | information | Recent activity presents typed facts or approved explanatory content for Trading command center, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR01ReadModel.panels.06 |


---

# TR-02: Positions and allocations

**Purpose:** Inspect owned inventory by exact instrument and analyst allocation.

Page: `/trade/positions`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-02`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Scope controls.
2. Position grid.
3. Exposure summary.
4. Saved views.

## Table and query behavior

Columns in default order: Account, Instrument ID, Analyst, Side, Owned, Pending entry, Possible closes, Working stop, P&L basis, State.

Filters: Account, Analyst, Instrument, Product, Protection.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-02-A01 | Open position | TR-03; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| TR-02-A02 | Preview owned-cohort action | TR-03; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-TR-02-01 | Known position count | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-TR-02-02 | Unresolved positions | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No verified positions in this scope.

Next permitted route: `TR-07` (Broker accounts and capabilities). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No verified positions in this scope.; next TR-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Manual and copier allocations remain separate. Selection scope is frozen before bulk actions. Zero quantity does not hide unresolved order families.

Run every SC-TR-02-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-103, CP-104.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-02-P01 | Scope controls | information | Scope controls presents typed facts or approved explanatory content for Positions and allocations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR02ReadModel.panels.01 |
| TR-02-P02 | Position grid | information | Position grid presents typed facts or approved explanatory content for Positions and allocations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR02ReadModel.panels.02 |
| TR-02-P03 | Exposure summary | information | Exposure summary presents typed facts or approved explanatory content for Positions and allocations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR02ReadModel.panels.03 |
| TR-02-P04 | Saved views | information | Saved views presents typed facts or approved explanatory content for Positions and allocations, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR02ReadModel.panels.04 |


---

# TR-03: Position and protection detail

**Purpose:** Manage one allocation through the existing writer with full order-family visibility.

Page: `/trade/positions/{allocation_id}`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-03`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Identity and plan.
2. Quantity ledger.
3. Price/stop timeline.
4. Orders and fills.
5. Protection transfer.
6. Controls.

## Table and query behavior

Columns in default order: Family/order, Purpose, Requested, Cumulative filled, Remaining possible, Status, Coverage evidence.

Filters: Event type, Order family.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-03-A01 | Preview partial reduction | F-POSITION-ACTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-03-A02 | Preview stop change | F-POSITION-ACTION; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-03-A03 | Request reconciliation | ACTION-TR-03-A03; Enqueue permitted authoritative readback. It may record observed facts but does not independently create or retry an order. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-TR-03-01 | Owned | instrument_quantity | Authoritative execution/position/order-family ledger for selected allocation. Never derive ownership from requested order quantity; null/unknown distinct from known zero. |
| M-TR-03-02 | Native covered | instrument_quantity | Authoritative execution/position/order-family ledger for selected allocation. Never derive ownership from requested order quantity; null/unknown distinct from known zero. |
| M-TR-03-03 | Uncovered | instrument_quantity | Authoritative execution/position/order-family ledger for selected allocation. Never derive ownership from requested order quantity; null/unknown distinct from known zero. |
| M-TR-03-04 | Still executable closes | instrument_quantity | Authoritative execution/position/order-family ledger for selected allocation. Never derive ownership from requested order quantity; null/unknown distinct from known zero. |


## Empty state

No verified allocation is available for this reference.

Next permitted route: `TR-02` (Positions and allocations). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-POSITION-ACTION: Position action

Steps: Select owned allocation → Operation → Quantity/floor → Preview → Confirm.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| allocation_id | Allocation | id | yes | unset | owned and exact instrument/environment; Not symbol-only |
| operation | Action | enum:reduce,close_owned,raise_protection,reconcile | yes | unset | supported exact route recipe; Risk reduction still quantity checked |
| quantity | Quantity | decimal | conditional/optional | unset | required for reduce;legal step;<=available after possible fills; No percentage without original/remaining basis |
| new_floor | New protective threshold | decimal | conditional/optional | unset | required for raise_protection;must not loosen activated policy; Price instrument/basis fixed by plan |
| reason | Reason | text | yes | unset | 1..500 plain text; Audit |

**Save:** Save action draft, not reservation

**Preview:** Read complete positions/order families and return expiring scoped preview

**Confirm:** Durably claim and dispatch through sole writer only after revalidation

Unknown same action cannot resubmit. Stop and TP orders may both execute without actual venue cap; constraints use maximum possible close quantity.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No verified allocation is available for this reference.; next TR-02 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Preview->confirm never bypasses close arbiter. 54 owned with47 stop and7 pending TP cannot gain54 stop. Protection changes revalidate current revision.

Run every SC-TR-03-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-103, CP-104.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-03-P01 | Identity and plan | information | Identity and plan presents typed facts or approved explanatory content for Position and protection detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR03ReadModel.panels.01 |
| TR-03-P02 | Quantity ledger | information | Quantity ledger presents typed facts or approved explanatory content for Position and protection detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR03ReadModel.panels.02 |
| TR-03-P03 | Price/stop timeline | timeline | Price/stop timeline shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: TR03ReadModel.panels.03 |
| TR-03-P04 | Orders and fills | information | Orders and fills presents typed facts or approved explanatory content for Position and protection detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR03ReadModel.panels.04 |
| TR-03-P05 | Protection transfer | information | Protection transfer presents typed facts or approved explanatory content for Position and protection detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR03ReadModel.panels.05 |
| TR-03-P06 | Controls | information | Controls presents typed facts or approved explanatory content for Position and protection detail, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR03ReadModel.panels.06 |


---

# TR-04: Incoming signal stream

**Purpose:** Classify every authorized incoming event including rejected or ignored instructions.

Page: `/trade/signals`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-04`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Source filters.
2. Stream status.
3. Disposition table.
4. Backlog.

## Table and query behavior

Columns in default order: Event/revision, Provider, Analyst, Observed, Interpreted action, Instrument, Disposition, Reason.

Filters: Source, Analyst, Disposition, Period.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-04-A01 | Inspect event | TR-05; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| TR-04-A02 | Open source | TR-09; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No authorized signals have been received.

Next permitted route: `TR-09` (Signal providers and collectors). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No authorized signals have been received.; next TR-09 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Ignored negation and stale entries remain searchable; historical imports cannot enter live queue; manual preview is read-only.

Run every SC-TR-04-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-098, CP-106.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-04-P01 | Source filters | information | Source filters presents typed facts or approved explanatory content for Incoming signal stream, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR04ReadModel.panels.01 |
| TR-04-P02 | Stream status | checklist | Stream status enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR04ReadModel.panels.02 |
| TR-04-P03 | Disposition table | table | Disposition table is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR04ReadModel.panels.03 |
| TR-04-P04 | Backlog | information | Backlog presents typed facts or approved explanatory content for Incoming signal stream, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR04ReadModel.panels.04 |


---

# TR-05: Signal evidence and plan preview

**Purpose:** Show exact source revision, field evidence and complete resolved trade plan.

Page: `/trade/signals/{event_id}`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-05`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Original/revisions.
2. Parsed fields/evidence.
3. Instrument resolution.
4. Risk/stop/horizon plan.
5. Routing preview.
6. Execution links.

## Table and query behavior

Columns in default order: Field, Source span, Parser version, Normalized value, Validation, Fallback provenance.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-05-A01 | Reclassify in sandbox | ACTION-TR-05-A01; Evaluate the selected source event with a reviewed parser in a no-effects sandbox. Persist assessment, not a new live signal. |
| TR-05-A02 | Compare parser versions | local_parser_compare; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| TR-05-A03 | Open execution | TR-06; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No retained source revision is available for this event.

Next permitted route: `TR-04` (Incoming signal stream). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No retained source revision is available for this event.; next TR-04 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Plan preview makes no reservation or order. Unconsumed material and invalid stop cannot be replaced silently. Raw display is inert and rights-scoped.

Run every SC-TR-05-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-098, CP-103, CP-106.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-05-P01 | Original/revisions | information | Original/revisions presents typed facts or approved explanatory content for Signal evidence and plan preview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR05ReadModel.panels.01 |
| TR-05-P02 | Parsed fields/evidence | information | Parsed fields/evidence presents typed facts or approved explanatory content for Signal evidence and plan preview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR05ReadModel.panels.02 |
| TR-05-P03 | Instrument resolution | information | Instrument resolution presents typed facts or approved explanatory content for Signal evidence and plan preview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR05ReadModel.panels.03 |
| TR-05-P04 | Risk/stop/horizon plan | information | Risk/stop/horizon plan presents typed facts or approved explanatory content for Signal evidence and plan preview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR05ReadModel.panels.04 |
| TR-05-P05 | Routing preview | checklist | Routing preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR05ReadModel.panels.05 |
| TR-05-P06 | Execution links | information | Execution links presents typed facts or approved explanatory content for Signal evidence and plan preview, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR05ReadModel.panels.06 |


---

# TR-06: Orders, fills and commands

**Purpose:** Reconcile durable intentions against actual venue order families and executions.

Page: `/trade/orders`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-06`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Command queue.
2. Orders/fills tabs.
3. Unknown outcome queue.
4. Correlations.

## Table and query behavior

Columns in default order: Intent, Account, Instrument, Purpose, Broker ID, Acknowledged, Filled, Remaining, Outcome.

Filters: Account, Family, Purpose, Status, Time.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-06-A01 | Open family | local_order_family; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| TR-06-A02 | Request outcome reconciliation | ACTION-TR-06-A02; Enqueue permitted authoritative readback. It may record observed facts but does not independently create or retry an order. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No order or command records in this scope.

Next permitted route: `TR-04` (Incoming signal stream). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No order or command records in this scope.; next TR-04 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

UNKNOWN is not rejected; received/accepted/working/filled states distinct. Retry cannot duplicate effects; known IDs stay consistent.

Run every SC-TR-06-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-103, CP-110.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-06-P01 | Command queue | table | Command queue is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR06ReadModel.panels.01 |
| TR-06-P02 | Orders/fills tabs | information | Orders/fills tabs presents typed facts or approved explanatory content for Orders, fills and commands, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR06ReadModel.panels.02 |
| TR-06-P03 | Unknown outcome queue | table | Unknown outcome queue is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR06ReadModel.panels.03 |
| TR-06-P04 | Correlations | chart | Correlations uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: TR06ReadModel.panels.04 |


---

# TR-07: Broker accounts and capabilities

**Purpose:** Manage account configuration and exact route qualification.

Page: `/trade/accounts`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-07`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Accounts.
2. Capability matrix.
3. Balance/permission state.
4. Change review.

## Table and query behavior

Columns in default order: Account, Adapter, Venue/API, Environment, Products, Connection, Qualification, Writer site.

Filters: Adapter, Environment, Qualification.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-07-A01 | Add account | TR-08; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| TR-07-A02 | Open capability evidence | local_capability; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| TR-07-A03 | Pause new entries | ACTION-TR-07-A03; Pause new admissions only for exact displayed account/source after versioned preview. Maintain authorized exits, stops and outstanding commands. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No accounts are configured.

Next permitted route: `TR-08` (Broker account configuration). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No accounts are configured.; next TR-08 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Broker logo or class override is not qualification. Every advertised feature links to evidence or blocking reason.

Run every SC-TR-07-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-100, CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-07-P01 | Accounts | information | Accounts presents typed facts or approved explanatory content for Broker accounts and capabilities, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR07ReadModel.panels.01 |
| TR-07-P02 | Capability matrix | information | Capability matrix presents typed facts or approved explanatory content for Broker accounts and capabilities, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR07ReadModel.panels.02 |
| TR-07-P03 | Balance/permission state | information | Balance/permission state presents typed facts or approved explanatory content for Broker accounts and capabilities, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR07ReadModel.panels.03 |
| TR-07-P04 | Change review | checklist | Change review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR07ReadModel.panels.04 |


---

# TR-08: Broker account configuration

**Purpose:** Bind an exact account and secret reference, then perform read-only qualification.

Page: `/trade/accounts/new`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-08`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Adapter/mode.
2. External identity.
3. Credential reference.
4. Product metadata.
5. Capabilities.
6. Review.

## Table and query behavior

Columns in default order: Capability, Required, Observed, Evidence age, Gap.

No additional domain filters; retain context and selected object.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-08-A01 | Save inactive account | F-BROKER; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-08-A02 | Run read-only checks | F-BROKER; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-08-A03 | Review activation | F-BROKER; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

Choose an implemented adapter to configure an account.

Next permitted route: `TR-07` (Broker accounts and capabilities). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-BROKER: Private brokerage account

Steps: Adapter → External identity → Credential references → Products → Read checks → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| account_label | Account label | text | yes | unset | 1..80 plain text; No credentials |
| adapter_id | Adapter | id | yes | unset | reviewed registry ID; No module import string |
| venue_id | Venue/API variant | id | yes | unset | supported adapter configuration; CCXT exchange not inferred |
| external_account_ref | Broker account | id | yes | unset | verified by read-only connection; Masked in general UI |
| environment | Environment | enum:simulation,paper,live | yes | simulation | actual provider mapping required; No fake broker paper |
| credential_ref | Secret reference | secret_ref | conditional/optional | unset | preprovisioned scoped vault reference; Secret values not in this form |
| products | Products | id_list | yes | unset | qualified exact product profiles; No all-assets default |
| position_mode | Position mode | enum:netting,hedged,spot | yes | unset | verified account setting; No implicit mode change |
| max_exposure | Notional ceiling | decimal | conditional/optional | unset | from owner-approved account policy; No auto increase |
| entry_enabled | Enable new entries | boolean | yes | false | true only separate release gates; Save never enables trading |

**Save:** Save inactive account draft

**Preview:** Read identity, scopes, positions and capability evidence

**Confirm:** Apply approved inactive configuration; activation separate operation

Account deletion becomes drain/handoff workflow. Credential rotation separate qualified workflow; no GET side effects.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | Choose an implemented adapter to configure an account.; next TR-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No raw secrets returned or live default. Restored flags cannot make account active. Disabling entries retains existing management.

Run every SC-TR-08-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-100.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-08-P01 | Adapter/mode | information | Adapter/mode presents typed facts or approved explanatory content for Broker account configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR08ReadModel.panels.01 |
| TR-08-P02 | External identity | information | External identity presents typed facts or approved explanatory content for Broker account configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR08ReadModel.panels.02 |
| TR-08-P03 | Credential reference | information | Credential reference presents typed facts or approved explanatory content for Broker account configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR08ReadModel.panels.03 |
| TR-08-P04 | Product metadata | information | Product metadata presents typed facts or approved explanatory content for Broker account configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR08ReadModel.panels.04 |
| TR-08-P05 | Capabilities | information | Capabilities presents typed facts or approved explanatory content for Broker account configuration, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR08ReadModel.panels.05 |
| TR-08-P06 | Review | checklist | Review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR08ReadModel.panels.06 |


---

# TR-09: Signal providers and collectors

**Purpose:** Manage reusable transport instances, source products, analysts and cursors.

Page: `/trade/sources`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-09`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Sources.
2. Transport health.
3. Parser coverage.
4. Rights.
5. History jobs.

## Table and query behavior

Columns in default order: Source, Transport, Channel/product, Analyst scope, Parser, Cursor, Lag, Status.

Filters: Transport, Provider, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-09-A01 | Add source | TR-10; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |
| TR-09-A02 | Inspect coverage | local_coverage; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| TR-09-A03 | Pause new admissions | ACTION-TR-09-A03; Pause new admissions only for exact displayed account/source after versioned preview. Maintain authorized exits, stops and outstanding commands. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No authorized source is configured.

Next permitted route: `TR-10` (Source onboarding and parser laboratory). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

This is a read/navigation page. Relevant changes navigate to the explicit scoped form; do not add ad hoc financial controls.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No authorized source is configured.; next TR-10 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

One transport may host multiple channel identities without conflating provider IDs; no resetting all external stream rules.

Run every SC-TR-09-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-106.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-09-P01 | Sources | information | Sources presents typed facts or approved explanatory content for Signal providers and collectors, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR09ReadModel.panels.01 |
| TR-09-P02 | Transport health | information | Transport health presents typed facts or approved explanatory content for Signal providers and collectors, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR09ReadModel.panels.02 |
| TR-09-P03 | Parser coverage | information | Parser coverage presents typed facts or approved explanatory content for Signal providers and collectors, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR09ReadModel.panels.03 |
| TR-09-P04 | Rights | information | Rights presents typed facts or approved explanatory content for Signal providers and collectors, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR09ReadModel.panels.04 |
| TR-09-P05 | History jobs | table | History jobs is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR09ReadModel.panels.05 |


---

# TR-10: Source onboarding and parser laboratory

**Purpose:** Configure source and prove parser behavior on full authorized selected history.

Page: `/trade/sources/new`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-10`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Transport/access.
2. Channel/analyst.
3. Rights.
4. History coverage.
5. Labeled classifications.
6. Parser comparison.
7. Review.

## Table and query behavior

Columns in default order: Message/revision, Expected action, Parser action, Unconsumed fields, Instrument, Difference.

Filters: Disposition, Revision, Parser.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-10-A01 | Save source draft | F-SOURCE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-10-A02 | Import authorized history | F-SOURCE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-10-A03 | Run parser validation | F-SOURCE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No source messages have been imported for validation.

Next permitted route: `TR-09` (Signal providers and collectors). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-SOURCE: Signal provider and parser

Steps: Transport → Source identity → Permissions → History → Parser → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| transport_instance_id | Transport | id | yes | unset | authorized shared collector instance; No duplicate bot for same transport unless required |
| provider_id | Provider | id | yes | unset | stable registered source identity; Not just telegram/discord |
| channel_product_id | Channel/product | text | yes | unset | stable external source identity;max200; Preserve channel-level lineage |
| analyst_mapping | Analyst mapping version | id | yes | unset | reviewed deterministic mapping; No ambiguous display-name matching |
| allowed_products | Asset profiles | id_list | yes | unset | explicit source scope; No default all crypto |
| parser_version_id | Parser version | id | yes | unset | reviewed parser registry; No arbitrary code |
| rights_grant_id | Rights grant | id | conditional/optional | unset | required for selected commercial/model uses; Personal receipt not resale grant |
| history_start | History start | datetime | conditional/optional | unset | authorized range;before end; Do not invent original revisions |
| history_end | History end | datetime | conditional/optional | unset | not future;paired range; Save does not start import |
| entry_enabled | Enable admissions | boolean | yes | false | activation separate reviewed transition; Management of old trades separate |

**Save:** Create real source draft

**Preview:** Validate access metadata and parser coverage

**Confirm:** Save draft or enqueue explicitly authorized history read job

Import progress durable; ingestion state separate from live admissions; edits/replies/cancellations included.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No source messages have been imported for validation.; next TR-09 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Full selected history counted with gaps; no fake examples in production. New parser is not live merely because sample messages parse.

Run every SC-TR-10-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-106.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-10-P01 | Transport/access | information | Transport/access presents typed facts or approved explanatory content for Source onboarding and parser laboratory, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR10ReadModel.panels.01 |
| TR-10-P02 | Channel/analyst | information | Channel/analyst presents typed facts or approved explanatory content for Source onboarding and parser laboratory, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR10ReadModel.panels.02 |
| TR-10-P03 | Rights | information | Rights presents typed facts or approved explanatory content for Source onboarding and parser laboratory, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR10ReadModel.panels.03 |
| TR-10-P04 | History coverage | table | History coverage is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR10ReadModel.panels.04 |
| TR-10-P05 | Labeled classifications | information | Labeled classifications presents typed facts or approved explanatory content for Source onboarding and parser laboratory, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR10ReadModel.panels.05 |
| TR-10-P06 | Parser comparison | information | Parser comparison presents typed facts or approved explanatory content for Source onboarding and parser laboratory, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR10ReadModel.panels.06 |
| TR-10-P07 | Review | checklist | Review enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR10ReadModel.panels.07 |


---

# TR-11: Routing and allocation rules

**Purpose:** Define effective destination rules without duplicating accounts or stranding existing exits.

Page: `/trade/routing`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-11`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Rule priority.
2. Match criteria.
3. Destination preview.
4. Conflicts.
5. Version diff.

## Table and query behavior

Columns in default order: Rule, Provider/analyst, Assets, Destinations, Effective state, Conflict.

Filters: Provider, Account, Product.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-11-A01 | Save draft rule | F-ROUTING; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-11-A02 | Preview matches | F-ROUTING; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-11-A03 | Submit config change | F-ROUTING; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No routing rules are configured.

Next permitted route: `TR-07` (Broker accounts and capabilities). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-ROUTING: Routing rule

Steps: Source match → Destination scope → Conflict preview → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| source_id | Source | id | yes | unset | registered source; Exact provider identity |
| analyst_ids | Analysts | id_list | conditional/optional | unset | within source; Null=all authorized analysts, not all providers |
| product_ids | Products | id_list | yes | unset | explicit qualified scope; Not ticker heuristic |
| instrument_filter | Instrument filter | id_list | conditional/optional | unset | verified instruments; No regex code execution |
| destination_ids | Destinations | id_list | yes | unset | distinct owned accounts; Duplicate rules do not duplicate intents |
| priority | Priority | integer | yes | 100 | 0..10000 deterministic conflict rule; No silently conflicting overrides |
| entry_enabled | New entries | boolean | yes | false | true only with release; Exit ownership stays on original account |

**Save:** Persist rule draft

**Preview:** Evaluate stored non-executing event set and show per-destination dedup

**Confirm:** Commit reviewed configuration revision

Preview never calls broker write. Change diff declares future-entry only versus independently approved active transition.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No routing rules are configured.; next TR-07 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Overlapping rules deduplicate exact destination intents. Entry route edits cannot redirect existing source exits to a new account.

Run every SC-TR-11-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-097, CP-101.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-11-P01 | Rule priority | information | Rule priority presents typed facts or approved explanatory content for Routing and allocation rules, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR11ReadModel.panels.01 |
| TR-11-P02 | Match criteria | information | Match criteria presents typed facts or approved explanatory content for Routing and allocation rules, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR11ReadModel.panels.02 |
| TR-11-P03 | Destination preview | checklist | Destination preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR11ReadModel.panels.03 |
| TR-11-P04 | Conflicts | information | Conflicts presents typed facts or approved explanatory content for Routing and allocation rules, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR11ReadModel.panels.04 |
| TR-11-P05 | Version diff | information | Version diff presents typed facts or approved explanatory content for Routing and allocation rules, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR11ReadModel.panels.05 |


---

# TR-12: Sizing, stops and profit policies

**Purpose:** Configure released sizing/stop/target/horizon recipes and show hard-limit precedence.

Page: `/trade/policies`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-12`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Policy scope.
2. Size/risk controls.
3. Initial protection.
4. Targets/trailing.
5. Deadlines.
6. Effective preview.

## Table and query behavior

Columns in default order: Setting, Source, Inherited, Requested, Hard ceiling, Effective, Applies to.

Filters: Account, Provider, Strategy, Product.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-12-A01 | Save policy draft | F-POLICY, F-SIZING-RECIPE, F-STOP-RECIPE, F-TRAIL-RECIPE, F-TARGET-RECIPE, F-HOLD-RECIPE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-12-A02 | Preview on signal | F-POLICY, F-SIZING-RECIPE, F-STOP-RECIPE, F-TRAIL-RECIPE, F-TARGET-RECIPE, F-HOLD-RECIPE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-12-A03 | Request review | F-POLICY, F-SIZING-RECIPE, F-STOP-RECIPE, F-TRAIL-RECIPE, F-TARGET-RECIPE, F-HOLD-RECIPE; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No compatible released management policy is available.

Next permitted route: `TR-10` (Source onboarding and parser laboratory). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-POLICY: Management policy draft

Steps: Scope → Sizing → Stop → Targets/trail → Holding → Preview.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| policy_name | Name | text | yes | unset | 1..100 plain text; Version immutable after release |
| product_profile_id | Product profile | id | yes | unset | exact currency/quantity/trigger semantics; No share math for options |
| size_mode | Sizing mode | enum:risk,fixed_units,fixed_notional,source_scaled | yes | risk | supported released family; All modes bounded by hard limits |
| risk_budget | Risk budget | decimal | conditional/optional | unset | explicit approved currency/percentage unit; required for risk mode |
| fixed_units | Fixed units | decimal | conditional/optional | unset | legal positive step;hard-cap bound; required only fixed_units |
| fixed_notional | Fixed notional | decimal | conditional/optional | unset | positive currency value; required only fixed_notional |
| stop_recipe_id | Fallback stop recipe | id | conditional/optional | unset | released compatible recipe; Null means stopless signal cannot be resolved without provider stop |
| target_policy_id | Target policy | id | yes | unset | released role/partial/basis definitions; No invented provider target |
| trail_recipe_id | Trail recipe | id | conditional/optional | unset | released non-loosening recipe; No trailing automatic activation by price default |
| holding_policy_id | Holding rule | id | yes | unset | finite applicable strategy/product deadlines; Includes session coverage |
| apply_to | Apply to | enum:future_entries | yes | future_entries | existing allocations require separate transition; No silent retrofit |

**Save:** Save draft policy only

**Preview:** Resolve a complete plan with exact provenance on selected event

**Confirm:** Submit for qualified owner review; live release separate

Unknown initial price or risk cannot bypass sizing. All configuration numbers validated on server with decimal/product units.

### F-SIZING-RECIPE: Sizing and portfolio-risk recipe

Steps: Identity and product scope → Sizing basis → Hard limits and reserves → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| recipe_name | Recipe name | text | yes | unset | max100;unique within account policy scope; Private draft only |
| product_profile_id | Product profile | id | yes | unset | verified compatible product schema; Defines units and multiplier |
| risk_basis | Sizing basis | enum:fixed_units,fixed_notional,loss_budget | yes | unset | must be allowed by the selected product profile; No algorithm inferred from a name |
| risk_amount | Per-trade risk amount | decimal | conditional/optional | unset | required for loss_budget;positive;at or below released ceiling; Currency or percent basis supplied by verified account policy |
| risk_currency | Risk currency | currency | conditional/optional | unset | required for money amount;must match approved basis; No implicit FX conversion |
| minimum_quantity | Minimum legal quantity | decimal | conditional/optional | unset | server instrument minimum;cannot raise quantity beyond risk; Smaller signals may be rejected |
| maximum_quantity | Maximum quantity | decimal | conditional/optional | unset | nonnegative compatible units;at or below account hard cap; Does not replace buying-power checks |
| account_limit_policy_id | Daily weekly exposure and loss limits | id | yes | unset | released server policy;customer cannot weaken it; Preview all applicable account and owner limits |
| allocation_policy_id | Analyst and concentration policy | id | yes | unset | compatible released overlap/reservation policy; Shared capital and pending orders remain counted |
| cost_stress_policy_id | Fees slippage and stress allowance | id | yes | unset | verified product-specific cost and stress convention; A stop price is not guaranteed loss |

**Save:** Save sizing recipe draft

**Preview:** Show all resolved quantities units caps and failure reasons on a selected signal

**Confirm:** Submit recipe for research/review only;do not alter live sizing

No browser computation can size the order. Edit requires actual product metadata and inherited hard boundaries. Unknown risk or buying power remains a blocker.

### F-STOP-RECIPE: Initial-stop and fallback recipe

Steps: Stop meaning → Source precedence → Fallback parameters → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| recipe_name | Recipe name | text | yes | unset | max100; Versioned internal recipe |
| reference_basis | Stop reference basis | enum:instrument_price,underlying_price,option_premium,combo_price | yes | unset | must match resolved source meaning and product; No automatic underlying-to-premium translation |
| provider_stop_policy | Provider stop treatment | enum:require_valid,use_valid_else_released_fallback | yes | unset | invalid/crossed supplied stop is not missing; Fallback only for genuinely absent source stop |
| fallback_method_id | Fallback calculation | id | conditional/optional | unset | released or research-only compatible method;required when selected; No invented percentage default |
| fallback_distance | Fallback distance parameter | decimal | conditional/optional | unset | units/bounds from method schema;positive where profile requires; Research draft until independently reviewed |
| volatility_window | Volatility lookback observations | integer | conditional/optional | unset | required only for compatible volatility recipe;integer>=2; Availability and warmup explicitly evaluated |
| native_coverage_policy_id | Coverage and unprotected-window policy | id | yes | unset | qualified exact account product session recipe; No claimed continuous protection on unsupported route |
| missing_data_action | Missing or invalid data action | enum:block_new_entry | yes | block_new_entry | cannot be relaxed by this form; Existing positions follow their separate recovery plan |

**Save:** Save initial-stop draft

**Preview:** Display provider/fallback provenance invalidation and attainable coverage

**Confirm:** Submit for reviewed policy release;no current stop move

Stops with valid negative product prices require an explicit compatible profile;do not globally forbid or reinterpret them. Existing trade remains on its original policy.

### F-TRAIL-RECIPE: Trailing and profit-lock recipe

Steps: Activation → Distance and floor → Update constraints → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| recipe_name | Recipe name | text | yes | unset | max100; Versioned draft |
| activation_rule_id | Activation condition | id | yes | unset | compatible reviewed rule;explicit trigger basis; A target can activate a trail only through policy |
| activation_value | Activation parameter | decimal | conditional/optional | unset | typed units and bounds supplied by rule; Missing value is not zero |
| trail_method_id | Trailing method | id | yes | unset | fixed percent price volatility or other implemented method ID; Only installed qualified formulas offered |
| trail_distance | Distance parameter | decimal | conditional/optional | unset | required by method;unit/bounds from recipe schema; Never an untyped generic percent |
| minimum_improvement_ticks | Minimum update improvement | integer | conditional/optional | unset | integer>=1;compatible tick metadata; Optimization cannot suppress a breached active threshold |
| update_cooldown_ms | Elective update cooldown in ms | integer | conditional/optional | unset | integer>=0;bounded by released policy; Urgent risk-reducing behavior has separate priority |
| never_loosen | Preserve activated protective floor | boolean | yes | true | must remain true; Both long and short directions enforced server-side |
| replacement_recipe_id | Broker stop-update procedure | id | yes | unset | qualified same-order or cancel-replace procedure; Native high-water reset and unknown outcomes accounted for |

**Save:** Save trailing draft

**Preview:** Show candidate activated and broker-confirmed floors separately on replay

**Confirm:** Submit reviewed recipe;no live update from Save

An unattainable candidate floor is not a breach. Every accepted change retains actual fills and outstanding close commitments;no app function assumes a bracket from its name.

### F-TARGET-RECIPE: Profit-target level and partial-exit recipe

Steps: Stable target identity → Meaning and level → Reduction basis → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| target_id | Target identity | id | yes | unset | stable within version;updates do not replace another target; Omitted means no change;remove is explicit operation |
| operation | Target operation | enum:add,update,remove | yes | unset | exact target/version scope; Removal never removes protective stop |
| role | Target role | enum:reference,hard_exit,partial_exit,trailing_trigger | conditional/optional | unset | required for add/update; Provider intent preserved unless released policy states otherwise |
| reference_basis | Trigger basis | enum:instrument_price,underlying_price,option_premium,combo_price | conditional/optional | unset | required for add/update;product-compatible; No raw price basis inference |
| level | Target price or trigger level | decimal | conditional/optional | unset | required for add/update;profile-valid domain; No invented level when source supplied none |
| reduction_basis | Reduction basis | enum:original_allocation,remaining_allocation,cumulative_goal | conditional/optional | unset | required for partial exit; Exact quantity-conserving semantics |
| reduction_fraction | Reduction fraction | decimal | conditional/optional | unset | required for partial exit;0<value<=1; Server cumulative rounding avoids excess sales |
| rounding_policy_id | Legal quantity rounding | id | conditional/optional | unset | required for partial exit;verified instrument step; Small allocations may not support every trim |
| trail_recipe_id | Trail activated at level | id | conditional/optional | unset | required for trailing_trigger; Activating a trail is not automatically an immediate sale |

**Save:** Save explicit target operation

**Preview:** Show changed levels actual owned quantity possible closes and remaining coverage

**Confirm:** Submit target-policy review or separately authorized existing-position action

Multiple target records form one ordered target set. Stable IDs distinguish revisions,omission,clear and deletion. A gap across several levels is processed through one coordinated exit manager.

### F-HOLD-RECIPE: Holding session and deadline recipe

Steps: Strategy horizon → Venue rules → Deadline and failure → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| recipe_name | Recipe name | text | yes | unset | max100; Versioned draft |
| holding_policy_id | Holding convention | id | yes | unset | implemented strategy horizon profile; No universal intraday cutoff for every strategy |
| maximum_hold_seconds | Maximum strategy hold | integer | conditional/optional | unset | required when profile uses elapsed duration;integer>0; Not a market-open assumption |
| session_calendar_id | Exchange session calendar | id | yes | unset | verified product/venue calendar plus broker exceptions; Holidays DST and special sessions tested |
| overnight_allowed | Overnight permitted | boolean | yes | false | cannot exceed released account/product permission; Closing market can make stops dormant |
| product_deadline_policy_id | Expiry notice and broker deadline | id | conditional/optional | unset | required for expiring/notice-sensitive products; Earliest applicable mandatory deadline wins |
| emergency_exit_recipe_id | Deadline and emergency handling | id | yes | unset | qualified route urgency/quantity procedure; Unknown prior order prevents blind duplicate exit |

**Save:** Save horizon recipe draft

**Preview:** Display actual current and future deadlines coverage windows and known venue restrictions

**Confirm:** Submit reviewed recipe;existing episodes preserve their deadline unless separately authorized

A valid calendar does not prove the broker can create/cancel/execute every order at that time. Do not suppress valid existing-position management with entry-hour filters.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No compatible released management policy is available.; next TR-10 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

UI only saves a versioned draft until approved; no absent source stop creates unbounded trade. Open positions retain bound policy unless separately transitioned.

Run every SC-TR-12-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-101, CP-103.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-12-P01 | Policy scope | information | Policy scope presents typed facts or approved explanatory content for Sizing, stops and profit policies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR12ReadModel.panels.01 |
| TR-12-P02 | Size/risk controls | information | Size/risk controls presents typed facts or approved explanatory content for Sizing, stops and profit policies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR12ReadModel.panels.02 |
| TR-12-P03 | Initial protection | information | Initial protection presents typed facts or approved explanatory content for Sizing, stops and profit policies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR12ReadModel.panels.03 |
| TR-12-P04 | Targets/trailing | information | Targets/trailing presents typed facts or approved explanatory content for Sizing, stops and profit policies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR12ReadModel.panels.04 |
| TR-12-P05 | Deadlines | information | Deadlines presents typed facts or approved explanatory content for Sizing, stops and profit policies, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR12ReadModel.panels.05 |
| TR-12-P06 | Effective preview | checklist | Effective preview enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR12ReadModel.panels.06 |


---

# TR-13: Reconciliation and trading incidents

**Purpose:** Explain actual discrepancies and approved containment rather than silently balancing them.

Page: `/trade/incidents`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-13`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Prioritized incidents.
2. Broker/store comparison.
3. Evidence timeline.
4. Containment.
5. Recovery.

## Table and query behavior

Columns in default order: Incident, Account, Severity, Known exposure, Difference, Protection, Owner, State.

Filters: Severity, Account, Age, State.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-13-A01 | Acknowledge | F-INCIDENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-13-A02 | Run scoped readback | F-INCIDENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-13-A03 | Review recovery action | F-INCIDENT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No active incidents; last complete checks are shown separately.

Next permitted route: `TR-01` (Trading command center). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-INCIDENT: Incident management

Steps: Evidence → Responsibility → Containment → Resolution.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| incident_id | Incident | id | yes | unset | within authorized scope; Persisted incident |
| operation | Operation | enum:acknowledge,assign,reconcile,propose_resolution | yes | acknowledge | explicit per-role permission; No execute arbitrary command |
| assignee_id | Assignee | id | conditional/optional | unset | active role eligible for incident; Cannot assign financial authority |
| note | Note | text | yes | unset | 1..2000 plain text; No secrets |
| evidence_ids | Evidence | id_list | conditional/optional | unset | scoped immutable objects; required for resolution proposal |

**Save:** Save comment/action intent

**Preview:** Show affected scope and proposed operation

**Confirm:** Acknowledge/assign/readback job or submit resolution review

Acknowledge never marks resolved. Reconcile is read-only broker action unless separately approved corrective command.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No active incidents; last complete checks are shown separately.; next TR-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Acknowledge is not resolve; no arbitrary position overwrite or blind resubmit. Closed incident requires fresh reconciled evidence.

Run every SC-TR-13-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-103, CP-105, CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-13-P01 | Prioritized incidents | information | Prioritized incidents presents typed facts or approved explanatory content for Reconciliation and trading incidents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR13ReadModel.panels.01 |
| TR-13-P02 | Broker/store comparison | information | Broker/store comparison presents typed facts or approved explanatory content for Reconciliation and trading incidents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR13ReadModel.panels.02 |
| TR-13-P03 | Evidence timeline | timeline | Evidence timeline shows immutable ordered events and revisions for the selected object. Retain original event time, receipt time, actor and correlation; do not reorder history to imply a better financial outcome. Query: TR13ReadModel.panels.03 |
| TR-13-P04 | Containment | information | Containment presents typed facts or approved explanatory content for Reconciliation and trading incidents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR13ReadModel.panels.04 |
| TR-13-P05 | Recovery | information | Recovery presents typed facts or approved explanatory content for Reconciliation and trading incidents, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR13ReadModel.panels.05 |


---

# TR-14: Trading performance and execution quality

**Purpose:** Distinguish economic outcome from signal and execution quality.

Page: `/trade/performance`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-14`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Account/analyst book.
2. P&L/drawdown.
3. Latency/slippage.
4. Costs.
5. Incomplete records.

## Table and query behavior

Columns in default order: Episode, Gross, Fees, Net, Source delay, Fill slippage, Protection delay, Quality.

Filters: Account, Analyst, Policy, Period, Book.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-14-A01 | Export report | F-REPORT; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-14-A02 | Inspect episode | TR-03; Use route registry and exact selected ID. Cross-origin private/commercial navigation requires independent authentication. Missing selection displays a prompt, never guesses. |

## Metrics

| ID | Metric | Unit | Definition and missing-data behavior |
|---|---|---|---|
| M-TR-14-01 | Net P&L | money | Sum identified realized and eligible unrealized P&L minus applicable costs, with cashflows excluded and all conversions versioned. Missing fees/marks/basis => partial or unavailable, not net verification. |
| M-TR-14-02 | Completed episodes | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |
| M-TR-14-03 | Maximum drawdown | ratio | max over chronological normalized equity of (running_peak-equity)/running_peak. Requires positive comparable equity and full stated history; use full-resolution data before chart decimation; unknown gaps qualify coverage. |
| M-TR-14-04 | Unresolved fees | count | Exact count over authorized snapshot with its filter and completeness. Zero only if query complete and count truly zero; never infer total rows from one page. |


## Empty state

No fully qualified economic report is available.

Next permitted route: `TR-06` (Orders, fills and commands). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-REPORT: Report / export request

Steps: Scope → Period/book → Format → Review.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| object_ids | Scoped objects | id_list | yes | unset | server-authorized nonempty IDs;no all-tenants wildcard; Scope is immutable in job |
| period_start | Start | datetime | yes | unset | UTC instant;before end;within retention; User timezone converted explicitly |
| period_end | End | datetime | yes | unset | after start;not future for actual report; End-exclusive interval documented |
| book | Book | enum:actual,model,platform,source,business | yes | actual | supported for caller and report;business never investment; Do not merge origins |
| currency | Reporting currency | currency | conditional/optional | unset | supported conversion policy or show separate currencies; Reference rate basis shown |
| format | Format | enum:csv,json,pdf | yes | csv | server supports generation;PDF only after renderer implemented; No formula injection in CSV |
| include_sensitive | Include sensitive fields | boolean | conditional/optional | false | only explicitly permitted by current scope; Secrets always excluded |

**Save:** Persist export definition

**Preview:** Show coverage, cost basis and rights without file generation

**Confirm:** Start bounded export job; reauthorize at download

No public unapproved hypothetical export; expiring download token scoped to principal and record; failed job not empty report.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No fully qualified economic report is available.; next TR-06 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Confirmed execution IDs drive attribution; no tax-lot claim for reporting basis. Timing metrics show reference availability and partial populations.

Run every SC-TR-14-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-102, CP-112.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-14-P01 | Account/analyst book | information | Account/analyst book presents typed facts or approved explanatory content for Trading performance and execution quality, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR14ReadModel.panels.01 |
| TR-14-P02 | P&L/drawdown | chart | P&L/drawdown uses only its verified report snapshot and definition IDs. Show unavailable series as gaps; link each point to the exact period and eligible evidence. There is no plot when data is absent. Query: TR14ReadModel.panels.02 |
| TR-14-P03 | Latency/slippage | information | Latency/slippage presents typed facts or approved explanatory content for Trading performance and execution quality, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR14ReadModel.panels.03 |
| TR-14-P04 | Costs | information | Costs presents typed facts or approved explanatory content for Trading performance and execution quality, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR14ReadModel.panels.04 |
| TR-14-P05 | Incomplete records | table | Incomplete records is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR14ReadModel.panels.05 |


---

# TR-15: Historical signal backtests

**Purpose:** Run durable source-history replay using actual management/execution semantics.

Page: `/trade/backtests`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-15`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. History catalog.
2. Coverage check.
3. Run configuration.
4. Queue/progress.
5. Reports.

## Table and query behavior

Columns in default order: Run, Source versions, Period, Coverage, Recipe, State, Net result, Uncertainty.

Filters: Provider, Period, Run state.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-15-A01 | Create replay | F-BACKTEST; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-15-A02 | Resume job | ACTION-TR-15-A02; Resume only incomplete shards of the same frozen job scope. Reauthorize dataset use and compute budget; do not resubmit external publications. |
| TR-15-A03 | Compare results | local_backtest_compare; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No historical replay has completed.

Next permitted route: `TR-10` (Source onboarding and parser laboratory). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-BACKTEST: Historical copier replay

Steps: Sources/history → Products/policies → Capital/costs → Period → Scope and run.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| dataset_version_ids | Source and market datasets | id_list | yes | unset | authorized immutable history snapshots; Missing originals and revisions remain visible |
| policy_version_ids | Policies | id_list | yes | unset | compatible baseline/candidate recipes; Do not change live policy |
| account_profile_id | Account profile | id | yes | unset | declared shared capital and product limits; No full capital independently assigned to each signal |
| period_start | Start | datetime | yes | unset | UTC before end;within dataset; Use information availability |
| period_end | End | datetime | yes | unset | UTC after start;not future; Full selected range |
| cost_scenario_ids | Costs/slippage | id_list | yes | unset | qualified fee/funding/latency assumptions; No silently zero costs |
| ambiguity_policy | Intrabar ambiguity | enum:bound_outcomes,require_finer_data | yes | bound_outcomes | never select favorable path silently; Bounds labeled hypothetical |
| resource_profile_id | Resources | id | yes | unset | approved job limits; Worker isolated from protection |

**Save:** Persist reproducible replay draft

**Preview:** Resolve coverage/gaps/candidate count and estimated resources

**Confirm:** Enqueue replay job through research service only

Backtest report persists and distinguishes outcomes, actual cost status, fills, episodes and incomplete data.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No historical replay has completed.; next TR-10 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

Canceled/unfilled/open trades remain; candle path ambiguity retained; no historical replay invokes live adapter.

Run every SC-TR-15-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-106, CP-107, CP-108.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-15-P01 | History catalog | table | History catalog is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR15ReadModel.panels.01 |
| TR-15-P02 | Coverage check | information | Coverage check presents typed facts or approved explanatory content for Historical signal backtests, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR15ReadModel.panels.02 |
| TR-15-P03 | Run configuration | information | Run configuration presents typed facts or approved explanatory content for Historical signal backtests, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR15ReadModel.panels.03 |
| TR-15-P04 | Queue/progress | table | Queue/progress is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR15ReadModel.panels.04 |
| TR-15-P05 | Reports | information | Reports presents typed facts or approved explanatory content for Historical signal backtests, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR15ReadModel.panels.05 |


---

# TR-16: Private settings, site role and recovery

**Purpose:** Show current artifact, actual site authority, backup and recovery evidence.

Page: `/trade/system`. Service: `private_execution`. Read model: `/api/operator/v1/ui/tr-16`. Access: private_owner, with verified private owner account scope.

Status: designed, not implemented or application-tested by this delivery. Reuse current domain services and verify newer source before modifying consumers.

## Layout and sequence

Breadcrumb, title, scope, origin/mode badges and as-of; separate action row

Desktop: 12-column grid; first main analytic panel spans8 and side evidence spans4; tables span12; configuration uses 8+4 summary rail

Tablet: 8-column grid; dominant panels span8; details follow primary view

Phone: One column, context first, urgent state next, metrics in2 columns, actions above optional charts; tables have scoped scroll or detail cards

Details: 560px at desktop, fullscreen at<=767px; browser-history-backed and accessible focus trap only when modal

Panels, in exact reading/tab order:
1. Site role/writer identity.
2. Dependencies.
3. Backup/restore status.
4. Owner access.
5. Readiness.
6. Recovery checklist.

## Table and query behavior

Columns in default order: Component, Mode, Last usable progress, Evidence, Blocker.

Filters: Site, Account, Severity.

Newest event descending then immutable ID descending; catalog uses approved display_order then product_id; financial tables never sort formatted currency strings. Visible financial views:10s fallback with1 in-flight request per resource; administrative tables:30s; public catalog:60s; immutable report:manual; hidden tabs stop polling. SSE later only through same scoped snapshot protocol.

public approved projection only: max-age60 plus grant/approval invalidation; all personal/operator/auth/private-draft data: private,no-store.

Any table uses server allowlisted sort and stable cursor/snapshot, 25 rows by default with 50/100 allowed. Preserve identity, units and as-of when horizontally scrolling. The mobile card equivalent links to the same exact object. A query failure does not display “no records.”

## Actions and API bindings

| ID | Visible label | Behavior/binding |
|---|---|---|
| TR-16-A01 | Inspect backup | local_backup; Use the same scoped immutable read model and selected object. Changes to filters/compare tray/panel view affect presentation only. Downloads go through authenticated export service with all quality labels. |
| TR-16-A02 | Open non-live restore job | F-RECOVERY-OPS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |
| TR-16-A03 | Prepare promotion review | F-RECOVERY-OPS; Open named form step appropriate to label; save/validate/preview/confirm are distinct bound operations. Do not bypass earlier validation with a confirm-labelled button. |

## Metrics

No numeric headline required. Do not add a decorative performance KPI.

## Empty state

No verified deployment or recovery evidence is recorded.

Next permitted route: `TR-01` (Trading command center). Same-screen destinations focus the first permitted create-draft action. If role or rights prevent that action, explain the exact prerequisite and provide help rather than a dead control.

## Configuration forms

### F-RECOVERY-OPS: Recovery review

Steps: Scope → Artifact/data → Old writer fencing → Reconciliation → Approvals.

| Field key | Label | Type | Required | Initial value | Validation / help |
|---|---|---|---|---|---|
| service_role | Role | enum:private_writer,publisher,api,research | yes | unset | operator allowed for role; Website role not trading permission |
| source_site_id | Old site | id | yes | unset | registered site; No arbitrary SSH host |
| target_site_id | Target site | id | yes | unset | qualified distinct site; Standby initially incapable of effects |
| release_manifest_id | Release | id | yes | unset | verified immutable manifest; No mutable latest tag |
| backup_generation_id | Data generation | id | yes | unset | verified compatible restore; Known RPO/unknown commands visible |
| fencing_evidence_ids | Fencing evidence | id_list | conditional/optional | unset | required before writer promotion; Lease expiry is insufficient |
| reconciliation_evidence_id | Reconciliation | id | conditional/optional | unset | required for promotion;fresh and complete; Positions alone may not recover ownership |

**Save:** Persist review checklist

**Preview:** Evaluate required evidence; no infrastructure effects

**Confirm:** Prepare signed release/handoff card; no unattended promote from GUI in this scope

Only separate authorized deployment runbook applies effect. Default no auto failover and no storing root credentials in UI.

## All state obligations

| State | Required treatment |
|---|---|
| loading | Neutral skeleton with aria-busy=true. No0 values or prior-account rows. Navigation and help remain available. |
| ready | Render complete scoped projection with origin,as-of,currency and allowed actions. Ready data does not imply trade authorization. |
| empty | No verified deployment or recovery evidence is recorded.; next TR-01 |
| denied | 401 identity flow or403 action denial;404 for concealed cross-tenant objects. Do not include object count/title or existence leakage. |
| stale | Retain same-scope last-good snapshot with timestamp and stale labels; disable new risk and stale-preview confirmation, retain allowed reconciliation/safety paths. |
| partial | Panel-level status and missing fields. Do not sum incomplete items into a falsely complete total. Distinguish omitted basis from measured zero. |
| timeout | GET can offer bounded retry; command timeout shows UNKNOWN plus operation ID and status read, never blind resubmit. |
| error | Sanitized reason and correlation ID; retain draft; bounded recovery action. No raw trace, secret URL or server-success toast. |
| unsupported | Explain missing integration/rights/eligibility/data/profile. Configuration/read-only work remains usable. Blocked gate is not passing implementation evidence. |
| conflict | Show authorized old/new field diff; preserve local draft; require explicit reconciliation and fresh preview. |
| session_expired | Mask personal data, stop polling, preserve only safe server draft. Reauthenticate and revalidate, never replay mutation automatically. |
| maintenance | Separate UI pause from ongoing trade-management status. Show approved status and safe contact. No assumption that restart closed positions. |

For a public page, session expiry cannot make public content private or expose personal data; revalidate only the identity-aware action. Conflict applies to query snapshot or selected version when there is no editable form. Maintenance/loading errors describe the affected resource, not a fabricated whole-site outage.

## Acceptance and failure remedy

No automatic promote button; role/epoch does not fence old writer. Restore cannot copy active financial flags or stale browser sessions.

Run every SC-TR-16-<state> case and all linked journey/form/action cases through actual browser, auth, repository and service boundaries. Assert persisted outcomes and forbidden effects, not only DOM text. On failure retain the original attempt, inspect projection authorization, serializer, template/component, request-generation guard and command path. Repair the consuming layer; add regression; rerun this screen in all required browsers/viewports and the affected shared components. No fake seeded production content or weakened expectation.

Inherited requirements: CP-114.

## Panel data and component contracts

| ID | Panel | Component | Required content/query |
|---|---|---|---|
| TR-16-P01 | Site role/writer identity | information | Site role/writer identity presents typed facts or approved explanatory content for Private settings, site role and recovery, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR16ReadModel.panels.01 |
| TR-16-P02 | Dependencies | information | Dependencies presents typed facts or approved explanatory content for Private settings, site role and recovery, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR16ReadModel.panels.02 |
| TR-16-P03 | Backup/restore status | checklist | Backup/restore status enumerates independently evaluated conditions, actual outcome, reason code, evidence age and permitted next step. Do not collapse payment,connection,rights and trading authority into one active badge. Query: TR16ReadModel.panels.03 |
| TR-16-P04 | Owner access | information | Owner access presents typed facts or approved explanatory content for Private settings, site role and recovery, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR16ReadModel.panels.04 |
| TR-16-P05 | Readiness | information | Readiness presents typed facts or approved explanatory content for Private settings, site role and recovery, linked to its actual source/document version. No model-generated assertion or unexplained calculated value is inserted. Query: TR16ReadModel.panels.05 |
| TR-16-P06 | Recovery checklist | table | Recovery checklist is a server-scoped list with exact immutable row IDs, filters and a snapshot-bound cursor. Show selected scope, total only when known, and each row's quality and origin. Query: TR16ReadModel.panels.06 |


# Exact original GUI requirement traceability

| Requirement | Original text | Journey | Screens |
|---|---|---|---|
| CP-091 | Only approved projections, costs, drawdown and performance origin are visible. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-PUBLIC_CATALOG | PU-01, PU-02, PU-04, AD-07, AD-19 |
| CP-092 | Correct version/history, disclosure, risk and compatibility; no restricted source raw text. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-PUBLIC_DETAIL | PU-03, PU-04, PU-06, AD-07, AD-19 |
| CP-093 | Tenant-bound identity, no paid or trading privilege before independent gates. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-SIGNUP | ID-01, ID-02, ID-03, CU-13 |
| CP-094 | Exact legal/product/channel approval, no inferred or user-editable bypass. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-ELIGIBILITY | PU-06, ID-04, AD-19 |
| CP-095 | Whitelisted price and customer identity; success page never creates entitlement. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-CHECKOUT | PU-05, CU-11, AD-13 |
| CP-096 | Correct interval/fees, safe open-episode treatment and separate platform state. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-SUBSCRIPTIONS | PU-05, CU-11, AD-13 |
| CP-097 | Effective version, risk/capacity/rights and customer scope preserved. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-PORTFOLIO_SELECT | CU-01, CU-02, CU-03, TR-11 |
| CP-098 | Correct lifecycle sequence, expiry, origin and delivered-versus-filled distinction. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-ALERTS | CU-01, CU-04, CU-05, TR-04, TR-05 |
| CP-099 | Verified endpoint and lawful consent; safety policy distinguished from marketing. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-DELIVERY_PREFS | CU-12, CU-13, CU-16, AD-20 |
| CP-100 | Correct account/environment and token scope; no browser or other-tenant key leakage. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-PLATFORM_CONNECT | PU-08, CU-07, CU-08, TR-07, TR-08, AD-17 |
| CP-101 | Explicit new-only/sync choice, exact constraints, independent billing and mandate. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-COPY_ACTIVATE | CU-03, CU-09, TR-11, TR-12 |
| CP-102 | Only customer actual observations, real fees/marks and incomplete flags. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-CUSTOMER_PERFORMANCE | CU-01, CU-03, CU-06, TR-14 |
| CP-103 | Actual quantities, uncertainty, children, corrections and stop coverage reconcile. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-TRADE_DETAIL | CU-05, TR-01, TR-02, TR-03, TR-05, TR-06, TR-12, TR-13 |
| CP-104 | Scope-specific approved wind-down; no whole-account surprise flatten. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-SAFETY_EXIT | CU-03, CU-10, TR-02, TR-03 |
| CP-105 | Narrow staff rights, audit trail and safe communications. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-SUPPORT | PU-07, PU-08, CU-13, CU-14, CU-16, TR-13, AD-11, AD-16, AD-18, AD-21 |
| CP-106 | Licensed complete history, known-flat versus missing, exact source versions. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-LAB_UNIVERSE | TR-04, TR-05, TR-09, TR-10, TR-15, AD-03 |
| CP-107 | All fixed subsets/recipes terminal outcomes, resumable deterministic shards. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-LAB_RUN | TR-15, AD-04, AD-05 |
| CP-108 | Holdout separation, fees/capacity/correlations and rejected candidates visible. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-LAB_COMPARE | TR-15, AD-06 |
| CP-109 | Separate reviewer, immutable artifact/version/rights and audience scope. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-RELEASE_REVIEW | AD-01, AD-07, AD-08, AD-16, AD-18, AD-20 |
| CP-110 | Exact external strategy, unknown operations, quota and writer state. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-PUBLISHER | TR-06, AD-01, AD-09, AD-10, AD-17, AD-21 |
| CP-111 | Evidence-only permissions, restricted changes and expiry/wind-down preview. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-RIGHTS | AD-01, AD-02, AD-16, AD-17, AD-21 |
| CP-112 | Subscription revenue/costs separate from trading P&L. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-BUSINESS | TR-14, AD-01, AD-12, AD-13 |
| CP-113 | Broker-native status, mandates, units/NAV/fees, no SaaS custody. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-MANAGED_ACCOUNT | CU-15, AD-14, AD-15 |
| CP-114 | No financial authority before fence, current grants and external reconciliation. Implement the actual workflow through all eight declared states and nine browser/viewport projects, with per-role tenant isolation and independent data/effect assertions. | J-RECOVERY | PU-07, TR-01, TR-07, TR-13, TR-16, AD-18, AD-21, AD-22 |


---

# Exact action dispatch and editor modes

The UI uses the immutable action ID in `catalog/actions.json`. It must not infer a backend method by interpreting a button's label at runtime. The handler registry allowlists screen, form, action, phase, object type, role and effect. Shared visual components do not share authority.

A navigation action resolves a known screen and exact selected object. A read action retrieves its named projection. A form action opens the named editor mode; opening it has no effect. A command action uses the exact separately cataloged API and receives a durable operation. No arbitrary reflection, JavaScript handler supplied by the server, or raw SQL is permitted.

The form workflow request envelope is:

- `form_id`: an allowlisted form used by the current screen.
- `ui_action_id`: an action registered for that screen.
- `target_object_id`: required when revising or revoking a specific record; not a caller-selected tenant.
- `expected_revision`: existing record version, or absent for a creation that will receive a server ID.
- `fields`: exactly the form's typed properties. Partial drafts may omit required-ready fields.
- `editor_mode`: the explicit permitted create/edit/revoke/import/verify/test/review mode from the action registry.

Save creates a versioned draft or commits a strictly cosmetic preference. Validation evaluates all current field and domain requirements. Preview receives draft identity/revision and requested action, then returns a version-bound summary and blockers. Confirmation receives only the valid preview identity and idempotency key; it must not accept edited financial fields that bypass the preview. The server rechecks rights, account, tenant, version and authority before effect.

Destructive or externally effectful labels always open a preview first. “Revoke key,” “revoke membership,” “pause entries,” “change plan,” and “request handoff” do not directly mutate just because the user clicked a row action. A canceled dialog has no effect. Password/verification forms are the exception to persisted drafts: they submit to the verified identity workflow without storing raw credentials.

## Source and delivery editor modes

Source setup supports separate `save_draft`, `import_history` and `validate_parser` operations. Importing history creates a read-only resumable job; it cannot enable live admissions. Parser validation creates an assessment linked to exact event/parser versions, not another source event. Activating a source requires its separate reviewed configuration action.

Delivery preferences support separate `save_preferences`, `request_verification` and `send_test` operations. A test message is visibly labeled and cannot be parsed into the live financial ingress. A verified target does not grant trading authority.

## API credentials and staff access

Creating a scoped API key verifies the permitted read/delivery scopes and expiry, reveals the secret once, and stores only the required protected form. Revocation binds the existing key ID and version. Staff invitation, grant update and revocation likewise bind the existing membership or verified invitation identity. Last-owner protection and immediate session invalidation are server rules. An admin role never becomes private-owner authority.

## Private policy editor

TR-12 has subnavigation: Overview, Sizing, Initial stop, Targets, Trailing, Holding and Review. The active editor is one of F-POLICY, F-SIZING-RECIPE, F-STOP-RECIPE, F-TARGET-RECIPE, F-TRAIL-RECIPE or F-HOLD-RECIPE. Switching tabs preserves explicitly saved drafts and warns before discarding unsaved changes.

Recipe drafts have their own immutable version identities. A top-level policy references exact child versions; changing one creates a new proposed policy version. “Save policy draft” applies to the active editor and does not release it. “Preview on signal” resolves the whole proposed policy against the selected original signal and exact product/account facts. “Request review” packages all changed versions and relevant tests. Existing-position policy changes require their separate scoped action, not a side effect of releasing future-entry defaults.

Method/recipe selectors use server-returned compatible schemas and bounds. A user may not supply a new calculation name, multiplier, price basis or arbitrary formula. Missing price, risk, position or entitlement inputs appear as blockers rather than guessed defaults. Cosmetic layout changes cannot conceal these blockers.

## Exports versus execution

“Export broker-bound instructions” generates a scoped document/job. It does not submit those instructions. “Prepare release card” generates evidence and proposed authority for review; it does not promote a site. “Reconcile unknown” requests authoritative readback; it is not a generic retry button. The result screen labels each of these outcomes distinctly.
