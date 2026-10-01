# Database Schema

Source of truth: `app/models/*.py` and `alembic/versions/*.py` (current head:
`c1d2e3f4a5b6`, see `MIGRATIONS.md`). Every table below is real and currently
migrated — nothing here is aspirational.

RLS = row-level security via `tenant_isolation` (ADR-0001). "RLS (bespoke)" = a
table with its own, non-generic policy. "Not RLS-scoped" = looked up by its own key
before any tenant scope can exist (documented per-table below). Append-only = has
the `append_only_guard` trigger (ADR-0008).

## Tenancy & identity

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `tenants` | `tenancy.Tenant` | `tenant_id` PK | — (root of scoping) | `display_name`, `environment`. |
| `user_identities` | `tenancy.UserIdentity` | `user_id` PK | Not RLS-scoped | `email` unique; `password_hash`, `email_verified_at` (local auth, ADR-0007). |
| `memberships` | `tenancy.Membership` | `(tenant_id, user_id)` PK | RLS | `role` (`MembershipRole`: OWNER, RESEARCHER, REVIEWER, PUBLISHER_OPERATOR, BILLING_OPERATOR, SUPPORT_READONLY, CUSTOMER). FKs to `tenants`, `user_identities`. |
| `customer_profiles` | `tenancy.CustomerProfile` | `tenant_id` PK | RLS | `user_id`, `display_name`, `residence_jurisdiction`. FK to `memberships`. |

## Rights & products

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `rights_grants` | `rights.RightsGrant` | `grant_id` PK | Not tenant-scoped (keyed by `source_id`) | `status` (RightsStatus), `uses`/`channels`/`jurisdictions`/`assets` arrays, `attribution_policy_id`, `wind_down_policy_id`, `review_id`. |
| `products` | `product.Product` | `product_id` PK | RLS (bespoke: `product_visibility` — own tenant OR `lifecycle_state='PUBLISHED'`) | `slug` unique, `portfolio_version_id`, `cash_bps`, `service_modes[]`, `lifecycle_state`, `revision`. |
| `content_documents` | `content_document.ContentDocument` | `document_id` PK | RLS (bespoke: `content_document_visibility` — own tenant OR `state='PUBLISHED'`) | `document_type`, `locale`, `title`, `body`, `audience_policy_id`, `source_evidence_ids[]`, `state`. |

## Research & selection

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `sleeves` | `sleeve.Sleeve` | `sleeve_id` PK | RLS | `provider`, `analyst`, `strategy_horizon`, `asset_class`, `parser_version`, various policy id refs. |
| `research_runs` | `research_run.ResearchRun` | `research_run_id` PK | RLS | `sleeve_ids[]`, `recipes[]`, subset/cash/cluster bps params, `train_sessions`, `test_sessions`, `holdout_fraction`. |
| `portfolio_versions` | `portfolio_version.PortfolioVersion` | `portfolio_version_id` PK | RLS. **Append-only.** | `portfolio_id`, `version_number`, `cash_weight`, `research_cutoff`, `max_subscriber_capacity`, `consent_disclosure_version`. |
| `portfolio_version_sleeves` | `portfolio_version.PortfolioVersionSleeve` | `(portfolio_version_id, sleeve_id)` PK | RLS (via parent). **Append-only.** | `weight`. FKs to `portfolio_versions`, `sleeves`. |
| `release_reviews` | `release_review.ReleaseReview` | `release_review_id` PK | RLS | `product_id` FK, `object_revision_reviewed`, `state` (ReleaseReviewState), `evidence_manifest_id`, `audience_policy_id`. |
| `portfolio_selections` | `portfolio_selection.PortfolioSelection` | `selection_id` PK | RLS | `user_id`, `product_id` FK, `state` (PortfolioSelectionState). |

## Publication

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `publication_intents` | `publication.PublicationIntent` | `intent_id` PK | Not tenant-scoped directly | `idempotency_key` unique, natural-key unique on `(channel, external_strategy_id, portfolio_version_id, action, revision)`, `state` (PublicationState), `source_revision_ids[]`, `rights_grant_ids[]`. |
| `publisher_writer_claims` | `publisher_writer_claim.PublisherWriterClaim` | `(channel, external_strategy_id)` PK | Not tenant-scoped | `writer_identity`, `claimed_at`. |
| `publisher_destinations` | `publisher_destination.PublisherDestination` | `destination_id` PK | RLS | `platform` (Platform enum), `external_strategy_id`, `environment`, `credential_ref`, `publication_mode`. |
| `real_account_routes` | `real_account_route.RealAccountRoute` | `(broker, account_reference)` PK | Not tenant-scoped (looked up by broker/account before tenant is known) | `channel`, `external_strategy_id`, `writer_identity`, `tenant_id` (stored, not an RLS gate), `claimed_at`. |
| `exclusive_ownership_plans` | `real_account_route.ExclusiveOwnershipPlan` | `(broker, account_reference)` PK | Not tenant-scoped | `approved_channel`, `approved_external_strategy_id`, `qualified_by`, `qualified_at`. |

## Commercial / billing

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `subscriptions` | `billing.Subscription` | `subscription_id` PK | RLS | `tier` (ProductTier), `state` (SubscriptionState), `price_cents`, `currency`, `current_period_end`, `processor_subscription_id`. |
| `price_versions` | `price_version.PriceVersion` | `price_version_id` PK | RLS | `sku` unique, `amount_minor`, `interval` (BillingInterval), `is_unlimited_portfolios`, `portfolio_limit`, `features[]`, `mode` (PriceMode). |
| `processed_webhook_events` | `webhook_event.ProcessedWebhookEvent` | `event_id` PK | Not tenant-scoped | Stripe webhook dedup ledger; `received_at`. |
| `copy_mandates` | `copy_mandate.CopyMandate` | `mandate_id` PK | RLS | `selection_id`, `connection_id`, `allocation_amount`/`currency`, `max_trade_risk`, `max_loss`, `start_mode` (CopyMandateStartMode), `state` (CopyMandateState). |
| `managed_programs` | `managed_program.ManagedProgram` | `program_id` PK | RLS | `broker_program_id`, `mode` (ManagedProgramMode), `allocation_policy_id`, `nav_policy_id`, `dealing_schedule_id`, `state`. |

## Ledger

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `ledger_entries` | `ledger.LedgerEntry` | `entry_id` PK | RLS. **Append-only.** | See `DATA_DICTIONARY.md` and `docs/design/LEDGER_MODEL.md`. |
| `source_stop_target_revisions` | `source_stop_target_revision.SourceStopTargetRevision` | `revision_id` PK | RLS. **Append-only.** | Track 41, ADR-0011: a `SourceEventKind.TARGET_UPDATE`/`STOP_UPDATE` revision -- never a `LedgerEntry` (no quantity/price economic fact). See `DATA_DICTIONARY.md`. |

## Integration ingest

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `export_stream_registrations` | `integration_inbox.ExportStreamRegistration` | `registration_id` PK | RLS + bespoke `relay_stream_lookup` permissive policy for `relay_role` | `source_stream` globally unique, `environment`. See ADR-0002. |
| `inbox_events` | `integration_inbox.InboxEvent` | `event_id` PK | RLS | See `DATA_DICTIONARY.md` and `docs/design/INGEST_PIPELINE.md`. Unique on `(source_stream, producer_generation, export_sequence)`. |

## Platform connections & customer workspace

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `platform_connections` | `platform_connection.PlatformConnection` | `connection_id` PK | RLS | `user_id`, `platform`, `environment`, `masked_account_label`, `state` (PlatformConnectionState). Declared only today — see ADR-0004. |
| `workspace_settings` | `workspace_settings.WorkspaceSettings` | `tenant_id` PK | RLS | `theme`, `density`, `visible_panel_ids[]`, `column_order[]`, `notification_route_id`. |
| `notification_preferences` | `notification_preferences.NotificationPreferences` | `(tenant_id, user_id)` PK | RLS | `categories[]`, `timezone_name`, `quiet_start`/`quiet_end`, `marketing_consent`. |
| `customer_display_preferences` | `customer_display_preferences.CustomerDisplayPreferences` | `(tenant_id, user_id)` PK | RLS | `theme`, `density`, `number_locale`, `view_currency`, `reduce_motion`. |
| `eligibility_assessments` | `eligibility.EligibilityAssessment` | `(tenant_id, user_id)` PK | RLS | `residence_country`, `tax_residence[]`, `customer_type`, `requested_service_modes[]`, `document_versions[]`, `facts_confirmed`. |
| `support_cases` | `support_case.SupportCase` | `case_id` PK | RLS | `category`, `related_object_id`, `subject`, `description`, `attachment_ids[]`, `status`. |

## Operations & security

| Table | Model | Key | RLS | Notes |
|---|---|---|---|---|
| `api_keys` | `api_key.ApiKey` | `key_id` PK | RLS | `scopes[]`, `key_hash` unique, `expires_at`, `revoked_at`. |
| `integration_configurations` | `integration_configuration.IntegrationConfiguration` | `integration_id` PK | RLS | `provider_registry_id`, `purpose` (IntegrationPurpose), `environment`, `credential_ref`, `quota_profile_id`. |
| `audit_events` | `audit_event.AuditEvent` | `event_id` PK | RLS. **Append-only.** | `actor_user_id`, `object_type`, `object_id`, `action`, `outcome` (AuditOutcome), `event_time`. |
| `incidents` | `incident.Incident` | `incident_id` PK | RLS | `service` (IncidentService), `severity`, `state`, `affected_object_type`/`id`, `assignee_user_id`, `evidence_ids[]`. |

## Local auth (not RLS-scoped — see ADR-0007)

| Table | Model | Key | Notes |
|---|---|---|---|
| `auth_tokens` | `local_auth.AuthToken` | `token` PK | `user_id` FK, `token_type` (EMAIL_VERIFICATION / PASSWORD_RESET), `expires_at`, `consumed_at`. |
| `web_sessions` | `local_auth.WebSession` | `session_id` PK | `user_id`, `tenant_id`, `role`, `csrf_token`, `expires_at`. FK `(tenant_id, user_id)` → `memberships`. |
| `issued_tokens` | `token_revocation.IssuedToken` | `jti` PK | `tenant_id`, `user_id`, `role`, `issued_at`, `expires_at` (indexed). |
| `revoked_tokens` | `token_revocation.RevokedToken` | `jti` PK | `tenant_id`, `revoked_at`, `expires_at` (copied from the token's own `exp`). |

## Enum reference (selected)

- `Book`: SOURCE, MODEL, PLATFORM, FOLLOWER
- `Side`: BUY, SELL
- `ReconciliationState`: UNRECONCILED, RECONCILED, DISPUTED
- `EvidenceClass`: from `signal_platform_contracts` (synthetic/paper/backtest/
  observed-execution/platform-reported — see `DATA_DICTIONARY.md`)
- `MembershipRole`: OWNER, RESEARCHER, REVIEWER, PUBLISHER_OPERATOR,
  BILLING_OPERATOR, SUPPORT_READONLY, CUSTOMER
- `ProductLifecycleState`, `ContentDocumentState`: include `PUBLISHED` (drives the
  bespoke visibility policies above)

All enums are stored as `native_enum=False` (plain string columns with a
Python-side `Enum` mapping) throughout this codebase — never a native Postgres
`ENUM` type — so adding a new member is an additive application-code change, never
a migration that alters a Postgres type.
