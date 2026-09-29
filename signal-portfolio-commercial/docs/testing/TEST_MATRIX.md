# Test Coverage Matrix

A real map from major feature areas to the test files that cover them,
built by reading every `tests/*.py` module docstring (each of which
already names the screen id and/or service module it covers). Screen ids
(`AD-XX`, `CU-XX`, `PU-XX`, `ID-XX`) and acceptance-case ids (`INT-XXX`,
`CP-XXX`) are copied verbatim from those docstrings.

| Feature area | Test files | Notes |
|---|---|---|
| **Ingest / relay** (source receipts, ordering, gap detection, snapshot bootstrap, relay auth/role) | `test_integration_inbox.py`, `test_integration_inbox_ordering.py` (INT-006), `test_integration_inbox_snapshot.py` (INT-008/INT-009), `test_integration_status.py`, `test_relay_auth.py`, `test_relay_role_access.py`, `test_relay_routes.py`, `test_real_account_route.py` (INT-033), `test_source_coverage.py` (INT-027), `test_source_coverage_route.py` | `test_relay_role_access.py` is the one RLS test that runs as `relay_role` rather than `app_role` -- see `docs/testing/E2E.md`. |
| **Ledger / attribution** (economic journal, append-only, P&L, FIFO-lot attribution) | `test_ledger.py`, `test_platform_performance.py`, `test_platform_performance_route.py`, `test_analyst_attribution.py`, `test_business_economics.py`, `test_pamm_accounting.py`, `test_mam_allocation.py` | `test_ledger.py` covers the append-only invariant itself (docs/04: "The execution journal is append-only by identity with correction/reversal events"). |
| **Research / selection** (sleeve catalog, portfolio research/candidate enumeration, portfolio selection, rights) | `test_sleeve_admin.py` (AD-03), `test_sleeve_model.py`, `test_research_run.py` (AD-04), `test_portfolio_research.py`, `test_portfolio_selection.py` (CU-02), `test_portfolio_rights.py` (CP-002), `test_rights_registry.py` | `app/services/candidate_comparison.py` (AD-06) has no dedicated `test_candidate_comparison.py` -- its `CandidateNotFoundError`/`InvalidCandidateDraftError` paths are exercised only indirectly, through `test_dashboard_routes.py`'s route-level coverage. A real gap worth a direct service-level test file. |
| **Publication** (products, publication intent, admission, publisher destinations/claims, release review) | `test_product_admin.py` (AD-07), `test_dashboard_routes.py` (AD-07/PU-02), `test_publication.py`, `test_publication_admin.py` (AD-10), `test_publication_admission.py` (CP-003/CP-051/CP-045), `test_publisher_destination.py` (AD-09), `test_publisher_writer_claim.py` (CP-045), `test_release_review.py` (AD-08), `test_collective2_publisher.py`, `test_etoro_adapter.py`, `test_copyfactory_close_only.py` |  |
| **Subscriptions / billing** (pricing, entitlement, billing model, Stripe webhooks) | `test_price_version.py` (AD-13), `test_entitlement.py`, `test_billing_model.py`, `test_stripe_webhook.py` |  |
| **Customer portal** (overview, selection detail, performance, connections, mandates, preferences, support) | `test_customer_overview.py` (CU-01), `test_customer_selection_detail.py` (CU-03), `test_customer_performance_report.py` (CU-06), `test_customer_performance_state.py`, `test_platform_connection.py` (CU-07/CU-08), `test_copy_mandate.py` (CU-09/CU-10), `test_customer_display_preferences.py` (CU-13), `test_notification_preferences.py` (CU-12), `test_support_case.py` (CU-14), `test_api_key.py` (CU-16), `test_managed_program.py` (AD-14), `test_customer_support_view.py` (AD-11), `test_eligibility.py` (ID-04), `test_follower_observation.py` |  |
| **Admin / operations console** (operations overview, staff, workspace settings, audit log, evidence, content) | `test_operations_overview.py` (AD-01), `test_staff_access.py` (AD-16), `test_workspace_settings.py` (AD-20), `test_audit_log.py` (AD-18), `test_evidence_manifest.py` (AD-18), `test_content_document.py` (AD-19), `test_integration_configuration.py` (AD-17), `test_trading_performance_page.py`, `test_model_gateway.py`, `test_history_origin.py`, `test_onboarding.py` |  |
| **Auth / token revocation** (JWT issue/verify, revocation denylist, local sign-in flows, sessions) | `test_auth.py`, `test_local_auth.py`, `test_id01_id02_id03_auth_routes.py` (ID-01/ID-02/ID-03), `test_token_revocation.py` |  |
| **Public site** (catalog, home, fit simulator, help/compatibility) | `test_public_site.py` (PU-01/PU-03/PU-08), `test_public_fit_simulation_route.py` (PU-03), `test_fit_simulation_client.py` |  |
| **Tenancy / row-level-security / permissions (cross-cutting)** | `test_tenancy_models.py`, `test_row_level_security.py` (CP-011), `test_cross_tenant_foreign_key.py` (CP-012), `test_permissions.py` (CP-013) | These are the tests that prove tenant isolation itself, independent of any one feature area -- see `docs/standards/CODING.md` §2 and `docs/testing/E2E.md`. |
| **API surface (generic HTTP)** | `test_api.py` |  |
| **Schema / migrations** | `test_alembic_migrations.py` | Import/shape sanity only -- the real `alembic upgrade head` check is a separate CI step against a live cluster (`docs/standards/TESTING.md` §3). |

## Reading this matrix

- A row lists every test file whose own module docstring names that
  feature area's screen id(s) or service module(s) -- this matrix is
  derived directly from those docstrings, not from guessing at file
  purpose from its name alone.
- Several service modules are deliberately "pure function tests, no
  database needed" per their own docstrings (`test_business_economics.py`,
  `test_collective2_publisher.py`, `test_copyfactory_close_only.py`,
  `test_entitlement.py`, `test_etoro_adapter.py`, `test_history_origin.py`,
  `test_mam_allocation.py`, `test_model_gateway.py`, `test_onboarding.py`,
  `test_pamm_accounting.py`, `test_permissions.py`,
  `test_portfolio_research.py`, `test_relay_auth.py`) -- these don't
  depend on `postgres_cluster`/`db_session` at all and stay fast even
  when Postgres is unavailable in the local dev environment.
- When adding a new feature, add its test file to the matching row above
  (or a new row) in the same commit that adds the feature -- this file is
  meant to be kept current, not written once and left stale.
