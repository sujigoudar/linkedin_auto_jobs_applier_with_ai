# Complete configuration field and workflow workbook

No field below grants authority by itself. See subaction_permissions.json and exact service validations. All default financial values remain inherited or unset unless part of an isolated research draft.

## F-IDENTITY: Commercial sign-in / sign-up

Choose sign in or sign up → Enter credentials → Terms for signup → Submit

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| email | Email | email | True | None | valid email;max254;normalize domain not provider-specific aliases | Used by the configured identity provider |
| password | Password | password | True | None | configured identity-provider policy;max1024 bytes;allow Unicode and password managers | Never log or persist in app state |
| terms_version | Terms version | id | False | None | server-supplied approved document ID;required on signup | Version accepted is stored as evidence |
| accept_terms | Accept terms | boolean | False | false | required true for signup only | Does not authorize trading |
| return_route | Return destination | route_key | False | customer_overview | server route-key allowlist only | No external redirect accepted |

**save_effect:** Create/verify identity through existing provider; create tenant/membership only after validated identity

**preview_effect:** No financial preview

**confirm_effect:** Create session or verification-pending state; never select/pay/copy

**rules:** No email enumeration. Bound attempts by trusted client and identity; signup uses bot defenses only when implemented accessibly.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-RECOVERY: Credential recovery / MFA

Validate challenge → Verify factor → Set new credential → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| challenge_id | Challenge | challenge | True | None | short-lived;origin/audience/action bound;single use | Issued server-side |
| verification_code | Verification code | challenge | False | None | provider-specific length;paste allowed | Use provider challenge verifier |
| new_password | New password | password | False | None | provider policy;max1024 bytes;only password-reset mode | Do not echo value |
| revoke_other_sessions | Revoke other browser sessions | boolean | False | true | actual boolean | Does not stop worker management |

**save_effect:** No raw challenge saved to drafts

**preview_effect:** Show session consequences only

**confirm_effect:** Provider-verified credential change and session epoch update

**rules:** Recovery cannot change mandate scope; expired step-up returns to requesting action without automatic replay.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-ELIGIBILITY: Residence and service eligibility

Identity facts → Requested service → Documents → Decision

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| residence_country | Country of residence | country | True | None | ISO country supported by approved policy | User fact, not approval |
| tax_residence | Tax residence | country_list | False | None | unique ISO countries;policy decides requirement | Not inferred from IP |
| customer_type | Customer type | enum:individual,entity | True | individual | allowed by service approval | Entities need their actual approved onboarding flow |
| service_modes | Requested services | enum_list:research,alerts,copying,managed_program | True | None | nonempty;published compatible services only | Payment and mandate gates remain separate |
| document_versions | Acknowledged documents | id_list | True | None | exact server-published versions | Document acceptance timestamp persisted |
| facts_confirmed | I confirm these facts | boolean | True | false | required true | No eligibility flags accepted from browser |

**save_effect:** Persist submitted facts and consent versions

**preview_effect:** Evaluate approved eligibility policy without charging or creating mandate

**confirm_effect:** Record fact submission; server result eligible/pending/unsupported with reason

**rules:** Residence change invalidates affected service eligibility and triggers review; does not silently orphan open obligations.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-SELECTION: Select a portfolio

Choose product/version → Eligibility → Plan capacity → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| product_id | Product | id | True | None | published and audience-visible | Draft IDs rejected |
| portfolio_version_id | Version | id | True | None | belongs to product;released | Version binding preserved |
| subscription_id | Subscription | id | False | None | owned and entitlement-compatible | required for paid access only |
| start_mode | Start mode | enum:new_entries_only | True | new_entries_only | only new_entries_only here | Existing-position sync is a separate mandate flow |

**save_effect:** Persist selection draft

**preview_effect:** Show entitlement/rights/capacity and missing gates

**confirm_effect:** Create scoped selection only; never publish or trade

**rules:** Duplicate selection returns existing record; version material change requires fresh preview.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-REPORT: Report / export request

Scope → Period/book → Format → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| object_ids | Scoped objects | id_list | True | None | server-authorized nonempty IDs;no all-tenants wildcard | Scope is immutable in job |
| period_start | Start | datetime | True | None | UTC instant;before end;within retention | User timezone converted explicitly |
| period_end | End | datetime | True | None | after start;not future for actual report | End-exclusive interval documented |
| book | Book | enum:actual,model,platform,source,business | True | actual | supported for caller and report;business never investment | Do not merge origins |
| currency | Reporting currency | currency | False | None | supported conversion policy or show separate currencies | Reference rate basis shown |
| format | Format | enum:csv,json,pdf | True | csv | server supports generation;PDF only after renderer implemented | No formula injection in CSV |
| include_sensitive | Include sensitive fields | boolean | False | false | only explicitly permitted by current scope | Secrets always excluded |

**save_effect:** Persist export definition

**preview_effect:** Show coverage, cost basis and rights without file generation

**confirm_effect:** Start bounded export job; reauthorize at download

**rules:** No public unapproved hypothetical export; expiring download token scoped to principal and record; failed job not empty report.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-CONNECTION: Platform connection

Choose platform → Mode → Eligibility → Hosted consent → Verify account

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| platform | Platform | enum:collective2,etoro,copyfactory,broker_native | True | None | configured and service-eligible | No inference of provider program approval |
| environment | Environment | enum:demo,live | True | demo | live needs distinct approved flow;C2 test strategy is not blanket sandbox | Displayed at every step |
| account_selection | Returned account | id | False | None | only IDs in server-verified provider response | Cannot type arbitrary account ID |
| connection_label | Label | text | True | None | 1..80 plain-text characters | No secrets in labels |
| consent_version | Connection consent | id | True | None | approved current document ID | Connection is not mandate |

**save_effect:** Persist connection draft and server-held auth state

**preview_effect:** Verify account/scopes using allowed read methods

**confirm_effect:** Save verified connection metadata; tokens remain encrypted server-side

**rules:** No live financial API calls during connection verification; OAuth/PKCE only where actual platform supports it; unsupported platform method visible.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-MANDATE: Copy mandate

Select version and account → Constraints → Start mode → Preview → Consent and confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| selection_id | Portfolio selection | id | True | None | owned and eligible | Locked in preview |
| connection_id | Connection | id | True | None | owned;current verified scope/mode | Account cannot change after preview |
| allocation_amount | Allocation amount | decimal | True | None | positive within released min/max and verified capacity | Currency separate, no browser sizing |
| allocation_currency | Currency | currency | True | None | matches approved account profile | Cross-currency unsupported ->blocked |
| max_trade_risk | Maximum per-trade risk | decimal | False | None | only values within released account/product ceiling | No guessed default |
| max_loss | Loss limit | decimal | False | None | unit and period from approved profile | required when profile requires |
| start_mode | Start mode | enum:new_entries_only,sync_existing | True | new_entries_only | sync_existing requires separate fresh price/exposure preview | No historical replay |
| policy_version_id | Policy | id | True | None | released and compatible with portfolio/account | No editable raw algorithm |
| consent_version | Mandate document | id | True | None | exact current approved version | Payment is not consent |
| acknowledge_scope | Confirm account and scope | boolean | True | false | required true only at confirmation | Step-up is separate challenge |

**save_effect:** Persist mandate draft without effect

**preview_effect:** Read current gates/positions/price and return expiring frozen action preview

**confirm_effect:** Enqueue approved activation through scoped publisher; return operation not instant success

**rules:** Rights, entitlement, mandate, environment, platform and risk all rechecked at effect boundary. No actual user signing through design tooling.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-WINDDOWN: Copy pause, exit and handoff

Choose safety action → Inspect cohort → Preview obligations → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| mandate_id | Mandate | id | True | None | owned and existing | Can be accessible during valid wind-down after billing expiry |
| action | Action | enum:pause_new_entries,drain,close_owned_cohort,request_handoff,revoke | True | pause_new_entries | operation compatible with mandate and platform | No general account flatten |
| cohort_revision | Owned cohort revision | revision | True | None | fresh server-issued immutable cohort | No newly opened/manual positions added |
| reason | Reason | text | True | None | 1..500 plain-text characters | Audit only |
| acknowledge_remaining | Acknowledge remaining obligations | boolean | True | false | required for non-pause actions | No claim closed until confirmed |

**save_effect:** Persist requested disposition

**preview_effect:** Compute outstanding obligations and executable quantities

**confirm_effect:** Enqueue exact approved scope; UNKNOWN stays reconciling

**rules:** Billing cancellation never implicitly calls this action. Platform revocation may require external handoff; show actual outcome.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-BILLING: Subscription checkout or change

Plan and interval → Costs/terms → Hosted provider → Return and verify

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| price_version_id | Price | id | True | None | server allowlist;approved for audience and mode | No client-supplied charge amount |
| interval | Interval | enum:month,year | True | month | matches price version | Actual proration from processor |
| subscription_id | Subscription | id | False | None | owned;required for changes | No other-customer billing ID |
| change_mode | Change | enum:start,upgrade,downgrade,cancel | True | start | current subscription transition valid | No financial-account effect |
| return_route | Return destination | route_key | True | billing | server allowlist | No arbitrary external redirect |
| accept_billing_terms | Accept terms | boolean | True | false | required at change confirmation | No stored card input in app |

**save_effect:** Store change intent

**preview_effect:** Show canonical processor quote or clearly unavailable quote

**confirm_effect:** Create hosted checkout/portal session in approved mode; webhook confirms canonical state

**rules:** HTTP redirect success does not grant access. Missing merchant approval blocks live session but not local workflow implementation.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-DELIVERY: Delivery preferences

Destinations → Categories → Timezone/schedule → Verification

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| email | Email destination | email | False | None | verified identity-owned email or verification workflow | No blind forward to arbitrary recipient |
| webhook_endpoint_id | Webhook destination | id | False | None | owned preverified endpoint;SSRF-safe registry | Raw URL uses separate secure validation |
| categories | Categories | enum_list:entry,update,exit,safety,billing,marketing | True | None | known category IDs | Safety follows agreed policy, not marketing toggle |
| timezone | Timezone | timezone | True | UTC | IANA zone | Schedule uses local wall time with DST rules |
| quiet_start | Quiet hours start | time | False | None | HH:mm plus explicit wrap behavior | Safety exceptions shown |
| quiet_end | Quiet hours end | time | False | None | required with quiet_start | Digest never revives expired entries |
| marketing_consent | Marketing consent | boolean | False | false | separate optional consent | No preselected consent |

**save_effect:** Persist preferences with revision

**preview_effect:** Show effective channel policy and safety exceptions

**confirm_effect:** Save preference version; verification/test jobs only when explicitly requested

**rules:** Test message labeled TEST and cannot be routed to financial engine. Endpoint private networks/redirects not allowed.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-PREFERENCES: Profile/security/display

Profile → Display → Sessions → Privacy

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| display_name | Display name | text | False | None | 0..80 plain-text characters | Not published by default |
| timezone | Timezone | timezone | True | UTC | IANA zone | UTC retained in stored events |
| theme | Theme | enum:system,dark,light | True | system | known values only | No custom CSS |
| density | Density | enum:comfortable,compact | True | comfortable | compact target sizes remain accessible | Never hide safety notices |
| number_locale | Number locale | locale | True | en-US | supported locale | Stored amounts remain decimal strings |
| view_currency | Preferred currency | currency | False | None | display only;no implicit conversion without data | No account currency change |
| reduce_motion | Reduce motion | enum:system,on | True | system | never override user reduced-motion preference off | No blinking tickers |

**save_effect:** Persist profile/display preferences

**preview_effect:** Show display-only diff

**confirm_effect:** Commit user preference version

**rules:** Session revocation, deletion and data export use separate audited action contracts; no arbitrary JSON settings blob.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-SUPPORT: Support case

Category → Affected record → Details → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| category | Category | enum:billing,delivery,connection,performance,safety,access | True | None | known category | Safety priority triaged separately |
| related_object_id | Related record | id | False | None | caller can access object | No cross-tenant IDs |
| subject | Subject | text | True | None | 1..120 characters | Plain text |
| description | Description | text | True | None | 1..8000 characters;inert rendering | Do not enter passwords or API keys |
| attachment_ids | Attachments | id_list | False | None | owned quarantine-scanned objects;max5 | max10MiB each;CSV/PNG/JPEG/PDF;no executable HTML |

**save_effect:** Persist scoped case

**preview_effect:** Show redaction/attachment scan status

**confirm_effect:** Create case and approved notification; no financial command

**rules:** Staff scope least privilege; private execution secrets unavailable; customer may submit before subscribing.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-MANAGED-REQUEST: Managed-account dealing request

Program → Broker convention → Amount → Cutoff → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| program_id | Program | id | True | None | owned participant in approved broker-native program | No local pooled account |
| request_type | Request | enum:broker_onboarding,subscription,redemption,statement | True | None | allowed by program | Requests are not transfers |
| amount | Amount | decimal | False | None | required positive for cashflow requests;program bound | No dollar defaults |
| currency | Currency | currency | False | None | required with amount | Matches program |
| cutoff_id | Dealing cutoff | id | False | None | server-issued;still open | Late requests roll by approved rule |
| mandate_version | Mandate | id | True | None | current approved participant mandate | Explicit request consent |

**save_effect:** Persist request draft

**preview_effect:** Show broker schedule, fees, restrictions and open-exposure effect

**confirm_effect:** Submit only through approved broker program flow; otherwise record awaiting owner action

**rules:** No Stripe cash deposits, invented NAV or fee arithmetic. Missing convention blocks dealing.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-API-ACCESS: Scoped API delivery access

Scope → Expiry → Destination → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| label | Key label | text | True | None | 1..80 plain text | Not a secret |
| scopes | Scopes | enum_list:alerts_read,reports_read,delivery_receive | True | None | subset of entitlement and rights | No trading/admin scope |
| expires_at | Expiry | datetime | True | None | future within server maximum | No never-expire by default |
| destination_id | Delivery endpoint | id | False | None | owned validated endpoint | No arbitrary outbound URL |

**save_effect:** Persist requested access metadata

**preview_effect:** Show allowed subset/quota and expiry

**confirm_effect:** Generate scoped secret once only; store hash/server secret as appropriate

**rules:** Key reveal not in audit/export/cache. Revocation idempotent and affects reads/delivery only.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-BROKER: Private brokerage account

Adapter → External identity → Credential references → Products → Read checks → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| account_label | Account label | text | True | None | 1..80 plain text | No credentials |
| adapter_id | Adapter | id | True | None | reviewed registry ID | No module import string |
| venue_id | Venue/API variant | id | True | None | supported adapter configuration | CCXT exchange not inferred |
| external_account_ref | Broker account | id | True | None | verified by read-only connection | Masked in general UI |
| environment | Environment | enum:simulation,paper,live | True | simulation | actual provider mapping required | No fake broker paper |
| credential_ref | Secret reference | secret_ref | False | None | preprovisioned scoped vault reference | Secret values not in this form |
| products | Products | id_list | True | None | qualified exact product profiles | No all-assets default |
| position_mode | Position mode | enum:netting,hedged,spot | True | None | verified account setting | No implicit mode change |
| max_exposure | Notional ceiling | decimal | False | None | from owner-approved account policy | No auto increase |
| entry_enabled | Enable new entries | boolean | True | false | true only separate release gates | Save never enables trading |

**save_effect:** Save inactive account draft

**preview_effect:** Read identity, scopes, positions and capability evidence

**confirm_effect:** Apply approved inactive configuration; activation separate operation

**rules:** Account deletion becomes drain/handoff workflow. Credential rotation separate qualified workflow; no GET side effects.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-SOURCE: Signal provider and parser

Transport → Source identity → Permissions → History → Parser → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| transport_instance_id | Transport | id | True | None | authorized shared collector instance | No duplicate bot for same transport unless required |
| provider_id | Provider | id | True | None | stable registered source identity | Not just telegram/discord |
| channel_product_id | Channel/product | text | True | None | stable external source identity;max200 | Preserve channel-level lineage |
| analyst_mapping | Analyst mapping version | id | True | None | reviewed deterministic mapping | No ambiguous display-name matching |
| allowed_products | Asset profiles | id_list | True | None | explicit source scope | No default all crypto |
| parser_version_id | Parser version | id | True | None | reviewed parser registry | No arbitrary code |
| rights_grant_id | Rights grant | id | False | None | required for selected commercial/model uses | Personal receipt not resale grant |
| history_start | History start | datetime | False | None | authorized range;before end | Do not invent original revisions |
| history_end | History end | datetime | False | None | not future;paired range | Save does not start import |
| entry_enabled | Enable admissions | boolean | True | false | activation separate reviewed transition | Management of old trades separate |

**save_effect:** Create real source draft

**preview_effect:** Validate access metadata and parser coverage

**confirm_effect:** Save draft or enqueue explicitly authorized history read job

**rules:** Import progress durable; ingestion state separate from live admissions; edits/replies/cancellations included.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-ROUTING: Routing rule

Source match → Destination scope → Conflict preview → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| source_id | Source | id | True | None | registered source | Exact provider identity |
| analyst_ids | Analysts | id_list | False | None | within source | Null=all authorized analysts, not all providers |
| product_ids | Products | id_list | True | None | explicit qualified scope | Not ticker heuristic |
| instrument_filter | Instrument filter | id_list | False | None | verified instruments | No regex code execution |
| destination_ids | Destinations | id_list | True | None | distinct owned accounts | Duplicate rules do not duplicate intents |
| priority | Priority | integer | True | 100 | 0..10000 deterministic conflict rule | No silently conflicting overrides |
| entry_enabled | New entries | boolean | True | false | true only with release | Exit ownership stays on original account |

**save_effect:** Persist rule draft

**preview_effect:** Evaluate stored non-executing event set and show per-destination dedup

**confirm_effect:** Commit reviewed configuration revision

**rules:** Preview never calls broker write. Change diff declares future-entry only versus independently approved active transition.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-POLICY: Management policy draft

Scope → Sizing → Stop → Targets/trail → Holding → Preview

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| policy_name | Name | text | True | None | 1..100 plain text | Version immutable after release |
| product_profile_id | Product profile | id | True | None | exact currency/quantity/trigger semantics | No share math for options |
| size_mode | Sizing mode | enum:risk,fixed_units,fixed_notional,source_scaled | True | risk | supported released family | All modes bounded by hard limits |
| risk_budget | Risk budget | decimal | False | None | explicit approved currency/percentage unit | required for risk mode |
| fixed_units | Fixed units | decimal | False | None | legal positive step;hard-cap bound | required only fixed_units |
| fixed_notional | Fixed notional | decimal | False | None | positive currency value | required only fixed_notional |
| stop_recipe_id | Fallback stop recipe | id | False | None | released compatible recipe | Null means stopless signal cannot be resolved without provider stop |
| target_policy_id | Target policy | id | True | None | released role/partial/basis definitions | No invented provider target |
| trail_recipe_id | Trail recipe | id | False | None | released non-loosening recipe | No trailing automatic activation by price default |
| holding_policy_id | Holding rule | id | True | None | finite applicable strategy/product deadlines | Includes session coverage |
| apply_to | Apply to | enum:future_entries | True | future_entries | existing allocations require separate transition | No silent retrofit |

**save_effect:** Save draft policy only

**preview_effect:** Resolve a complete plan with exact provenance on selected event

**confirm_effect:** Submit for qualified owner review; live release separate

**rules:** Unknown initial price or risk cannot bypass sizing. All configuration numbers validated on server with decimal/product units.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-POSITION-ACTION: Position action

Select owned allocation → Operation → Quantity/floor → Preview → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| allocation_id | Allocation | id | True | None | owned and exact instrument/environment | Not symbol-only |
| operation | Action | enum:reduce,close_owned,raise_protection,reconcile | True | None | supported exact route recipe | Risk reduction still quantity checked |
| quantity | Quantity | decimal | False | None | required for reduce;legal step;<=available after possible fills | No percentage without original/remaining basis |
| new_floor | New protective threshold | decimal | False | None | required for raise_protection;must not loosen activated policy | Price instrument/basis fixed by plan |
| reason | Reason | text | True | None | 1..500 plain text | Audit |

**save_effect:** Save action draft, not reservation

**preview_effect:** Read complete positions/order families and return expiring scoped preview

**confirm_effect:** Durably claim and dispatch through sole writer only after revalidation

**rules:** Unknown same action cannot resubmit. Stop and TP orders may both execute without actual venue cap; constraints use maximum possible close quantity.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-INCIDENT: Incident management

Evidence → Responsibility → Containment → Resolution

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| incident_id | Incident | id | True | None | within authorized scope | Persisted incident |
| operation | Operation | enum:acknowledge,assign,reconcile,propose_resolution | True | acknowledge | explicit per-role permission | No execute arbitrary command |
| assignee_id | Assignee | id | False | None | active role eligible for incident | Cannot assign financial authority |
| note | Note | text | True | None | 1..2000 plain text | No secrets |
| evidence_ids | Evidence | id_list | False | None | scoped immutable objects | required for resolution proposal |

**save_effect:** Save comment/action intent

**preview_effect:** Show affected scope and proposed operation

**confirm_effect:** Acknowledge/assign/readback job or submit resolution review

**rules:** Acknowledge never marks resolved. Reconcile is read-only broker action unless separately approved corrective command.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-RECOVERY-OPS: Recovery review

Scope → Artifact/data → Old writer fencing → Reconciliation → Approvals

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| service_role | Role | enum:private_writer,publisher,api,research | True | None | operator allowed for role | Website role not trading permission |
| source_site_id | Old site | id | True | None | registered site | No arbitrary SSH host |
| target_site_id | Target site | id | True | None | qualified distinct site | Standby initially incapable of effects |
| release_manifest_id | Release | id | True | None | verified immutable manifest | No mutable latest tag |
| backup_generation_id | Data generation | id | True | None | verified compatible restore | Known RPO/unknown commands visible |
| fencing_evidence_ids | Fencing evidence | id_list | False | None | required before writer promotion | Lease expiry is insufficient |
| reconciliation_evidence_id | Reconciliation | id | False | None | required for promotion;fresh and complete | Positions alone may not recover ownership |

**save_effect:** Persist review checklist

**preview_effect:** Evaluate required evidence; no infrastructure effects

**confirm_effect:** Prepare signed release/handoff card; no unattended promote from GUI in this scope

**rules:** Only separate authorized deployment runbook applies effect. Default no auto failover and no storing root credentials in UI.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-RIGHTS: Rights-grant evidence

Source → Allowed uses → Audience/channel → Term → Evidence → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| source_id | Source | id | True | None | registered source/product | Exact rights counterparty |
| uses | Uses | enum_list:private_trading,research,redistribution,model_processing | True | None | explicit written grant scope | Combining signals does not grant rights |
| jurisdictions | Jurisdictions | country_list | True | None | explicit approved audience | No implicit worldwide |
| channels | Channels | id_list | True | None | approved destinations | No arbitrary all-channel |
| assets | Asset profiles | id_list | True | None | covered by agreement | Exact use scope |
| effective_at | Effective | datetime | True | None | UTC;before expiry | No retroactive fake permission |
| expires_at | Expires | datetime | True | None | after effective;ongoing requires separately approved convention | Expiration evaluated at effect |
| evidence_ids | Contract/evidence | id_list | True | None | private reviewed immutable documents | No public raw contract |
| attribution_policy_id | Attribution | id | True | None | reviewed policy | Trade secrets/private sources respected |

**save_effect:** Create draft grant and evidence records

**preview_effect:** Show intersection and affected products/obligations

**confirm_effect:** Submit or record owner-approved decision with audit; researcher cannot approve

**rules:** Revocation triggers scoped wind-down review, not instant deletion of history or abandonment of existing management.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-SLEEVE: Strategy sleeve

Lineage → History → Product → Management → Qualification

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| provider_id | Provider | id | True | None | registered source | Rights checked separately |
| analyst_id | Analyst | id | True | None | verified mapping | Stable ID |
| strategy_id | Strategy | id | True | None | reviewed strategy identity | Not arbitrary marketing label |
| parser_version_id | Parser | id | True | None | reviewed exact version | No latest alias |
| policy_version_id | Management policy | id | True | None | compatible released recipe | Original risk semantics retained |
| product_profile_id | Product | id | True | None | verified instrument family | No mixed financial math |
| dataset_version_id | History | id | False | None | required for research eligibility, not saving draft | No fabricated performance |
| cluster_id | Exposure cluster | id | False | None | reviewed context label | Does not merge source events |

**save_effect:** Create real sleeve draft

**preview_effect:** Report rights/data/capacity blockers

**confirm_effect:** Save qualified status only from evidence-backed evaluation

**rules:** Draft with no history permitted. History study blocks until data, never blocks form construction.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-RESEARCH: Portfolio research run

Universe → Candidates → Capital constraints → Data split → Costs → Budget → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| universe_version_id | Universe | id | True | None | rights-qualified version | Whole approved universe enumerated |
| recipes | Recipes | enum_list:equal_capital,inverse_volatility,hrp,min_cvar | True | None | implemented deterministic recipes | No runtime LLM required |
| subset_min | Minimum sleeves | integer | True | 2 | >=1 and<=subset_max | Research-only constraint |
| subset_max | Maximum sleeves | integer | True | 5 | <=universe size and approved job cap | No silently reduced search |
| cash_bps | Cash allocation | integer | True | 1500 | 0..10000 and existing research policy | Not live account allocation |
| max_sleeve_bps | Sleeve ceiling | integer | True | 3500 | 0..10000 and feasible | Weights plus cash exactly10000 |
| max_cluster_bps | Cluster ceiling | integer | True | 5000 | 0..10000 and approved study | No hidden concentration |
| train_sessions | Training sessions | integer | True | 252 | positive;available window sufficient | Chronological only |
| test_sessions | Test sessions | integer | True | 63 | positive;nonoverlapping holdout | No hindsight information |
| holdout_fraction | Holdout fraction | decimal | True | 0.20 | strictly0..1;lock dataset before search | No repeated tuning on holdout |
| cost_scenario_ids | Costs/stress | id_list | True | None | verified available scenarios | No zero-cost assumption |
| resource_profile_id | Resources | id | True | None | approved memory/time/disk budget | Cannot starve execution |

**save_effect:** Persist complete study definition

**preview_effect:** Compute full candidate denominator, data gaps, resource quote and immutable manifest

**confirm_effect:** Enqueue research job only; not publish/weight live portfolio

**rules:** Defaults inherited research drafts only, not evidence of optimal parameters; infeasible candidates retained with reasons.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-PRODUCT: Product and portfolio version

Identity → Sleeves/weights → Channels/audience → Evidence → Disclosures → Preview

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| product_name | Product name | text | True | None | 1..100 plain text | No unsupported promotional claims |
| slug | Public slug | slug | True | None | unique lower-case URL slug | Draft slug not public |
| portfolio_version_id | Existing version | id | False | None | editable draft only | Released changes create new version |
| sleeve_weights | Sleeve weights | weight_map | False | None | nonnegative integer bps;plus cash10000 when complete | Draft can be incomplete |
| cash_bps | Cash weight | integer | True | 10000 | 0..10000;weight conservation before eligibility | New empty draft all cash is not a trading recommendation |
| service_modes | Modes | enum_list:alerts,copying,managed_program | True | None | rights/platform/legal intersections | No auto-enable unsupported mode |
| audience_policy_id | Audience | id | False | None | required before release | No inferred jurisdictions |
| research_report_id | Evidence | id | False | None | required for claims;can save without | No invented track record |
| methodology_document_id | Methodology | id | False | None | required before public publication | Versioned approved content |

**save_effect:** Create real draft rows even without completed research

**preview_effect:** Render private no-store audience-safe preview with missing-data messages

**confirm_effect:** Submit version for review; not directly publish

**rules:** A version with no sleeves/evidence may be saved but cannot pass release or appear publicly. State badges separate edited, validated, approved, published.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-RELEASE: Release review

Object/hash → Evidence → Gates → Audience → Independent review → Decision

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| object_version_id | Version | id | True | None | immutable current review target | Exact hash shown |
| evidence_manifest_id | Evidence | id | True | None | complete mapped tests/rights/data/report | Structural hashes not truth by themselves |
| audience_policy_id | Audience | id | True | None | approved exact service/channel/geography | No broadened audience |
| scheduled_at | Effective time | datetime | False | None | future within approval validity | Open allocations retain original binding |
| decision | Decision | enum:request_changes,reject,approve | True | request_changes | per-role reviewer authorization | Cannot approve own proposal where independence required |
| reason | Decision reason | text | True | None | 1..4000 characters | Audit evidence |

**save_effect:** Record review notes

**preview_effect:** Recompute all gates and display changes since proposal

**confirm_effect:** Persist decision after fresh step-up; publication is separate

**rules:** No checkbox declares legal advice complete; evidence-bound approvals only. Financial permission cannot arise from role switch in same identity.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-PUBLISHER: Publisher destination

Platform → External strategy → Identity/scope → Capabilities → Qualification

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| platform | Platform | enum:collective2,etoro,copyfactory,broker_native | True | None | implemented approved adapter | Not proprietary SignalStack self-hosting |
| external_strategy_id | External strategy | id | True | None | read-verified account strategy | Unique financial authority binding |
| environment | Mode | enum:local_simulation,external_test,demo,live | True | local_simulation | vendor-specific evidence;C2 external_test not sandbox | No subscribers/autotrade for test without verification |
| credential_ref | Credential reference | secret_ref | False | None | scoped preprovisioned reference | Never disclose key |
| capability_manifest_id | Capabilities | id | False | None | required before external action | Exact platform version/evidence |
| publication_mode | Mode | enum:api_strategy_publisher,approved_master_copy | True | api_strategy_publisher | one approved path per external account/strategy | No double publisher |

**save_effect:** Save inactive destination

**preview_effect:** Run read-only identity/capability checks where allowed

**confirm_effect:** Request qualification/release; not send signal

**rules:** No real C2 test request without external explicit authorization; unsupported operations stay blocked while local protocol tests exercise all outcomes.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-PUBLISH-ACTION: Publisher recovery/correction

Intent → Current external state → Allowed operation → Preview → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| intent_id | Intent | id | True | None | scope-owned existing intent | Never fabricate new identity for retry |
| operation | Operation | enum:reconcile,propose_price_change,propose_cancel | True | reconcile | supported exact recipe | Quantity changes cannot masquerade as price edit |
| new_price | Proposed price | decimal | False | None | required only valid price modification | Instrument/tick/side validation |
| reason | Reason | text | True | None | 1..2000 characters | Audit |

**save_effect:** Record correction proposal

**preview_effect:** Recheck external family, rights and permitted mutation

**confirm_effect:** Enqueue approved operation with durable identity; no blind retry

**rules:** Cohort unchanged after unknown send; new subscribers receive no late old entry through repair.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-PRICE: Price and entitlement draft

Product/SKU → Interval/currency → Entitlements → Processor mode → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| sku | SKU | slug | True | None | immutable unique product plan key | No arbitrary client prices |
| currency | Currency | currency | True | USD | supported merchant approved currency | Currency exponent from currency metadata |
| amount_minor | Amount in minor units | integer | True | None | >=0 within approved test/live pricing | Not float cents |
| interval | Interval | enum:month,year | True | month | approved plan interval | No hidden renewal |
| portfolio_limit | Portfolio count | integer | True | None | nonnegative;defined unlimited only as explicit policy | No unexplained null unlimited |
| features | Feature IDs | id_list | True | None | reviewed entitlement registry | No implicit trading right |
| mode | Mode | enum:test,live | True | test | live requires merchant/owner price approval | No auto paid launch |

**save_effect:** Create test-mode price version

**preview_effect:** Show total costs, entitlement diff and active subscriber impact

**confirm_effect:** Submit approved processor price request only in explicit mode

**rules:** Existing subscriptions bind original price/version; downgrade has effective date and safe open-obligation treatment.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-MANAGED-PROGRAM: PAMM/MAM program config

Broker agreement → Allocation → Dealing → NAV/fees → Audience → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| program_name | Name | text | True | None | 1..100 plain text | No implication launched |
| broker_program_id | Broker program | id | True | None | broker-verified exact program | No internally invented pool |
| mode | Structure | enum:pamm,mam | True | None | approved contract semantics | Not interchangeable accounting |
| allocation_policy_id | Allocation policy | id | True | None | precommitted deterministic rule | No after-outcome selection |
| nav_policy_id | NAV policy | id | True | None | broker-approved generation/restatement convention | No incomplete estimate sold as NAV |
| dealing_schedule_id | Dealing schedule | id | True | None | cutoffs/timezone verified | No browser wall-clock authority |
| fee_policy_id | Fee policy | id | False | None | approved cashflow/HWM convention | Performance fees disabled without full convention |
| agreement_evidence_ids | Evidence | id_list | True | None | rights/legal/broker approvals | No automated signing |

**save_effect:** Save inactive program

**preview_effect:** Validate completeness and unresolved conventions

**confirm_effect:** Submit program release review, not custody or deposit action

**rules:** UI built even when approvals absent; real cash handling remains through broker.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-DEALING: Dealing and allocation review

Program/generation → Requests → Allocation → Evidence → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| program_id | Program | id | True | None | approved scoped program | No generic account multiplier |
| nav_generation_id | NAV generation | id | True | None | verified current broker generation | Immutable |
| request_ids | Requests | id_list | True | None | eligible cutoff cohort | No add after allocation outcome known |
| allocation_manifest_id | Allocation | id | True | None | deterministic policy and tie-break bound | Conserves units/amounts |
| reason | Reason | text | True | None | 1..2000 plain text | Required audit |

**save_effect:** Save review

**preview_effect:** Show broker-defined allocation/fees/rounding and unresolved conditions

**confirm_effect:** Prepare approved broker instruction or await external authority

**rules:** No local invented transfer. Corrected NAV causes new generation and revised report, not overwrite.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-ACCESS: Commercial role membership

Identity → Role → Scope → Expiry → Confirm

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| user_id | User | id | True | None | verified existing or invited identity | No arbitrary JWT claims |
| role | Role | enum:researcher,reviewer,publisher_operator,billing_operator,support_readonly | True | None | explicit named role | Owner grants not through self-service form |
| scope_ids | Scope | id_list | True | None | minimum necessary objects/tenants | No implicit global scope |
| expires_at | Expiry | datetime | False | None | required for temporary support grant | Current time server-side |
| purpose | Purpose | text | True | None | 1..1000 characters | Audited access |

**save_effect:** Save proposed grant

**preview_effect:** Display exact read and effect capabilities

**confirm_effect:** Apply owner-authorized membership and session invalidation as appropriate

**rules:** Customer cannot access admin role route; support impersonation and private-secret access absent.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-INTEGRATION: Integration configuration

Registry → Purpose → Mode → Entitlement → Credentials → Quotas

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| provider_registry_id | Provider | id | True | None | reviewed allowlist | No arbitrary service URL |
| purpose | Purpose | enum:research,quotes,reference,publication,billing,monitoring | True | None | approved provider use | Research credentials have no trade powers |
| environment | Environment | enum:test,demo,live | True | test | purpose-specific mapping | Explicit live approval |
| credential_ref | Secret reference | secret_ref | False | None | role-scoped vault reference | No secret echo |
| entitlement_evidence_id | Entitlement/use terms | id | False | None | required for selected data use | Free software is not free data rights |
| quota_profile_id | Quota/cost profile | id | True | None | known request weights/reset/cost cap | No paid fallback |

**save_effect:** Save inactive config

**preview_effect:** Permitted read-only check and quota forecast

**confirm_effect:** Apply approved configuration only

**rules:** Get/read endpoint must still enforce host/redirect authorization and payload limits.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-CONTENT: Approved public content

Document → Plain content → Audience → Sources → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| document_type | Type | enum:methodology,risk,billing_terms,privacy,help,status | True | None | known structured template | No arbitrary HTML |
| locale | Language | locale | True | en-US | supported language | Translations reviewed for factual consistency |
| title | Title | text | True | None | 1..120 plain text | No unsupported claims |
| body | Content | text | True | None | bounded20000 plain text or safe structured nodes | No scripts/iframes/event handlers |
| audience_policy_id | Audience | id | True | None | approved scope | No raw licenses |
| source_evidence_ids | Supporting evidence | id_list | False | None | required for factual performance claims | No model invented citations |

**save_effect:** Save private content draft

**preview_effect:** Render using same safe renderer under no-store private route

**confirm_effect:** Submit for review; publishing distinct approved command

**rules:** No unreviewed AI marketing text; consent signed on exact published document version.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-WORKSPACE: Workspace and saved views

Display → Navigation → Notifications → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| workspace_name | Workspace label | text | True | None | 1..80 plain text | No impersonating another entity |
| theme | Theme | enum:system,dark,light | True | system | known theme | Shared visual tokens only |
| density | Density | enum:comfortable,compact | True | comfortable | accessible minimum controls | Not a risk policy |
| visible_panel_ids | Optional panels | id_list | False | None | allowlisted current role panels | Mandatory safety/fees/origin cannot hide |
| column_order | Table columns | id_list | False | None | allowlisted;required identity columns retained | Move via buttons not drag-only |
| notification_route_id | Operations notifications | id | False | None | verified recipient/channel policy | Test notice non-actionable |

**save_effect:** Persist personal view or workspace draft

**preview_effect:** Show exact cosmetic scope and mandatory panels

**confirm_effect:** Save display configuration; financial configuration separate review

**rules:** No custom CSS/JS/SQL/plugin imports. Reordering accessible and versioned per principal/workspace/environment.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-SIZING-RECIPE: Sizing and portfolio-risk recipe

Identity and product scope → Sizing basis → Hard limits and reserves → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| recipe_name | Recipe name | text | True | None | max100;unique within account policy scope | Private draft only |
| product_profile_id | Product profile | id | True | None | verified compatible product schema | Defines units and multiplier |
| risk_basis | Sizing basis | enum:fixed_units,fixed_notional,loss_budget | True | None | must be allowed by the selected product profile | No algorithm inferred from a name |
| risk_amount | Per-trade risk amount | decimal | False | None | required for loss_budget;positive;at or below released ceiling | Currency or percent basis supplied by verified account policy |
| risk_currency | Risk currency | currency | False | None | required for money amount;must match approved basis | No implicit FX conversion |
| minimum_quantity | Minimum legal quantity | decimal | False | None | server instrument minimum;cannot raise quantity beyond risk | Smaller signals may be rejected |
| maximum_quantity | Maximum quantity | decimal | False | None | nonnegative compatible units;at or below account hard cap | Does not replace buying-power checks |
| account_limit_policy_id | Daily weekly exposure and loss limits | id | True | None | released server policy;customer cannot weaken it | Preview all applicable account and owner limits |
| allocation_policy_id | Analyst and concentration policy | id | True | None | compatible released overlap/reservation policy | Shared capital and pending orders remain counted |
| cost_stress_policy_id | Fees slippage and stress allowance | id | True | None | verified product-specific cost and stress convention | A stop price is not guaranteed loss |

**save_effect:** Save sizing recipe draft

**preview_effect:** Show all resolved quantities units caps and failure reasons on a selected signal

**confirm_effect:** Submit recipe for research/review only;do not alter live sizing

**rules:** No browser computation can size the order. Edit requires actual product metadata and inherited hard boundaries. Unknown risk or buying power remains a blocker.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-STOP-RECIPE: Initial-stop and fallback recipe

Stop meaning → Source precedence → Fallback parameters → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| recipe_name | Recipe name | text | True | None | max100 | Versioned internal recipe |
| reference_basis | Stop reference basis | enum:instrument_price,underlying_price,option_premium,combo_price | True | None | must match resolved source meaning and product | No automatic underlying-to-premium translation |
| provider_stop_policy | Provider stop treatment | enum:require_valid,use_valid_else_released_fallback | True | None | invalid/crossed supplied stop is not missing | Fallback only for genuinely absent source stop |
| fallback_method_id | Fallback calculation | id | False | None | released or research-only compatible method;required when selected | No invented percentage default |
| fallback_distance | Fallback distance parameter | decimal | False | None | units/bounds from method schema;positive where profile requires | Research draft until independently reviewed |
| volatility_window | Volatility lookback observations | integer | False | None | required only for compatible volatility recipe;integer>=2 | Availability and warmup explicitly evaluated |
| native_coverage_policy_id | Coverage and unprotected-window policy | id | True | None | qualified exact account product session recipe | No claimed continuous protection on unsupported route |
| missing_data_action | Missing or invalid data action | enum:block_new_entry | True | block_new_entry | cannot be relaxed by this form | Existing positions follow their separate recovery plan |

**save_effect:** Save initial-stop draft

**preview_effect:** Display provider/fallback provenance invalidation and attainable coverage

**confirm_effect:** Submit for reviewed policy release;no current stop move

**rules:** Stops with valid negative product prices require an explicit compatible profile;do not globally forbid or reinterpret them. Existing trade remains on its original policy.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-TRAIL-RECIPE: Trailing and profit-lock recipe

Activation → Distance and floor → Update constraints → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| recipe_name | Recipe name | text | True | None | max100 | Versioned draft |
| activation_rule_id | Activation condition | id | True | None | compatible reviewed rule;explicit trigger basis | A target can activate a trail only through policy |
| activation_value | Activation parameter | decimal | False | None | typed units and bounds supplied by rule | Missing value is not zero |
| trail_method_id | Trailing method | id | True | None | fixed percent price volatility or other implemented method ID | Only installed qualified formulas offered |
| trail_distance | Distance parameter | decimal | False | None | required by method;unit/bounds from recipe schema | Never an untyped generic percent |
| minimum_improvement_ticks | Minimum update improvement | integer | False | None | integer>=1;compatible tick metadata | Optimization cannot suppress a breached active threshold |
| update_cooldown_ms | Elective update cooldown in ms | integer | False | None | integer>=0;bounded by released policy | Urgent risk-reducing behavior has separate priority |
| never_loosen | Preserve activated protective floor | boolean | True | true | must remain true | Both long and short directions enforced server-side |
| replacement_recipe_id | Broker stop-update procedure | id | True | None | qualified same-order or cancel-replace procedure | Native high-water reset and unknown outcomes accounted for |

**save_effect:** Save trailing draft

**preview_effect:** Show candidate activated and broker-confirmed floors separately on replay

**confirm_effect:** Submit reviewed recipe;no live update from Save

**rules:** An unattainable candidate floor is not a breach. Every accepted change retains actual fills and outstanding close commitments;no app function assumes a bracket from its name.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-TARGET-RECIPE: Profit-target level and partial-exit recipe

Stable target identity → Meaning and level → Reduction basis → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| target_id | Target identity | id | True | None | stable within version;updates do not replace another target | Omitted means no change;remove is explicit operation |
| operation | Target operation | enum:add,update,remove | True | None | exact target/version scope | Removal never removes protective stop |
| role | Target role | enum:reference,hard_exit,partial_exit,trailing_trigger | False | None | required for add/update | Provider intent preserved unless released policy states otherwise |
| reference_basis | Trigger basis | enum:instrument_price,underlying_price,option_premium,combo_price | False | None | required for add/update;product-compatible | No raw price basis inference |
| level | Target price or trigger level | decimal | False | None | required for add/update;profile-valid domain | No invented level when source supplied none |
| reduction_basis | Reduction basis | enum:original_allocation,remaining_allocation,cumulative_goal | False | None | required for partial exit | Exact quantity-conserving semantics |
| reduction_fraction | Reduction fraction | decimal | False | None | required for partial exit;0<value<=1 | Server cumulative rounding avoids excess sales |
| rounding_policy_id | Legal quantity rounding | id | False | None | required for partial exit;verified instrument step | Small allocations may not support every trim |
| trail_recipe_id | Trail activated at level | id | False | None | required for trailing_trigger | Activating a trail is not automatically an immediate sale |

**save_effect:** Save explicit target operation

**preview_effect:** Show changed levels actual owned quantity possible closes and remaining coverage

**confirm_effect:** Submit target-policy review or separately authorized existing-position action

**rules:** Multiple target records form one ordered target set. Stable IDs distinguish revisions,omission,clear and deletion. A gap across several levels is processed through one coordinated exit manager.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-HOLD-RECIPE: Holding session and deadline recipe

Strategy horizon → Venue rules → Deadline and failure → Review

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| recipe_name | Recipe name | text | True | None | max100 | Versioned draft |
| holding_policy_id | Holding convention | id | True | None | implemented strategy horizon profile | No universal intraday cutoff for every strategy |
| maximum_hold_seconds | Maximum strategy hold | integer | False | None | required when profile uses elapsed duration;integer>0 | Not a market-open assumption |
| session_calendar_id | Exchange session calendar | id | True | None | verified product/venue calendar plus broker exceptions | Holidays DST and special sessions tested |
| overnight_allowed | Overnight permitted | boolean | True | false | cannot exceed released account/product permission | Closing market can make stops dormant |
| product_deadline_policy_id | Expiry notice and broker deadline | id | False | None | required for expiring/notice-sensitive products | Earliest applicable mandatory deadline wins |
| emergency_exit_recipe_id | Deadline and emergency handling | id | True | None | qualified route urgency/quantity procedure | Unknown prior order prevents blind duplicate exit |

**save_effect:** Save horizon recipe draft

**preview_effect:** Display actual current and future deadlines coverage windows and known venue restrictions

**confirm_effect:** Submit reviewed recipe;existing episodes preserve their deadline unless separately authorized

**rules:** A valid calendar does not prove the broker can create/cancel/execute every order at that time. Do not suppress valid existing-position management with entry-hour filters.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.

## F-BACKTEST: Historical copier replay

Sources/history → Products/policies → Capital/costs → Period → Scope and run

| Field | Label | Type | Required | Default | Validation | Help |
|---|---|---|---|---|---|---|
| dataset_version_ids | Source and market datasets | id_list | True | None | authorized immutable history snapshots | Missing originals and revisions remain visible |
| policy_version_ids | Policies | id_list | True | None | compatible baseline/candidate recipes | Do not change live policy |
| account_profile_id | Account profile | id | True | None | declared shared capital and product limits | No full capital independently assigned to each signal |
| period_start | Start | datetime | True | None | UTC before end;within dataset | Use information availability |
| period_end | End | datetime | True | None | UTC after start;not future | Full selected range |
| cost_scenario_ids | Costs/slippage | id_list | True | None | qualified fee/funding/latency assumptions | No silently zero costs |
| ambiguity_policy | Intrabar ambiguity | enum:bound_outcomes,require_finer_data | True | bound_outcomes | never select favorable path silently | Bounds labeled hypothetical |
| resource_profile_id | Resources | id | True | None | approved job limits | Worker isolated from protection |

**save_effect:** Persist reproducible replay draft

**preview_effect:** Resolve coverage/gaps/candidate count and estimated resources

**confirm_effect:** Enqueue replay job through research service only

**rules:** Backtest report persists and distinguishes outcomes, actual cost status, fills, episodes and incomplete data.

**conflict:** If-Match or expected_revision mismatch ->409 with current revision and non-secret field diff; preserve local draft; user resolves explicitly.

**autosave:** Display prefs only after500ms debounce; all other forms explicit Save draft; secrets/challenges never autosaved.

**idempotency:** Stable per logical submission key; retries join same operation; changed payload with same key ->409.
