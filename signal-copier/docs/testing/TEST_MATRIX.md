# Test Coverage Matrix

Built by grepping `tests/` for the files that actually exercise each
feature area (161 test files, ~1,059 test functions as of this writing).
This is a map of *where* coverage for an area lives, not a claim that
every line in that area is covered — see each file for what it actually
asserts.

## Signal ingestion (sources)

| Source / concern | Test file(s) |
|---|---|
| Generic webhook source | `test_webhook_source.py`, `test_c37_webhook_schemathesis_fuzzing.py` (hypothesis-generated adversarial bodies/headers against the real `POST /webhook/{source_name}` route, Track 40) |
| WhatsApp | `test_whatsapp_webhook.py` |
| SMS (Twilio) + sender authorization | `test_sms_twilio_sender_authorization.py` |
| NinjaTrader source | `test_ninjatrader_source.py`, `test_ninjatrader_webhook.py` |
| Rithmic source | `test_rithmic_source.py` |
| Twitter/X bridge collector | `test_sig05_bridge_collector_lifecycle_handling.py` |
| Free-text message parsing | `test_text_parser.py` |
| Message classification / import endpoints | `test_e02_classify_messages_endpoint.py`, `test_e02_import_signals_endpoint.py`, `test_e02_message_disposition.py` |
| Duplicate/replay protection at signal ingestion | `test_sig01_duplicate_submission_protection.py` |
| Mixed provider/asset-class classification | `test_sig03_mixed_provider_asset_classification.py` |
| Source→analyst settings wiring | `test_source_analyst_wiring.py` |
| Ingress rate limiting | `test_c06_ingress_rate_limiting.py` |

## Routing

| Concern | Test file(s) |
|---|---|
| Route qualification ladder (implemented/configured/authenticated/entitled/tested/approved) | `test_route_qualification.py`, `test_qualification_api.py` |
| Routing-rule dry-run simulator (`POST /routing-rules/simulate`) | `test_tr11_routing_simulator.py` |
| Asset-class admission gate | `test_asset_class_gate.py` |
| Broker capability gate | `test_broker_capability_gate.py` |
| Entry-pause vs. exit-block distinction (EXE-10) | `test_exe10_config_change_stranding.py` |

## Risk sizing

| Concern | Test file(s) |
|---|---|
| Strict financial-input validation | `test_risk01_strict_financial_inputs.py` |
| Max-risk not silently ignored | `test_risk02_max_risk_not_silently_ignored.py` |
| Managed-entry signal reconstruction (stop/target from risk params) | `test_risk03_managed_entry_signal_reconstruction.py` |
| Override precedence + CRUD validation | `test_risk04_override_precedence_and_crud_validation.py` |
| Fixed-quantity / multiplier sizing | covered inline in `test_engine.py` |

## Broker execution, per adapter

| Broker | Test file(s) |
|---|---|
| Paper (in-memory reference broker) | `test_paper_broker.py`, `test_paper_broker_lifecycle_capabilities.py` |
| Alpaca | `test_alpaca_broker.py`, `test_alpaca_lifecycle_capabilities.py` |
| Interactive Brokers (IBKR) | `test_ibkr_broker.py`, `test_ibkr_last_price.py` |
| CCXT (crypto exchanges) | `test_ccxt_broker.py`, `test_ccxt_lifecycle_capabilities.py`, `test_ccxt_order_status_mapping.py`, `test_adp03_ccxt_cancel_symbol_context.py` |
| NinjaTrader | `test_ninjatrader_broker.py` |
| Rithmic | (source-side only: `test_rithmic_source.py`; broker adapter covered via `test_broker_capability_gate.py`) |
| OANDA | `test_oanda_broker.py` |
| Robinhood | `test_robinhood_broker.py` |
| Schwab | `test_schwab_broker.py` |
| SignalStack | `test_signalstack_broker.py` |
| Tastytrade | `test_tastytrade_broker.py` |
| TradeStation | `test_tradestation_broker.py` |
| Tradovate | `test_tradovate_broker.py` |
| MT4/MT5 | covered via broker-capability/bracket tests: `test_adp08_mt5_partial_fill.py` |
| Cross-adapter bracket-order capability verification | `test_adp02_adp06_bracket_capability_verification.py` |
| Account balance capability (honest not_tracked/unknown) | `test_account_balance_capability.py` |
| Cancel/replace confirmation semantics | `test_adp04_alpaca_cancel_replace_confirmation.py` |
| Last-price capability | `test_adp07_alpaca_last_price.py`, `test_ibkr_last_price.py` |

## Lifecycle management

| Concern | Test file(s) |
|---|---|
| Core `PositionLifecycleManager` | `test_lifecycle_manager.py`, `test_5e91e78_lifecycle_composition.py` |
| Managed-entry lifecycle via the engine | `test_engine_managed_lifecycle.py` |
| Close-quantity arbiter (owned/reserved/halted ledger) | `test_close_arbiter.py` |
| Idempotent close | `test_idempotent_close.py` |
| Manual exit | `test_manual_exit.py` |
| Plain-account close race | `test_plain_close_race.py` |
| Plain-account PENDING with zero fill | `test_plain_pending_zero_fill.py` |
| Position tracking (net_quantity truth) | `test_position_tracking.py` |
| Stop-loss/target activation + time-based exit | `test_pro02_activation_and_time_exit.py` |
| Stop amend on partial exit | `test_pro06_stop_amend_on_partial_exit.py` |
| Protection (stop/target) transfer on account/broker change | `test_protection_transfer.py`, `test_protection_transfer_oracle_sweep.py` |
| Immediate partial fill on managed entry | `test_managed_entry_immediate_partial_fill.py` |
| Submission/exit response lost mid-flight (EXE-01 family) | `test_exe01_exit_response_lost.py`, `test_exe01_submission_response_lost.py`, `test_exe01b_error_result_response_lost.py` |
| Idempotency-key scoping across retries | `test_exe11_idempotency_key_scoping.py` |
| Price polling into lifecycle state | `test_price_monitor.py` |

## Capital allocation

| Concern | Test file(s) |
|---|---|
| `CapitalAllocator` core sizing/reservation logic | `test_capital_allocator.py` |
| Fail-closed price-absent admission (P0-3) | `test_e03_capital_exposure_gate.py`, `test_capital_allocator.py` |
| Cross-signal capital contention (B7) | `test_b7_capital_contention.py` |
| Capital utilization dashboard/reporting | `test_tr02_capital_utilization.py` |
| Review regressions incl. exposure edge cases | `test_edd2b70_review_regressions.py` |

## Writer fencing

| Concern | Test file(s) |
|---|---|
| Fencing-token acquire/renew/require_active, cross-site refusal, concurrent-thread race safety | `test_writer_lease_fencing.py` |
| Standby mode read-only enforcement | `test_standby_mode.py` |

## Command ledger

| Concern | Test file(s) |
|---|---|
| Pre-effect durable ledger, fingerprint mismatch, duplicate-submission replay, ambiguous-outcome recording | `test_p0_2_command_ledger.py` |
| Config-change stranding (EXE-10) | `test_exe10_config_change_stranding.py` |
| Idempotency-key scoping (EXE-11) | `test_exe11_idempotency_key_scoping.py` |

## Reconciliation

| Concern | Test file(s) |
|---|---|
| Order/position reconciliation core | `test_reconciliation.py` |
| Close-order reconciliation against broker truth (P0-5) | `test_p0_5_close_reconciliation.py` |
| Pending-fill reconciliation, end-to-end | `test_pending_fill_reconciliation_integration.py` |
| Reconciliation + lifecycle preview endpoints (B2/B6) | `test_b2_b6_reconciliation_and_lifecycle_previews.py` |
| Worker health + reconciliation ops checks (OPS-01/02/03) | `test_ops01_ops02_ops03_worker_health_and_reconciliation.py` |
| Order-purpose family / distinct-quantity model (AUD-01) | `test_aud01_distinct_quantity_model.py`, `test_db0x_order_purpose_family.py` |

## Cross-cutting (security, observability, financial-integrity fuzzing)

| Concern | Test file(s) |
|---|---|
| Owner authentication (mutation-tested) | `test_owner_auth.py`, `test_c05_password_hash_auth.py`, `test_login_hardening.py` |
| Structured logging correlation (C22) | `test_c22_structured_logging.py` |
| Metrics/Prometheus endpoint | `test_c23_metrics_endpoint.py`, `test_c23_prometheus_metrics.py` |
| Dashboard XSS prevention (real browser) | `test_c14_dashboard_xss_prevention.py` |
| Dashboard accessibility (real browser) | `test_c33_c34_dashboard_accessibility.py` |
| Hypothesis-based quantity-conservation property test (C29) | `test_c29_hypothesis_quantity_conservation.py` |
| Schemathesis API fuzzing (C30, extended Track 40) | `test_c30_schemathesis_api_fuzzing.py` (read-only GETs, now including `/system/readiness`, `/export-events`, `/mobile-devices`, `/connections/*`), `test_c37_webhook_schemathesis_fuzzing.py` (the webhook ingress route specifically) |
| Fault injection (C32, extended Track 40) | `test_c32_fault_injection.py` (broker `get_order_status`/reconciliation), `test_c36_broker_submission_fault_injection.py` (broker `place_order` itself -- real transport faults + malformed response shapes) |
| DB foreign-key enforcement (DB-01) | `test_db01_foreign_key_enforcement.py` |
| Alembic migration stamping (E01) | `test_e01_alembic_migration_stamping.py` |
| Context-source rate limiting (C07) | `test_c07_context_rate_limiting.py` |
| Health-endpoint truthfulness | `test_health_truthfulness.py`, `test_monitoring.py` |

See `docs/testing/REGRESSIONS.md` for the specific real bugs several of
these files were written to catch, and `docs/testing/E2E.md` /
`docs/testing/FIXTURES.md` for how the real-browser and full-stack tests
in this matrix are actually wired.
