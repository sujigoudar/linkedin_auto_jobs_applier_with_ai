# Coverage matrix C: AI/LLM, Config/Admin, Deployment, CI/CD, Docs existence, Docs quality

Repo: /home/user/linkedin_auto_jobs_applier_with_ai. Apps: `signal-copier` (SC), `signal-portfolio-commercial` (SPC).
Read-only audit. Paths are relative to the repo root unless prefixed `SC/` or `SPC/`.

Conventions: COVERED = opened and confirmed. PARTIAL = part tested. GAP = exists (or should exist as a gate or doc) and nothing tests it. INFRA = needs infrastructure or external accounts. N/A = feature does not exist.
A missing CI gate (e.g. coverage) is scored GAP because the gate is expected; a missing product feature (e.g. autoscaling) is N/A.

## Tally

| Section | COVERED | PARTIAL | GAP | INFRA | N/A | Total |
|---|---|---|---|---|---|---|
| 56 AI/LLM | 1 | 6 | 0 | 0 | 14 | 21 |
| 57 Config/admin | 9 | 7 | 0 | 0 | 2 | 18 |
| 58 Deployment | 7 | 4 | 4 | 8 | 3 | 26 |
| 59 CI/CD | 7 | 4 | 1 | 5 | 0 | 17 |
| 60 Docs existence | 15 | 8 | 3 | 0 | 0 | 26 |
| 61 Docs quality | 0 | 5 | 16 | 2 | 0 | 23 |

Link and anchor scan (`/tmp/runs/matrix/linkcheck.py`, 997 .md files, 138 relative links, 75 anchor links):
- SC and SPC: 0 broken file links, 0 broken anchors. The apps use backticked paths instead of markdown links; see 61.16 for those.
- Root `README.md` (AIHawk job-applier docs) has 1 broken file link (`docs/LICENSE`) and 2 broken anchors (`#features`, `#contributors`). `CONTRIBUTING.md` has 1 broken link (`./docs/development_diagrams.md`).
- The same 4 defects repeat in `.claude/worktrees/wp-40*`, which are copies.
- Total across the repo: 6 broken file links and 6 broken anchors. All are in the unrelated AIHawk files or their worktree copies.

Backticked path references (`/tmp/runs/matrix/pathcheck.py`):
- SC: 1334 refs, 54 unresolved. Of those, 23 are user-created `config/{accounts,routing,providers}.yaml` (acceptable), 2 exist only in SPC, and 29 are truly missing.
- SPC: 820 refs, 40 unresolved. Of those, 26 are cross-app refs to SC files, and 14 are truly missing.

---

## Section 56: AI/LLM

Where LLMs are used: nowhere in executable form.
- `grep -riE "anthropic|openai"` over both `app/` trees and both `requirements.txt` files returns nothing.
- The LLM-shaped code is boundary-only:
  - `SPC/app/services/model_gateway.py`: a purpose allow-list, a hard deny-list of 20 actions, `requires_human_review` (always True), config validation and a read-only API key role check. Its docstring says "No model provider API key exists in this environment".
  - `SC/app/phone_escalation.py::SignalExtractor`: `_do_extract` raises `NotImplementedError`; only `MockSignalExtractor` exists. In `SC/app/main.py::_resolve_phone_escalation_*` (~line 6054) the production resolver supplies no adapter or extractor, so the ENABLED path is unreachable.
  - `SC/app/sources/article_classifier.py`: a deterministic keyword classifier. Its docstring names an LLM as future work.
- Nothing in `app/` calls `require_model_permitted_action`, `requires_human_review`, `build_model_gateway_config` or `validate_api_key_role`. They are unwired. `record_llm_usage_estimate` is uncalled and untested.
- The "no execution authority" constraint is therefore enforced by absence, plus unit tests of the policy functions.

| # | Item | Status | Evidence / what is missing |
|---|---|---|---|
| 1 | Prompt regression | N/A | No prompts exist. Greps for anthropic/openai/prompt in `app/` and requirements are empty. `SignalExtractor._do_extract` raises `NotImplementedError`. |
| 2 | Structured-output | N/A | `ExtractionResult` (phone_escalation.py) is the intended contract, but only `MockSignalExtractor` returns it. There is no LLM output parser to test. |
| 3 | Schema-conformance | N/A | No LLM output to validate. The "JSON-schema-constrained response" appears only in a docstring. |
| 4 | Hallucinated-symbol | N/A | No LLM extractor. Deterministic analog: `SC/tests/test_article_classifier.py::test_multi_leg_spread_with_one_unresolved_leg_stays_unresolved_not_partially_executed` (unresolved candidate has `to_signal() is None`). Nothing tests an LLM-extracted symbol against a symbol allow-list. |
| 5 | Hallucinated-price | N/A | No LLM extractor. A candidate would re-enter the normal pipeline (price-deviation and age gates `SIGNAL_MAX_PRICE_*`), but no test wires an LLM candidate through it. |
| 6 | Hallucinated-action | PARTIAL | `SPC/tests/test_model_gateway.py::test_every_documented_disallowed_action_is_rejected` (20 parametrized actions incl. place_order, cancel_order, change_risk_limits, grant_entitlement) and `SC/tests/test_t13_phone_escalation.py::test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing` (AST check of the adapter surface) are real. Missing: the deny-list is called by no production code, and SC has no equivalent deny-list for extractor output. |
| 7 | Ambiguous-signal | N/A | No LLM. Deterministic analog is COVERED: `SC/tests/test_article_classifier.py::test_ambiguous_article_holds_for_human_review_never_guessed` and `test_empty_text_holds_for_human_review`. |
| 8 | Low-confidence | N/A | No confidence score exists. The article_classifier docstring defers it to "a real classifier/LLM call with its own confidence score". |
| 9 | Prompt-injection | N/A | No LLM consumes provider text. This becomes a GAP the moment an extractor is wired: `SignalExtractor.extract` receives raw screen/notification text with no injection test or sanitizer. |
| 10 | Malicious-provider-message | N/A | No LLM. Non-LLM adversarial ingest tests exist (`SPC/tests/test_c40_relay_ingest_adversarial_payloads.py`, `SC/tests/test_c30_schemathesis_api_fuzzing.py`) but do not test LLM handling. |
| 11 | Tool-use authorization | COVERED | `SPC/tests/test_model_gateway.py::test_every_documented_disallowed_action_is_rejected` (20 actions raise `DisallowedModelActionError`), `test_an_allowed_action_is_not_rejected`, `test_any_other_key_role_is_rejected[trading/admin/full_access/""]`. SC: `test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing` fixes the public method set and requires `tap(node)` to be structured-only. Caveat: unit-level only, nothing is wired to it. |
| 12 | LLM-timeout | N/A | No LLM call exists. |
| 13 | LLM-outage | N/A | No LLM call exists. The default state is "all providers disabled" and the full suite runs in it. SPC spec doc 11 says the runtime must work with models off. |
| 14 | Model-change regression | N/A | Only `model_version` as a required non-empty string (`test_a_valid_config_builds_successfully`). No model is invoked. |
| 15 | Model-fallback | N/A | No model exists. |
| 16 | Determinism/repeatability | N/A | No LLM. The deterministic classifier is exercised, but there is no repeatability test (e.g. N runs, identical output). |
| 17 | Cost-limit | PARTIAL | `SPC/tests/test_model_gateway.py::test_non_positive_limits_are_rejected[max_cost_budget_cents]` validates the config. Missing: runtime budget enforcement (no call path), and `operating_cost.record_llm_usage_estimate` is uncalled and untested (`grep llm SPC/tests/test_operating_cost.py` is empty). |
| 18 | Token-limit | PARTIAL | `test_non_positive_limits_are_rejected[max_input_tokens / max_output_tokens]` validates the config. No enforcement exists because no call path exists. |
| 19 | LLM-never-bypasses-risk-engine | PARTIAL | `SC/tests/test_t13_phone_escalation.py::test_shadow_mode_result_never_reaches_live_pipeline` (SHADOW returns `extraction is None`) and `test_missing_adapter_or_extractor_is_treated_as_disabled_even_if_enabled`. The ENABLED path returns a candidate "for caller to route" (`test_enabled_mode_with_real_candidate_returns_candidate_for_caller_to_route`). Missing: no test that `main._evaluate_phone_escalation_for_event` sends that candidate through `engine.handle_signal` and the risk gates, and no test that no other path from the extractor reaches an order. |
| 20 | LLM-never-directly-authorizes-capital | PARTIAL | `SPC/tests/test_model_gateway.py::test_every_model_purpose_requires_human_review` (all 7 purposes True), the deny-list entries (place_order, change_risk_limits, alter_nav/fees, grant_entitlement, auto_publish_output) and `validate_api_key_role` rejecting trading roles. Missing: unwired, and SC has no counterpart. |
| 21 | LLM decision auditability | PARTIAL | `SC/tests/test_t13_phone_escalation.py::test_record_and_list_phone_escalation_attempts` (persisted ledger row with disposition and `extraction_status`). Missing: no prompt, response or model-version log, since no model is called. |

---

## Section 57: Configuration / admin

| # | Item | Status | Evidence / what is missing |
|---|---|---|---|
| 1 | Create configuration | COVERED | `SC/tests/test_config_crud.py::test_account_created_via_api_is_immediately_usable_no_restart`. `SC/tests/test_track21_onboarding_wizard.py::test_create_provider_success`, `::test_create_source_success_after_provider_exists`. `SPC/tests/test_sleeve_admin.py::test_create_sleeve_persists_and_reloads`. `SPC/tests/test_product_admin.py::test_create_draft_product_persists_and_reloads`. |
| 2 | Edit | COVERED | `SC/tests/test_wp06_account_patch.py::test_patch_multiple_fields`, `::test_patch_preserves_safety_fields`. `SC/tests/test_config_crud.py::test_updating_a_routing_rule_changes_destinations_live`. `SPC/tests/test_workspace_settings.py::test_save_workspace_settings_updates_the_existing_row_not_a_new_one`. |
| 3 | Delete | COVERED | SC: `test_config_crud.py::test_deleting_an_account_removes_it_from_routing_immediately`, `::test_deleting_a_routing_rule_stops_routing_to_it`, `::test_deleting_a_provider_reverts_to_account_defaults`. `test_exe10_config_change_stranding.py::test_audits_exact_case_account_deletion_with_owned_position_is_blocked`. SPC has no delete path for settings or integrations (not a defect, just absent). |
| 4 | Validation | COVERED | `SC/tests/test_risk04_override_precedence_and_crud_validation.py::test_account_crud_rejects_invalid_scaling_values` (422 for -1, 0, True on multiplier and fixed_quantity), `::test_provider_crud_rejects_invalid_scaling_values`, `::test_analyst_crud_rejects_invalid_scaling_values`. SPC: `test_workspace_settings.py::test_save_workspace_settings_rejects_an_unknown_theme / _hiding_a_mandatory_panel / _an_empty_workspace_name`; `test_notification_preferences.py::..._rejects_excluding_safety`. |
| 5 | Versioning | PARTIAL | SPC has versioned domain objects: `test_candidate_comparison_version_race.py` (portfolio `version_number` unique), and `test_product_admin.py` revision field. SC `config_accounts`, `config_routing_rules`, `config_providers` and `config_analysts` (`app/db.py:301-369`) are single-row upserts with no history or revision. SPC workspace, integration and notification settings are also unversioned. |
| 6 | Rollback | N/A | No config rollback feature. Searched `app/db.py` and `app/main.py` for config history, revision and snapshot tables: none. `SC/docs/process/ROLLBACK.md` says code rollback is manual git revert and "no automated rollback/canary mechanism" exists. |
| 7 | Audit | PARTIAL | SPC has an append-only `audit_events` table (`SPC/tests/test_audit_log.py::test_audit_events_table_refuses_a_direct_update / _delete`). Audited actions: research runs, portfolio versions, content documents, copy mandates, local auth, staff access, token revocation, incidents. Missing: no `append_audit_event` on workspace-settings, integration-config or notification-preference saves (`dashboard_routes.py` ~2190, ~2674). SC has no audit trail for account, routing or provider config changes (the `/accounts` POST, PATCH and DELETE handlers record nothing). |
| 8 | Concurrent-edit | PARTIAL | SPC: `test_product_admin.py::test_update_product_draft_with_stale_revision_raises` (optimistic revision check) and `test_candidate_comparison_version_race.py::test_true_two_session_race_is_recovered_by_the_retry`. Missing: SC `PATCH /accounts/{id}` is read-modify-write with no etag or transaction, no test of two concurrent edits; SPC workspace, integration and notification settings are last-write-wins. |
| 9 | Invalid-setting | COVERED | `SC/tests/test_c01_typed_settings.py::test_malformed_standby_mode_fails_loud_at_import`, `::test_malformed_force_secure_cookies_fails_loud_at_import` (subprocess, non-zero exit with ValidationError). `SPC/tests/test_integration_configuration.py::test_create_rejects_a_non_test_environment`, `::test_create_rejects_an_unreviewed_provider_for_billing`. `SPC/tests/test_price_version.py::test_create_rejects_live_mode`. |
| 10 | Dangerous-setting confirmation | PARTIAL | `SC/tests/test_tr06_flatten_confirm.py::test_confirm_gate_blocks_the_mutating_request_until_exact_text_is_typed` (Playwright, typed confirm for flatten). Server-side guards: `test_wp06_account_patch.py::test_patch_broker_change_with_exposure_409` and `::test_patch_managed_lifecycle_change_with_exposure_409`. Missing: no confirmation test for other dangerous config changes (enabling a live broker, raising leverage or loss limits, deleting accounts or rules in the UI, STANDBY_MODE flip). |
| 11 | Provider configuration | COVERED | `SC/tests/test_config_crud.py::test_provider_override_created_via_api_applies_to_the_next_signal`, `::test_analyst_override_created_via_api_wins_over_provider`. `SC/tests/test_track21_onboarding_wizard.py::test_create_provider_success` (422 and 404 cases). `SPC/tests/test_integration_configuration.py::test_create_accepts_every_real_reviewed_publication_provider`. |
| 12 | Broker configuration | PARTIAL | The broker is an account field. Tested: `test_wp06_account_patch.py::test_patch_broker_change_with_exposure_409` and `test_wp45_readiness_checklist.py` (account with unknown broker blocks entries). `POST /accounts` accepts any broker string with no config-time validation (`app/main.py:3038`). Broker credentials come from env only; there is no config-API test. Per-broker adapter tests exist, but those are not configuration tests. |
| 13 | Account configuration | COVERED | `SC/tests/test_config_crud.py::test_max_notional_exposure_round_trips_and_gates_live`, `::test_config_persists_across_a_fresh_store_reload`. `SC/tests/test_wp41_account_editor_ui.py::test_tr08_ui_sizing_mode_and_risk_fraction_persist` (Playwright). `SC/tests/test_exe10_config_change_stranding.py`. |
| 14 | Strategy configuration | PARTIAL | SC "strategy" config is routing rules and provider and analyst overrides (covered). `GET /strategy-budgets` is read-only (`app/main.py:3455`, `test_wc30_budget_persistence.py` covers persistence only). SPC sleeves: `test_sleeve_admin.py::test_create_sleeve_persists_and_reloads`. There is no edit API or test for strategy budgets or sleeves in SC. |
| 15 | Risk configuration | COVERED | `SC/tests/test_alloc09_loss_limit_fails_closed.py::test_daily_loss_limit_configured_fails_closed`, `::test_daily_loss_limit_none_allows_entries`. `SC/tests/test_risk04...::test_account_crud_accepts_valid_scaling_values`. `SC/tests/test_config_crud.py::test_max_notional_exposure_round_trips_and_gates_live`. `test_wc35_risk_fraction_exact_sizing.py` and `test_risk_sizing.py` exist (names only, not opened). |
| 16 | Notification configuration | PARTIAL | SPC: `test_notification_preferences.py::test_save_notification_preferences_rejects_excluding_safety`, `::_rejects_quiet_start_without_quiet_end`, `::_rejects_an_unknown_category`. SC: notification-bridge device registration in `test_notification_bridge_api.py` (unauthorized app package rejected, 401 paths). Missing: SC `ALERT_WEBHOOK_URL` POST delivery has no test (`grep ALERT_WEBHOOK_URL SC/tests` returns nothing; `test_wp34_alerts.py` covers only the DB sink and ack). |
| 17 | Schedule configuration | N/A | Searched SC and SPC `app/` for cron, trading_hours, market_hours, trading_window and quiet_hours: no schedule configuration exists. SC audit `docs/audit/raw/F_operations_config_security_ui.md` F-12 records "No market calendar / session / holiday awareness". Only fixed intervals via env (e.g. `PRICE_MONITOR_INTERVAL_SECONDS`, `RELAY_POLL_INTERVAL_SECONDS`) exist. |
| 18 | Environment configuration | COVERED | `SPC/tests/test_commercial_live_secret_guard.py::test_commercial_live_refuses_to_start_with_default_placeholder_secrets`, `::test_commercial_live_starts_fine_with_real_looking_secret_overrides`, `::test_non_commercial_live_environments_are_unaffected_by_placeholder_secrets`, `::test_placeholder_secrets_in_use_reports_exactly_the_defaulted_ones`. `SC/tests/test_c01_typed_settings.py`. `SC/tests/test_wp33_qualification_environment.py::test_paper_broker_venue_environment`. Note: SC has no equivalent placeholder-secret startup guard for `RELAY_SIGNING_SECRET` and similar. |

---

## Section 58: Deployment

Workflows in `.github/workflows`: `signal-copier-ci.yml`, `signal-portfolio-commercial-ci.yml`, `integration-docker-build-ci.yml`, `ci.yml` (root, AIHawk), `design-system-check.yml` (CSS and a11y lint), `stale.yml`. There is no CD, deploy or release workflow. `SC/deploy/README.md` states "design and IaC draft, not an executed deployment".

| # | Item | Status | Evidence / what is missing |
|---|---|---|---|
| 1 | Local deployment | COVERED | `.github/workflows/integration-docker-build-ci.yml` job `build`: `docker compose build`, `docker compose up -d`, wait for both healthchecks, then a real webhook-to-commercial-ledger smoke. Needs a Docker daemon to reproduce in a sandbox; CI is the gate. |
| 2 | Development deployment | INFRA | No shared dev environment is defined. The only dev topology is the LOCAL_SIM compose stack (covered in 58.1). A shared dev environment would need a host and cloud account. |
| 3 | Staging deployment | INFRA | No staging environment exists (`grep -i staging` over `SC/deploy` and both `docs/operations` is empty). Needs a staging host or cluster, DNS, secrets and broker paper accounts. |
| 4 | Production deployment | INFRA | `SC/deploy/{terraform/oci-standby,cloud-init,systemd,litestream,cloudflare-heartbeat}` are never applied. Needs OCI tenancy, a Hostinger VPS, Cloudflare Worker and R2, and real broker credentials. Static checks only (`deploy/README.md` "Static validation performed"). SPC `docs/operations/DEPLOYMENT.md` "What a real production rollout still needs". |
| 5 | Docker build | COVERED | `integration-docker-build-ci.yml` step "Build both real images via docker compose". It also guards the `signal_platform_contracts` COPY regression (workflow header). |
| 6 | Container startup | COVERED | Same workflow, step "Boot the real stack and wait for both healthchecks" (40 x 3s). SPC `deploy/entrypoint-commercial.sh` runs alembic upgrade head then `ops/bootstrap.py` then exec; both are exercised by the boot. |
| 7 | Health-check | PARTIAL | SC: `SC/tests/test_health_truthfulness.py` (not ok before the first worker cycle; ok after a fresh cycle; not ok when stale; no auth needed), `SC/tests/test_wp35_rule_deletion_guard_schema_check_disk_health.py::test_health_endpoint_includes_disk_checks`. SPC: `SPC/tests/test_health_metrics_endpoints.py::test_health_is_public_and_truthful` covers only the DB-ok path. Missing: SPC `/health` "degraded" on DB failure is untested. The Dockerfiles define no HEALTHCHECK (compose healthchecks are exercised only in the CI workflow). |
| 8 | Readiness | COVERED | `SC/tests/test_tr13_tr16_trading_screens.py::test_readiness_endpoint_returns_all_six_dimensions_and_a_rollup`, `::test_readiness_liveness_up_while_data_readiness_unknown_render_both` (rollup DEGRADED, not ACTIVE). `SPC/tests/test_system_readiness.py::test_readiness_is_honest_and_empty_before_any_product_exists`, `::test_readiness_reports_a_real_per_product_release_status_and_trading_authority`. |
| 9 | Liveness | COVERED | `SPC/tests/test_api.py::test_healthz_reports_ok`. SC readiness asserts `body["liveness"]["status"] == "up"`. The checks are trivial by design. |
| 10 | Graceful-shutdown | PARTIAL | SC `lifespan` teardown (`app/main.py` ~620-637) stops the workers and closes every broker. Exercised only incidentally (many tests enter `with TestClient`) and by `SC/tests/test_standby_mode.py::test_lifespan_skips_background_loops_in_standby_mode`. No test asserts that stop() and close() are called, or SIGTERM and `TimeoutStopSec=30` behaviour (`SC/deploy/systemd/signal-copier.service`). SPC has only a startup guard in lifespan. |
| 11 | Rolling-deployment | N/A | Single-writer design: `SC/deploy/README.md` "exactly one active writer", `SC/docs/adr/0003-no-automatic-failover.md`, `SC/docs/process/ROLLBACK.md` "no automated rollback/canary mechanism". Searched rolling/blue-green/canary in deploy, docs and compose: nothing implemented. |
| 12 | Zero-downtime | N/A | Not a goal: `SC/deploy/README.md` "not redundant execution, not zero-RPO, and not automatic failover". Promotion is manual (`SC/deploy/RUNBOOK.md`). |
| 13 | Rollback | GAP | Manual only (`SC/docs/process/ROLLBACK.md`, `SPC/docs/process/ROLLBACK.md`). CI runs only `alembic upgrade head`. No downgrade or rollback test (`SPC/tests/test_alembic_migrations.py::test_the_rls_and_append_only_migration_refuses_to_downgrade` only checks the refusal). `SPC/tests/test_rollback_recovery_rls.py` is DB-transaction rollback, not deployment rollback. Image-tag rollback needs a registry (INFRA). |
| 14 | Database migration deployment | COVERED | `signal-portfolio-commercial-ci.yml` step "Verify Alembic migrations apply cleanly" (disposable PG16, `alembic upgrade head`). The integration workflow runs the entrypoint migration against `postgres:16`. `SC/tests/test_e01_alembic_migration_stamping.py::test_alembic_upgrade_head_from_genuinely_empty_database_matches_bootstrap`, `::test_fresh_database_is_stamped_at_head`, `::test_legacy_pre_alembic_database_is_stamped_not_recreated`. `SPC/tests/test_alembic_migrations.py::test_the_revision_chain_has_exactly_one_root_and_one_head`. |
| 15 | Configuration deployment | COVERED | CI compose boot loads `config/accounts.example.yaml` and `routing.example.yaml` (compose env) and the smoke asserts a signal flows. `SC/tests/test_exe10_config_change_stranding.py::test_fresh_database_still_seeds_from_yaml_once` and `::test_audits_exact_case_one_time_seed_does_not_resurrect_a_deleted_account`. No unit test parses the `.example.yaml` files or `.env.example` directly; CI covers only the YAML. |
| 16 | Secret injection | PARTIAL | Fail-closed behaviour is tested: `SPC/tests/test_commercial_live_secret_guard.py` (placeholder secrets refuse COMMERCIAL_LIVE); `SC/app/main.py` `receive_webhook` returns 503 when `WEBHOOK_SHARED_SECRET` is unset (code read, no dedicated test opened). The entrypoint uses `${COMMERCIAL_MIGRATOR_DATABASE_URL:?}` (untested). Injection is plain env vars or `env_file`; no secret-manager integration. SC has no startup guard for placeholder secrets. |
| 17 | DNS | INFRA | Needs a real domain and DNS records, plus the Cloudflare Worker target (`SC/deploy/cloudflare-heartbeat`). Nothing in the repo defines DNS. |
| 18 | TLS | INFRA | Needs real certs and a reverse proxy (README: the app serves plain HTTP; "put an HTTPS-terminating reverse proxy in front"). The app-side `FORCE_SECURE_COOKIES` flag is parsed (`test_c01_typed_settings.py`). |
| 19 | Certificate-renewal | INFRA | Needs an ACME or CA-managed cert on a real host. No artefact exists. |
| 20 | Firewall | INFRA | Needs the OCI security list or NSG (`terraform/oci-standby/main.tf` outputs: "restrict it with OCI security-list/NSG rules") and a real cloud. Not defined as code. |
| 21 | Port-exposure | GAP | Locally checkable, but nothing tests it. `SC/docker-compose.yml` publishes `127.0.0.1:8000` only. The root `docker-compose.yml` publishes `5432:5432` (Postgres, password `local-sim-postgres-password`), `8001:8001` and `8000:8000` on all interfaces. No test parses the compose files. A trivial YAML assertion would cover it. |
| 22 | Autoscaling | N/A | Not implemented and incompatible with the single-writer design. Searched deploy, docs, compose and terraform for autoscal: none. |
| 23 | Resource-limit | GAP | `SC/docker-compose.yml` sets `mem_limit: 512m` and `cpus: 1.0`. The root compose and both Dockerfiles set none. Nothing tests either. |
| 24 | Log-persistence | GAP | Logs go to stdout via structlog (`SC/app/logging_config.py`, covered by `SC/tests/test_c22_structured_logging.py` for redaction and format). No log volume, driver or file sink is defined in compose; the systemd unit relies on journald. Persistence is neither configured nor tested. |
| 25 | Backup | INFRA | Litestream to Cloudflare R2 (`SC/deploy/litestream/litestream.yml`, `SC/docs/operations/BACKUPS.md`). Needs the litestream binary and R2 credentials and bucket. SPC Postgres backup is documented only (`SPC/docs/operations/BACKUPS.md`). No app code or test creates a backup. |
| 26 | Restore | PARTIAL | App-state resume after a crash is tested: `SC/tests/test_alloc07_crash_boundaries.py::test_crash_after_ledger_intent_before_broker_call_never_resubmits`, `SC/tests/test_5e91e78_lifecycle_composition.py::test_crash_after_position_commit_before_fill_checkpoint_does_not_double_apply`, `SC/tests/test_config_crud.py::test_config_persists_across_a_fresh_store_reload`. Missing: no restore-from-backup drill (`litestream restore` plus `PRAGMA integrity_check`), which needs R2 (INFRA). No Postgres restore test. |

---

## Section 59: CI/CD

| # | Item | Status | Evidence / what is missing |
|---|---|---|---|
| 1 | Pull-request build | COVERED | `signal-copier-ci.yml` and `signal-portfolio-commercial-ci.yml` trigger on pull_request, path-scoped. `integration-docker-build-ci.yml` also triggers on pull_request. The root `ci.yml` runs pytest with both apps ignored and tolerates exit 5, so it is a no-op for these apps. |
| 2 | Unit-test gate | COVERED | `signal-copier-ci.yml` job `test` step "Run tests" (`pytest -q`). `signal-portfolio-commercial-ci.yml` step "Run tests" (`pytest -q` with PG16 installed). No required-status or branch-protection config is in the repo, so "gate" is by workflow failure. |
| 3 | Integration-test gate | COVERED | SPC tests use a real disposable Postgres cluster (`SPC/tests/conftest.py`, PG16 install step). `integration-docker-build-ci.yml` boots both services plus Postgres. |
| 4 | E2E-test gate | COVERED | `integration-docker-build-ci.yml` step "Real end-to-end smoke" (webhook to commercial `/api/v1/ops/source-coverage`). SC CI installs Playwright Chromium (step "Install Playwright's Chromium") for UI tests such as `test_tr06_flatten_confirm.py` and `test_wp41_account_editor_ui.py`. |
| 5 | Lint gate | COVERED | `ruff check .` in both CI workflows (SC scoped to rules F and B via `pyproject.toml`; SPC default). `design-system-check.yml` lints CSS. |
| 6 | Type-check gate | PARTIAL | SPC: `mypy app --ignore-missing-imports` (whole package). SC: an explicit list of about 55 files (`signal-copier-ci.yml`). The Makefile `typecheck-full` states the rest of `app/` has 18 known errors and is not run in CI. The Makefile list also drifted from CI (it lacks `app/workflow/margin.py`). |
| 7 | Security-scan gate | PARTIAL | `bandit -r app -ll` exists only in `SC/Makefile` (`make security`), not in any workflow. SPC has no SAST. No CodeQL or similar workflow. |
| 8 | Dependency-scan gate | COVERED | `pip-audit -r requirements.txt` in both CI workflows (SC ignores CVE-2026-49265, documented in the workflow). `.github/dependabot.yml` covers SC pip and github-actions only; SPC and `signal_platform_contracts` dependencies and Docker base images have no Dependabot. |
| 9 | Secret-scan gate | PARTIAL | `signal-copier-ci.yml` job `secret-scan` (gitleaks-action, `fetch-depth: 0`, config `signal-copier/.gitleaks.toml`). It runs only when `signal-copier/**` changes (workflow path filter), so SPC-only changes are not scanned. SPC has no config or allowlist. |
| 10 | Migration gate | COVERED | `signal-portfolio-commercial-ci.yml` step "Verify Alembic migrations apply cleanly". SC: `test_e01_alembic_migration_stamping.py` runs inside `pytest -q`. |
| 11 | Coverage gate | GAP | No gate exists. `pytest-cov` is not in either app's `requirements.txt`, and there is no `--cov` or `fail_under` in `pytest.ini`, `pyproject.toml`, the Makefile or workflows. (`pytest-cov` appears only in the root AIHawk requirements.) |
| 12 | Mutation-test gate | PARTIAL | `mutmut` is configured (`SC/pyproject.toml [tool.mutmut]`, `SPC/pyproject.toml`), with many mutation-derived regression tests (`test_track6x_*mutation*.py`). `SC/docs/standards/TESTING.md` states it is "a periodic check, not a CI gate". No workflow runs it. |
| 13 | Artifact-signing | INFRA | No image push or release job exists, so there is nothing to sign. Needs a registry plus cosign or KMS keys (and a publish step first). |
| 14 | Deployment-approval | INFRA | No deploy job. Needs GitHub Environments with required reviewers and a target environment. |
| 15 | Production-promotion | INFRA | No promotion pipeline (`SC/docs/process/RELEASE.md`: no versioned releases). Needs environments, a registry and a deploy target. |
| 16 | Rollback-pipeline | INFRA | None. Manual procedure only (`ROLLBACK.md`). Needs a deploy target and a registry of prior artifacts. |
| 17 | Failed-pipeline notification | INFRA | No notification step (`grep -E "slack|notify|if: failure"` in workflows returns nothing beyond the default GitHub emails). Needs a Slack or email webhook secret. |

Other CI observations:
- `signal-copier-ci.yml` and the SPC workflow are path-scoped to their own directory, so changes to `signal_platform_contracts/**` trigger only the Docker build workflow, not the app test suites.
- `signal_platform_contracts/tests/` has 5 test files. The only workflow that could collect them is root `ci.yml` (`pytest --ignore=...`), which installs the AIHawk requirements. Whether pydantic resolves there was not verified.

---

## Section 60: Documentation existence (both apps)

SC docs: 98 md files under `docs/` plus `README.md`, `CHANGELOG.md`, `PRODUCTION_READINESS.md`, `deploy/{README,RUNBOOK}.md`. SPC docs: `docs/` (about 70 files) plus `spec/` and `dashboard_spec/`. SPC has NO `README.md`. The repo-root `README.md` is the unrelated AIHawk job-applier project.

| # | Item | Status | Evidence / what is missing |
|---|---|---|---|
| 1 | README | PARTIAL | `SC/README.md` (1834 lines, substantive). `SPC/README.md` does not exist, yet `SPC/CLAUDE.md` says "Read README.md first". The repo-root README describes a different product (AIHawk). |
| 2 | Installation guide | COVERED | SC: `README.md` "Quickstart" and "Running with Docker"; `docs/operations/DEPLOYMENT.md` (113 lines). SPC: `docs/operations/DEPLOYMENT.md` (175 lines); root `docker-compose.yml` header comment. No standalone installation guide. |
| 3 | Quick-start | PARTIAL | SC `README.md` "Quickstart" exists (but see 61.22: the curl example fails as written). SPC has only the root `docker-compose.yml` header comment. |
| 4 | Architecture document | COVERED | `SC/docs/architecture/{ARCHITECTURE(300 lines),COMPONENTS,DEPENDENCIES}.md`; `SPC/docs/architecture/{ARCHITECTURE(238),COMPONENTS,DEPENDENCIES}.md`. |
| 5 | System-context diagram | COVERED | `SC/docs/architecture/SYSTEM_CONTEXT.md` (mermaid flowchart). `SPC/docs/architecture/SYSTEM_CONTEXT.md` (ASCII box diagram). Currency: see 61.11. |
| 6 | Data-flow diagram | COVERED | `SC/docs/architecture/DATA_FLOWS.md` (5 mermaid blocks); `SPC/docs/architecture/DATA_FLOWS.md` (3 mermaid blocks). |
| 7 | Trading-workflow document | COVERED | `SC/docs/workflow-contract/WORKFLOW_SPECIFICATION.md` (579 lines), `SC/docs/design/POSITION_LIFECYCLE.md`, `CAPITAL_ALLOCATION.md`. SPC is not a trading engine. |
| 8 | Signal-lifecycle document | PARTIAL | No document by that name. Pieces: `SC/docs/architecture/DATA_FLOWS.md` "End-to-end: a plain-account entry signal", `SC/docs/design/POSITION_LIFECYCLE.md` (position, not signal). SPC `docs/design/INGEST_PIPELINE.md` covers ingest only. |
| 9 | Broker-integration documentation | COVERED | `SC/docs/integrations/{CATALOG,MATRIX}.md`, `SC/docs/testing/VENUE_QUALIFICATION.md`, README "Adapter capabilities". Per-broker setup steps live in module docstrings. |
| 10 | Provider-integration documentation | COVERED | `SC/README.md` "Providers and analysts", `SC/docs/integrations/CATALOG.md` (sources), `SC/docs/security/{TELEGRAM_USER_LOGIN,SLACK_USER_TOKEN,TWITTER_USER_CONTEXT,EMAIL_COLLECTOR}.md`; `SPC/docs/integrations/CATALOG.md` (157 lines). |
| 11 | API documentation | PARTIAL | No endpoint reference document in either app. FastAPI auto-generates OpenAPI (no `docs_url`/`openapi_url` override; schemathesis fuzz tests use it). SPC has `dashboard_spec/docs/05_API_AND_STATE.md` (23 lines). README documents some endpoints inline. |
| 12 | Database-schema documentation | COVERED | `SC/docs/database/{SCHEMA(658 lines),DATA_DICTIONARY(401),MIGRATIONS}.md`; `SPC/docs/database/{SCHEMA,DATA_DICTIONARY,MIGRATIONS}.md`. Completeness is poor; see 61.2. |
| 13 | Configuration-reference | COVERED | `SC/docs/operations/CONFIGURATION.md` (185 lines), `SPC/docs/operations/CONFIGURATION.md` (182). |
| 14 | Environment-variable reference | COVERED | The same CONFIGURATION.md files (tabulated), plus `.env.example` in both apps and `docs/security/SECRETS.md`. Accuracy diff in 61.17. |
| 15 | Security documentation | COVERED | `SC/docs/security/{ARCHITECTURE,AUTHORIZATION,SECRETS,THREAT_MODEL}.md` plus README "Security notes". `SPC/docs/security/{ARCHITECTURE(325),AUTHORIZATION,SECRETS,THREAT_MODEL}.md`. |
| 16 | Risk-policy documentation | PARTIAL | No standalone risk policy. Pieces: `SC/README.md` "Loss limits and risk controls", `SC/docs/design/CAPITAL_ALLOCATION.md`, `SC/docs/adr/0007-fail-closed-capital-allocator.md`. `docs/workflow-contract/EFFECTIVE_POLICY.md` is config precedence, not risk policy. |
| 17 | Operational runbook | PARTIAL | `SC/deploy/RUNBOOK.md` (179 lines) is a failover and promotion runbook; `SC/docs/FAILOVER.md`. No routine ops runbook (start, stop, rotate, upgrade) in either app. |
| 18 | Incident runbook | PARTIAL | Only failover and DR scenarios (`RUNBOOK.md`, `operations/DR.md`). No general incident-response doc (stuck order, broker outage, halted trading, severity or escalation), although SPC has an incident register feature (`app/services/incident.py`). |
| 19 | Disaster-recovery document | COVERED | `SC/docs/operations/{DR(115),BACKUPS(90)}.md`; `SPC/docs/operations/{DR(137),BACKUPS(65)}.md`. Both are honest about gaps. |
| 20 | Deployment guide | COVERED | `SC/docs/operations/DEPLOYMENT.md`, `SC/deploy/README.md`; `SPC/docs/operations/DEPLOYMENT.md`. |
| 21 | Testing guide | COVERED | `SC/docs/standards/TESTING.md` (162), `SC/docs/testing/*`, README "Running tests"; `SPC/docs/standards/TESTING.md`, `SPC/docs/testing/*`. |
| 22 | User guide | GAP | None. SC README is the only owner-facing doc. `SPC/dashboard_spec/screens/CU-*.md` are build specs for screens, not a user guide. |
| 23 | Admin guide | GAP | None. `SPC/dashboard_spec/screens/AD-*.md` are build specs. |
| 24 | Troubleshooting guide | PARTIAL | `SC/docs/TROUBLESHOOTING.md` (109 lines) has 3 sections, all dev and test issues (`FencedOutError` under load, `no such table: writer_lease`, alembic multiple heads); no runtime error catalogue. `SPC/docs/TROUBLESHOOTING.md` (213 lines) includes deploy and bootstrap errors. |
| 25 | Commercial onboarding guide | GAP | The onboarding feature exists (`SPC/app/services/onboarding*.py`, `SPC/tests/test_onboarding*.py`; screens ID-01..04, CU-*) but no guide document exists (grep onboarding in `SPC/docs`: only manifest, ADR, PROGRESS, TEST_MATRIX and COMPONENTS mentions). |
| 26 | Changelog | COVERED | `SC/CHANGELOG.md` (1759 lines), `SPC/CHANGELOG.md` (1371 lines). Unversioned ("does not yet cut versioned releases"). |

---

## Section 61: Documentation quality

Automated checks that exist:
- `SC/scripts/check_docs_manifest.py` and `SPC/scripts/check_docs_manifest.py` (`make docs-check`). They verify every `docs/` file is indexed in `docs/manifest.yaml` and that there are no dangling entries. I ran both: SC "OK", SPC "OK (82 paths tracked)". They are NOT in any CI workflow.
- `SC/tests/test_wc01_traceability.py` checks `docs/workflow-contract/traceability_map.yaml` against `SCENARIO_CATALOG`, including `::test_implementation_paths_exist` and `::test_test_paths_exist`. It runs in CI via pytest.
- Nothing checks links, anchors, code, commands, API or config examples, screenshots, diagrams, terminology or env-var accuracy.

| # | Item | Status | Evidence / what is missing |
|---|---|---|---|
| 1 | Documentation accuracy | GAP | No automated check. Spot-checks found wrong claims: (a) `SC/README.md` Quickstart webhook curl (see 22); (b) `SC/docs/standards/TESTING.md` "CI's exact commands" lists a 25-file mypy set, while CI now runs about 55 files (stale), and says secret-scan runs "on every push/PR" although it is path-filtered; (c) `SC/docs/database/SCHEMA.md` omits 31 of 68 tables (61.2); (d) `SPC/CLAUDE.md` points to a nonexistent `README.md`; (e) `SC/deploy/litestream/litestream.yml` references `litestream.env.example`, which does not exist (`ls deploy/litestream` shows only `litestream.yml`). |
| 2 | Completeness | PARTIAL | Manifest check (above) passes for both apps but not in CI. Schema docs are incomplete. SC tables in `app/db.py` (`CREATE TABLE IF NOT EXISTS`, 68) not mentioned in `SCHEMA.md` or `DATA_DICTIONARY.md`: 31, e.g. `account_bindings`, `alerts`, `allocation_intents`, `decision_traces`, `email_collectors`, `notification_bridge_devices`, `owner_limits`, `phone_escalation_*`, `shadow_mode_results`, `strategy_budgets`, `telegram_collectors`, `website_*`. SPC models (44 tables): 3 undocumented (`onboarding_progress`, `operating_costs`, `service_health_samples`). |
| 3 | Freshness | GAP | No check. `git log -1`: SC `docs/architecture` last 2026-09-30 vs `app` 2026-10-03; `SPC/docs` 2026-10-02 vs `app` 2026-10-03. SC `SYSTEM_CONTEXT.md` is demonstrably stale (see 11). |
| 4 | Broken-link | GAP | No link checker in CI. My scan: 0 broken markdown file links in SC and SPC (138 relative links repo-wide). The root `README.md` has `docs/LICENSE` and `CONTRIBUTING.md` has `./docs/development_diagrams.md` (AIHawk, plus worktree copies). |
| 5 | Broken-anchor | GAP | No check. My scan: 75 anchor links, 0 broken in SC and SPC. Root `README.md` has 2 (`#features`, `#contributors`), duplicated in worktrees. |
| 6 | Code-example | GAP | No doctest or example runner. Fenced `python -c` and curl snippets are unverified; at least one is wrong (22). |
| 7 | Command-example | GAP | No runner. Evidence of drift: `TESTING.md` mypy command is stale vs `signal-copier-ci.yml`; the `SC/Makefile` `typecheck` list lacks `app/workflow/margin.py` that CI has. |
| 8 | API-example | GAP | No check. `SC/README.md` Quickstart `curl -X POST .../webhook/tradingview` omits the required `x-webhook-secret` header. `.env.example` ships `WEBHOOK_SHARED_SECRET=` blank, so `receive_webhook` returns 503 (and 401 once a secret is set but not sent) (`app/main.py:1525-1535`). README never mentions the header (only `docs/security/SECRETS.md` and `docs/testing/E2E.md` do). |
| 9 | Configuration-example | PARTIAL | `config/accounts.example.yaml` and `routing.example.yaml` are loaded by the CI compose stack (root `docker-compose.yml` sets `ACCOUNTS_CONFIG_PATH`/`ROUTING_CONFIG_PATH`) and the E2E smoke relies on them. `providers.example.yaml` and both `.env.example` files are not validated by any test (no test references `example.yaml`). |
| 10 | Screenshot-currentness | GAP | `SPC/dashboard_spec/evidence/*.png` (6 files: atlas_admin_desktop, atlas_customer_desktop, atlas_customer_phone, atlas_product_configuration, atlas_public_desktop, atlas_trading_desktop). Nothing compares them to the UI. SC has no screenshots. |
| 11 | Diagram-currentness | GAP | No check. Spot-check: `SC/docs/architecture/SYSTEM_CONTEXT.md` mermaid lists 10 inbound sources (TradingView, Telegram, Discord, Slack, Twitter, SMS, WhatsApp, MT, NinjaTrader, Rithmic) but `app/sources/` also has `email_source`, `rss_source`/`website`/`article_extraction`, `slack_user`, `telegram_user`, `twitter_user`, plus the `mobile/notification-bridge` and AgentMail ingress. About 6 source families are missing from the diagram. |
| 12 | Terminology-consistency | GAP | `GLOSSARY.md` exists in both apps (207 and 142 lines); no lint or check. |
| 13 | Acronym-definition | GAP | Same; no automated check. |
| 14 | Cross-reference | PARTIAL | Manifest check plus `test_wc01_traceability` cover the manifest and traceability-map paths only. Stale cross-doc refs remain (backticked, not links): `SPC/docs/**` cite `docs/PENDING_DECISIONS.md` in 12 places (real path `docs/state/PENDING_DECISIONS.md`); `SPC/docs/operations/DR.md` cites `docs/observability/SLO.md` (real: `operations/SLO.md`); `SPC/docs/00_discovery.md` cites `docs/00_current_state_and_scope.md` (it is under `spec/docs/`); `SC/deploy/README.md` cites `docs/03_REDUNDANCY_AND_DATA.md`; `SC/docs/design/ALLOCATION_DECISION_LEDGER.md` cites `docs/00_EVIDENCE_SCOPE_AND_DECISIONS.md`; `REMEDIATION_PLAN.md` cites `docs/adr/0013-intent-model.md` and `0014-sizing-and-quantization.md` (not present; ADR dir jumps 0012 to 0016). |
| 15 | Version-reference | GAP | No check. Spot-check consistent: Python 3.11 (both Dockerfiles, CI), Postgres 16 (compose, CI, docs). No guard keeps these in sync. |
| 16 | File-path-reference | PARTIAL | Automated only for `traceability_map.yaml` (`test_implementation_paths_exist`, `test_test_paths_exist`). My scan of backticked repo paths: SC 1334 refs, 29 truly missing, e.g. `app/workflow/risk.py`, `app/workflow/budgets.py` (in ADR 0016 and DATA_DICTIONARY), `app/brokers/classification.py`, `app/static/views/trXX.js`, 14 `tests/test_wp*.py` names in `REMEDIATION_PLAN.md`, `tests/venue/*`. SPC 820 refs, 14 truly missing (the `docs/PENDING_DECISIONS.md` cluster, `app/exceptions.py` in `standards/ERROR_HANDLING.md`) plus 26 cross-app refs that omit which app they mean (e.g. `KNOWN_ISSUES.md` -> `app/relay_worker.py`). |
| 17 | Environment-variable accuracy | GAP | No automated check. Manual diff vs `app/config.py` `_Settings` (SC 84 fields, SPC 13). SC code to docs (never documented anywhere in SC): `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOXES`, `AGENTMAIL_WEBHOOK_SECRET`, `AGENTMAIL_WEBHOOK_URL`, `SIGNAL_MAX_PRICE_AGE_SECONDS`, `SIGNAL_MAX_PRICE_DEVIATION_PCT`. Documented elsewhere but missing from `CONFIGURATION.md`: `DEFAULT_DAILY_LOSS_LIMIT_PERCENT`, `DEFAULT_MIN_EQUITY_THRESHOLD`, `LOST_ENTRY_GRACE_SECONDS`, `MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS`, `NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS`, `SIGNAL_CORRELATION_*` (3), `SLACK_USER_APP_TOKEN`, `TELEGRAM_API_HASH/ID` (17 total missing from CONFIGURATION.md). `.env.example` omits 30 of 84 fields, including `STANDBY_MODE`, `WRITER_*`, `PRICE_MONITOR_INTERVAL_SECONDS` and `RECONCILE_INTERVAL_SECONDS`. SC docs to code: no phantom vars. The only non-setting tokens are enum values (`EXECUTION_APPLIED`, `INTERNAL_PAPER`, `LOCAL_SIM`). `.env.example` per-account names (e.g. `ALPACA_MAIN_*`, `TRADOVATE_MAIN_*`) are dynamic (`os.getenv(f"{prefix}_API_KEY")` in `app/brokers/*.py`), not in `_Settings`; I did not verify each name. SPC code to docs: `HEALTH_SAMPLER_ENABLED` and `HEALTH_SAMPLER_INTERVAL_SECONDS` are undocumented anywhere; `.env.example` also omits `ENVIRONMENT`, `FORCE_SECURE_COOKIES`, `RELAY_SIGNING_SECRET_PREVIOUS` and the two HEALTH_SAMPLER vars (5 of 13). SPC docs to code: `COMMERCIAL_MIGRATOR_DATABASE_URL`, `COMMERCIAL_RUNTIME_ROLE_PASSWORD`, `RELAY_ROLE_PASSWORD` and `BOOTSTRAP_ARGS` are real, but read by the entrypoint and `ops/bootstrap.py`, not `config.py`. `SESSION_COOKIE_NAME` is a code constant, not an env var. |
| 18 | Default-value accuracy | GAP | No automated check. Manual: all 67 default rows in `SC/docs/operations/CONFIGURATION.md` match `_Settings` defaults (9 flagged by my script are formatting-only: relative vs absolute path, "(12h)" annotations). All 11 default blocks in `SPC/docs/operations/CONFIGURATION.md` match. Accurate today, unguarded. |
| 19 | Error-message documentation | GAP | No check. `SC/docs/TROUBLESHOOTING.md` covers only 3 dev and test errors. Runtime messages are undocumented, e.g. `webhook ingress is not configured (set WEBHOOK_SHARED_SECRET)` (503), `invalid webhook secret` (401), and standby 503s; README only mentions "fail closed with 503" generically. `SPC/docs/TROUBLESHOOTING.md` (213 lines) documents bootstrap and DB errors, most of which are strings from Postgres or tooling rather than the app. |
| 20 | Broker-setup reproducibility | INFRA | Real broker setup needs real or paper accounts and credentials (Alpaca, IBKR gateway, ccxt keys, etc.). Only the paper broker path is reproduced, by the CI compose E2E. There is no step-by-step per-broker guide; env names live in `.env.example` and module docstrings. |
| 21 | Provider-setup reproducibility | INFRA | Needs Telegram, Slack, Twitter, email and similar accounts. Security docs describe the flows (`docs/security/TELEGRAM_USER_LOGIN.md` etc.). The webhook provider path is reproduced by CI. |
| 22 | New-user reproducibility | PARTIAL | The root-compose route is exercised end-to-end in CI (`integration-docker-build-ci.yml`), but with env supplied in the compose file, not via the README steps. The README Quickstart path as written does not work: it copies `.env.example` (blank `WEBHOOK_SHARED_SECRET`, `OWNER_PASSWORD`, `SESSION_SECRET`), then the documented curl returns 503 (and 401 without the header); the dashboard's owner endpoints also fail closed (503) without `OWNER_PASSWORD`+`SESSION_SECRET`. The Quickstart never mentions either. SPC has no README or Quickstart. |
| 23 | No-technical-skill setup | GAP | Setup needs a shell, venv or Docker, and editing `.env`/YAML. A GUI wizard backend exists (`SC/tests/test_track21_onboarding_wizard.py`: "+Add Signal Provider") but secrets and install remain CLI-only. No installer, hosted setup or non-technical guide, and no user guide (60.22). |

---

## Top GAPs (ranked)

1. 59.11 Coverage gate: none in either app.
2. 59.7 / 59.9 / 59.6: bandit not in CI, gitleaks path-filtered to SC and absent for SPC, mypy scoped to about 55 SC files.
3. 59.12 Mutation tests: configured but never run in CI.
4. 57.5-57.8 Config versioning, rollback, audit and concurrent-edit: SC account, routing and provider config has none; SPC settings saves are unaudited.
5. 58.21 / 58.23 / 58.24 / 58.13: port exposure (root compose publishes Postgres 5432 on all interfaces), resource limits, log persistence and rollback are all untested.
6. 56.19-56.20 and the unwired deny-list: the LLM "no execution authority" guarantee is held by absence, and `model_gateway` has no production callers.
7. 61.22 / 61.8: README Quickstart is not reproducible (blank `WEBHOOK_SHARED_SECRET`, missing header), and SPC has no README.
8. 61.2 / 61.17: 31 of 68 SC tables undocumented; 6 SC env vars never documented and 17 missing from `CONFIGURATION.md`; 30 fields missing from `.env.example`.
9. 61.4-61.19: no CI docs gate at all (links, anchors, examples, env vars; the manifest check is not in CI).
10. 60.22 / 60.23 / 60.25 / 60.18: no user guide, admin guide, commercial onboarding guide or incident runbook.
