# Authorization model

The complete real permission model, as implemented in
`app/services/permissions.py`. This is an explicit allow-list per action,
not a role hierarchy: an action string with no entry in `_ALLOWED` denies
**every** role, including OWNER. Comments in the source are clear about
this being a deliberate choice -- "owner can do everything a lower role
can" is exactly the kind of implicit assumption a future role addition
could silently acquire authority nobody reviewed, so it is not how this
system works.

## Roles

`MembershipRole` (`app/models/tenancy.py`) has one CUSTOMER role and six
staff/operator roles, matching `spec/docs/02_architecture_and_tenancy.md`'s
"Operator roles are separate memberships" list:

| Role | Nature |
|---|---|
| `OWNER` | Platform operator, full command authority. Granted only by direct database/founder action (`app/services/staff_access.py::GRANTABLE_ROLES` explicitly excludes it) -- never through the self-service staff-invite form. |
| `RESEARCHER` | Runs research jobs, drafts products/sleeves. |
| `REVIEWER` | Reviews/releases strategies, reads rights register, manages content documents and managed programs. |
| `PUBLISHER_OPERATOR` | Manages publisher destinations/integration configs, publishes intent, views deployment status. |
| `BILLING_OPERATOR` | Manages billing/pricing, views business economics. |
| `SUPPORT_READONLY` | Reads support cases and the incident register; explicitly **cannot** act on incidents or view broker credentials ("support cannot view broker credentials" -- `spec/docs/02_architecture_and_tenancy.md`). |
| `CUSTOMER` | Self-service tenant member; every `manage_own_*`/`view_own_*` action is scoped to this role alone. |

`invite_staff_member()`/`revoke_staff_member()` (`app/services/staff_access.py`)
are the only real grant/revoke paths for the five grantable staff roles
(`RESEARCHER`, `REVIEWER`, `PUBLISHER_OPERATOR`, `BILLING_OPERATOR`,
`SUPPORT_READONLY`); they structurally forbid granting `OWNER` through the
form, forbid revoking `OWNER` at all through it, and forbid a caller
revoking their own membership.

## The full action -> role matrix

Reproduced directly from `app/services/permissions.py::_ALLOWED` (each row
is one dashboard screen's own access list, per the inline comment tying it
to its `dashboard_spec/screens/*.md` source):

| Action | Allowed roles | Screen |
|---|---|---|
| `grant_rights` | OWNER | AD-02 |
| `release_strategy` | OWNER, REVIEWER | AD-05/AD-06 family |
| `publish_intent` | OWNER, PUBLISHER_OPERATOR | AD-10 |
| `manage_billing` | OWNER, BILLING_OPERATOR | AD-13 |
| `view_broker_credentials` | OWNER | -- |
| `run_research_job` | OWNER, RESEARCHER | AD-05 |
| `view_support_case` | OWNER, SUPPORT_READONLY | AD-11 |
| `view_own_customer_profile` | CUSTOMER, OWNER, SUPPORT_READONLY | -- |
| `manage_product_draft` | OWNER, RESEARCHER, REVIEWER | AD-07 |
| `view_rights_register` | OWNER, REVIEWER | AD-02 |
| `manage_sleeve_draft` | OWNER, RESEARCHER, REVIEWER | AD-03 |
| `view_operations_overview` | OWNER, RESEARCHER, REVIEWER, PUBLISHER_OPERATOR, BILLING_OPERATOR, SUPPORT_READONLY | AD-01 |
| `manage_own_eligibility` | CUSTOMER | ID-04 |
| `manage_staff_access` | OWNER | AD-16 |
| `manage_own_support_case` | CUSTOMER | CU-14 |
| `manage_publisher_destinations` | OWNER, PUBLISHER_OPERATOR | AD-09 |
| `manage_own_api_keys` | CUSTOMER | CU-16 |
| `view_business_economics` | OWNER, BILLING_OPERATOR | AD-12 |
| `view_customer_support_record` | OWNER, SUPPORT_READONLY | AD-11 |
| `manage_integration_configurations` | OWNER, PUBLISHER_OPERATOR | AD-17 |
| `manage_pricing` | OWNER, BILLING_OPERATOR | AD-13 |
| `manage_content_documents` | OWNER, REVIEWER | AD-19 |
| `manage_managed_programs` | OWNER, REVIEWER | AD-14 |
| `view_audit_log` | OWNER, REVIEWER | AD-18 |
| `export_evidence_manifest` | OWNER, REVIEWER | AD-18 |
| `manage_workspace_settings` | OWNER | AD-20 |
| `view_research_run_detail` | OWNER, RESEARCHER, REVIEWER | AD-05 |
| `view_publication_intent_detail` | OWNER, PUBLISHER_OPERATOR | AD-10 |
| `manage_own_portfolio_selections` | CUSTOMER | CU-02 |
| `manage_own_notification_preferences` | CUSTOMER | CU-12 |
| `manage_own_display_preferences` | CUSTOMER | CU-13 |
| `view_managed_operations` | OWNER, PUBLISHER_OPERATOR, REVIEWER | AD-15 |
| `view_deployment_status` | OWNER, PUBLISHER_OPERATOR | AD-22 |
| `manage_own_platform_connections` | CUSTOMER | CU-07/CU-08 |
| `manage_own_copy_mandates` | CUSTOMER | CU-09 |
| `view_own_customer_overview` | CUSTOMER | CU-01 |
| `view_own_selection_detail` | CUSTOMER | CU-03 |
| `view_integration_status` | OWNER, RESEARCHER | -- (PLATFORM-book execution telemetry, deliberately narrower than `view_operations_overview`) |
| `compare_research_candidates` | OWNER, RESEARCHER, REVIEWER | AD-06 |
| `view_incident_register` | OWNER, PUBLISHER_OPERATOR, SUPPORT_READONLY | AD-21 (read) |
| `manage_incidents` | OWNER, PUBLISHER_OPERATOR | AD-21 (write -- deliberately excludes SUPPORT_READONLY) |
| `view_own_alerts` | CUSTOMER | CU-04 |
| `view_own_activity_detail` | CUSTOMER | CU-05 |
| `view_own_performance` | CUSTOMER | CU-06 |
| `view_own_billing` | CUSTOMER | CU-11 |
| `view_own_managed_programs` | CUSTOMER | CU-15 |

## Notable design decisions visible in the matrix

- **View/write splits are deliberate and repeated.** `view_rights_register`
  (OWNER, REVIEWER) is separate from `grant_rights` (OWNER only) -- a
  reviewer can see the register without approving a grant. The same
  pattern repeats for `view_research_run_detail` vs `run_research_job`,
  `view_managed_operations` vs `manage_managed_programs`, and
  `view_incident_register` vs `manage_incidents`. In every case the
  narrower write-side action is a strict subset of the read-side role set.
- **`export_evidence_manifest` deliberately mirrors `view_audit_log`
  exactly** (OWNER, REVIEWER) -- "exporting a verbatim bundle of rows a
  role can already see is the same authority as viewing them, not a
  distinct, more sensitive command." No role denied the audit log itself
  can reach its export.
- **`view_integration_status` is deliberately narrower than
  `view_operations_overview`** -- it exposes real PLATFORM-book execution
  telemetry (the owner's own private trading), which
  PUBLISHER_OPERATOR/BILLING_OPERATOR/SUPPORT_READONLY have no legitimate
  reason to see, per `INTEGRATION_DECISION.md` S8/S11's "first export
  grants are PRIVATE_OWNER or explicitly scoped PRIVATE_STAFF_RESEARCH,
  not public or customer-wide access."
- **SUPPORT_READONLY genuinely cannot act.** It reads
  `view_support_case`, `view_customer_support_record`, and
  `view_incident_register`, but has no entry at all in `manage_incidents`
  or any other write action -- its name is enforced structurally, not just
  by convention.
- **Every `manage_own_*`/`view_own_*` action is CUSTOMER-only.** No staff
  role appears in any of these, and no customer-role action grants access
  to another tenant's or another customer's data -- scoping to "own" is a
  role-matrix property here, with the actual row-level enforcement coming
  from RLS (`docs/security/ARCHITECTURE.md` section 3) plus an explicit
  `user_id`/`tenant_id` filter in the service layer.

## Enforcement points

`is_allowed(role, action) -> bool` and `require_permission(role, action)`
(raises `PermissionDenied`) are the only two entry points. Every
authenticated route handler that performs a gated action is expected to
call `require_permission(scope.role, "<action>")` before doing anything --
this document's matrix is exactly and only what `_ALLOWED` contains; there
is no other place in the codebase a permission decision is made.
