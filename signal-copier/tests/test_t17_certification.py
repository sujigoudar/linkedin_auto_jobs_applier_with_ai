"""Track 17: provider certification checklist + shadow mode.

Covers: certification-check scoping (provider x source x asset_class x
account_route, not just provider), LIVE_ELIGIBLE as a correctly-derived
(never independently settable) property, shadow mode's structural
inability to submit a real order, shadow mode reusing (not
reimplementing) real decision logic, and scorecard computation honesty
(no fabricated percentages). Mirrors tests/test_t13_phone_escalation.py's
own structure (`store` fixture, direct module-level calls)."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.certification import (
    ALL_CHECKS,
    CHECK_KIND,
    CertificationError,
    CheckKind,
    CheckStatus,
    is_live_eligible,
    validate_check_record,
    validate_scope,
)
from app.certification_scorecard import compute_scorecard
from app.db import SignalStore
from app.models import AssetClass, DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule
from app.shadow_mode import ShadowOrderIntent, evaluate_shadow, to_result_row


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


def _register_provider_source(store: SignalStore, *, provider_id: str = "tradealgo") -> tuple[str, str]:
    store.register_provider(
        provider_id=provider_id, display_name="TradeAlgo", status="onboarding", certification_state="uncertified"
    )
    source = store.register_source(source_id=f"{provider_id}-tg", provider_id=provider_id, platform="telegram")
    return provider_id, source["id"]


# ---------------------------------------------------------------------------
# Scoping: provider x source x asset_class x account_route
# ---------------------------------------------------------------------------


def test_validate_scope_requires_all_four_dimensions():
    with pytest.raises(CertificationError):
        validate_scope(provider_id="p", source_id="", asset_class="equity", account_route="acct1")
    with pytest.raises(CertificationError):
        validate_scope(provider_id="p", source_id="s", asset_class="", account_route="acct1")
    validate_scope(provider_id="p", source_id="s", asset_class="equity", account_route="acct1")


def test_certification_checks_are_scoped_not_just_by_provider(store: SignalStore):
    provider_id, source_id = _register_provider_source(store)
    checks_equity_acct1 = store.ensure_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    checks_option_acct1 = store.ensure_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="option", account_route="acct1"
    )
    checks_equity_acct2 = store.ensure_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct2"
    )
    # Three DIFFERENT scopes -> three DIFFERENT, independently-tracked
    # sets of check ids, even though provider_id/source_id repeat.
    ids_equity_acct1 = {c["id"] for c in checks_equity_acct1}
    ids_option_acct1 = {c["id"] for c in checks_option_acct1}
    ids_equity_acct2 = {c["id"] for c in checks_equity_acct2}
    assert ids_equity_acct1.isdisjoint(ids_option_acct1)
    assert ids_equity_acct1.isdisjoint(ids_equity_acct2)
    assert len(checks_equity_acct1) == len(ALL_CHECKS)

    # Marking a PASS in one scope must NEVER leak into a sibling scope.
    store.record_certification_check(
        provider_id=provider_id,
        source_id=source_id,
        asset_class="equity",
        account_route="acct1",
        check_name="disconnect_recovery",
        status="PASS",
        evidence={"note": "manually verified reconnect behavior on 2026-09-30"},
        checked_by="operator@example.com",
    )
    equity_acct1_after = store.list_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    option_acct1_after = store.list_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="option", account_route="acct1"
    )
    equity_acct2_after = store.list_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct2"
    )
    assert any(
        c["check_name"] == "disconnect_recovery" and c["status"] == "PASS" for c in equity_acct1_after
    )
    assert all(
        c["status"] != "PASS" for c in option_acct1_after if c["check_name"] == "disconnect_recovery"
    )
    assert all(
        c["status"] != "PASS" for c in equity_acct2_after if c["check_name"] == "disconnect_recovery"
    )


def test_fresh_scope_starts_with_every_check_not_run(store: SignalStore):
    provider_id, source_id = _register_provider_source(store)
    checks = store.ensure_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    assert {c["check_name"] for c in checks} == {c.value for c in ALL_CHECKS}
    for c in checks:
        assert c["status"] == "NOT_RUN"


# ---------------------------------------------------------------------------
# Manual attestation: never a bare boolean, never auto-passed, never for
# an AUTOMATED check
# ---------------------------------------------------------------------------


def test_manual_record_requires_evidence_and_checked_by_for_pass():
    with pytest.raises(CertificationError):
        validate_check_record(check_name="disconnect_recovery", status="PASS", evidence=None, checked_by="op")
    with pytest.raises(CertificationError):
        validate_check_record(check_name="disconnect_recovery", status="PASS", evidence={}, checked_by="op")
    with pytest.raises(CertificationError):
        validate_check_record(
            check_name="disconnect_recovery", status="PASS", evidence={"note": "verified"}, checked_by=""
        )
    # A real evidence note + a real identity is accepted.
    validate_check_record(
        check_name="disconnect_recovery", status="PASS", evidence={"note": "verified"}, checked_by="op@example.com"
    )


def test_automated_check_cannot_be_manually_recorded(store: SignalStore):
    provider_id, source_id = _register_provider_source(store)
    with pytest.raises(CertificationError):
        store.record_certification_check(
            provider_id=provider_id,
            source_id=source_id,
            asset_class="equity",
            account_route="acct1",
            check_name="connection",
            status="PASS",
            evidence={"note": "trust me"},
            checked_by="op@example.com",
        )


def test_every_check_is_classified_automated_or_attestation_only():
    assert set(CHECK_KIND.keys()) == set(ALL_CHECKS)
    for kind in CHECK_KIND.values():
        assert kind in (CheckKind.AUTOMATED, CheckKind.ATTESTATION_ONLY)


# ---------------------------------------------------------------------------
# LIVE_ELIGIBLE: a correctly-derived property, never independently settable
# ---------------------------------------------------------------------------


def test_live_eligible_false_until_every_check_passes(store: SignalStore):
    provider_id, source_id = _register_provider_source(store)
    result = store.is_scope_live_eligible(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    assert result["live_eligible"] is False
    assert set(result["missing_checks"]) == {c.value for c in ALL_CHECKS}


def test_live_eligible_cannot_be_set_independently_of_its_checks(store: SignalStore):
    """There is no column/setter anywhere that lets a caller flip
    LIVE_ELIGIBLE directly -- it is ALWAYS recomputed from the real,
    current check rows. Proven here by exhaustively marking every
    ATTESTATION_ONLY check PASS (the AUTOMATED ones stay whatever real
    evidence computes them to, which is NOT_RUN in this empty test
    store) and confirming live_eligible is still False, precisely
    because the automated checks weren't (and can't be) faked to PASS."""
    provider_id, source_id = _register_provider_source(store)
    store.ensure_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    for check in ALL_CHECKS:
        if CHECK_KIND[check] == CheckKind.ATTESTATION_ONLY:
            store.record_certification_check(
                provider_id=provider_id,
                source_id=source_id,
                asset_class="equity",
                account_route="acct1",
                check_name=check.value,
                status="PASS",
                evidence={"note": f"manually verified {check.value}"},
                checked_by="op@example.com",
            )
    result = store.is_scope_live_eligible(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    assert result["live_eligible"] is False
    automated_names = {c.value for c in ALL_CHECKS if CHECK_KIND[c] == CheckKind.AUTOMATED}
    assert set(result["missing_checks"]) == automated_names

    # And there is genuinely no way to hand-set it PASS -- SignalStore's
    # own write path for AUTOMATED checks doesn't exist (see
    # test_automated_check_cannot_be_manually_recorded).
    assert not hasattr(store, "set_live_eligible")


def test_is_live_eligible_pure_function_over_check_rows():
    rows = [{"check_name": c.value, "status": CheckStatus.PASS.value} for c in ALL_CHECKS]
    eligible, missing = is_live_eligible(rows)
    assert eligible is True
    assert missing == []

    rows[0]["status"] = CheckStatus.FAIL.value
    eligible, missing = is_live_eligible(rows)
    assert eligible is False
    assert len(missing) == 1


def test_live_eligible_composes_with_but_never_reads_route_qualification():
    """This module's own docstring hard rule: nothing in
    app/certification.py imports app/qualification.py or app/engine.py
    -- LIVE_ELIGIBLE is computed purely from certification_checks rows,
    never composed with (or aware of) the route-qualification ladder at
    the Python level. The two are combined only by a CALLER checking
    both independently."""
    import app.certification as certification_module

    source = inspect.getsource(certification_module)
    assert "app.qualification" not in source
    assert "app.engine" not in source


# ---------------------------------------------------------------------------
# Shadow mode: structurally incapable of submitting a real order
# ---------------------------------------------------------------------------


def test_shadow_evaluation_module_imports_no_broker_adapter():
    """AST-based static proof that app/shadow_mode.py never imports
    anything from app.brokers -- mirrors app/phone_escalation.py's own
    `test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing`
    precedent for a different structural guarantee."""
    import app.shadow_mode as shadow_mode_module

    source = inspect.getsource(shadow_mode_module)
    tree = ast.parse(source)
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
    assert not any(m.startswith("app.brokers") for m in imported_modules), (
        f"app/shadow_mode.py imports from app.brokers ({imported_modules!r}) -- shadow mode must never be able "
        "to reach a broker adapter at all"
    )


def test_shadow_evaluation_never_calls_save_order_result():
    """Behavioral proof: evaluate_shadow takes no store/broker at all --
    its signature is (Signal, RoutingConfig, ...) -- so there is no
    object in scope it COULD call save_order_result/place_order on."""
    sig = inspect.signature(evaluate_shadow)
    param_names = list(sig.parameters.keys())
    assert "store" not in param_names
    assert "broker" not in param_names
    assert "brokers" not in param_names


def _routing_with_one_account(*, symbol_filter=None) -> RoutingConfig:
    account = DestinationAccount(account_id="acct1", broker="paper", multiplier=2.0)
    rule = RoutingRule(source="tradealgo", destinations=["acct1"], symbol_filter=symbol_filter)
    return RoutingConfig(rules=[rule], accounts={"acct1": account})


def test_shadow_mode_computes_hypothetical_order_without_submitting():
    routing = _routing_with_one_account()
    signal = Signal(
        source="tradealgo",
        symbol="SPY",
        side=Side.BUY,
        asset_class=AssetClass.OPTION,
        quantity=5,
        price=1.42,
        stop_loss=1.05,
        take_profit=1.80,
    )
    intents = evaluate_shadow(signal, routing, certification_version="4")
    assert len(intents) == 1
    intent = intents[0]
    assert isinstance(intent, ShadowOrderIntent)
    assert intent.account_id == "acct1"
    assert intent.quantity == 10  # 5 * multiplier 2.0
    assert intent.expected_entry == 1.42
    assert intent.stop_price == 1.05
    assert intent.targets == [1.80]
    assert intent.policy_reference == "tradealgo policy v4"
    assert "Would buy" in intent.reasoning

    row = to_result_row(intent)
    assert row["signal_id"] == signal.id
    assert row["policy_reference"] == "tradealgo policy v4"


def test_shadow_mode_reuses_real_routing_and_sizing_not_a_reimplementation():
    """Static proof this module calls the SAME real functions
    app/engine.py's own _handle_signal calls -- never a separately
    maintained/reimplemented sizing or routing algorithm."""
    import app.shadow_mode as shadow_mode_module

    source = inspect.getsource(shadow_mode_module)
    assert "from app.risk import size_for_account, symbol_for_account" in source
    assert "from app.routing import RoutingConfig" in source
    assert "destinations_for(" in source
    assert "size_for_account(" in source
    assert "symbol_for_account(" in source

    # And the exact same objects app/engine.py imports.
    import app.engine as engine_module

    assert engine_module.size_for_account is shadow_mode_module.size_for_account
    assert engine_module.symbol_for_account is shadow_mode_module.symbol_for_account


def test_shadow_mode_records_never_touch_orders_table(store: SignalStore):
    """End-to-end: recording a shadow result writes ONLY to
    shadow_mode_results, never to `orders`/`positions`."""
    routing = _routing_with_one_account()
    signal = Signal(source="tradealgo", symbol="SPY", side=Side.BUY, asset_class=AssetClass.OPTION, quantity=5, price=1.42)
    intents = evaluate_shadow(signal, routing)
    for intent in intents:
        store.record_shadow_mode_result(to_result_row(intent))

    results = store.list_shadow_mode_results(provider_id="tradealgo")
    assert len(results) == 1
    with store._connect() as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM orders WHERE signal_id = ?", (signal.id,)).fetchone()[0]
    assert order_count == 0


def test_engine_shadow_mode_gate_never_runs_for_certified_providers():
    from app.engine import SignalCopierEngine

    assert "certified" not in SignalCopierEngine._SHADOW_MODE_CERTIFICATION_STATES
    assert SignalCopierEngine._SHADOW_MODE_CERTIFICATION_STATES == {"uncertified", "draft", "tested", "shadow"}


# ---------------------------------------------------------------------------
# Scorecard: honest, computed-at-read-time, never fabricated
# ---------------------------------------------------------------------------


def test_scorecard_is_insufficient_data_for_a_provider_with_no_checks(store: SignalStore):
    store.register_provider(provider_id="brand_new", display_name="Brand New", status="onboarding")
    scorecard = compute_scorecard(store, "brand_new")
    assert scorecard["note"].startswith("insufficient_data")
    for category in scorecard["categories"].values():
        assert category["percent"] is None
        assert category["note"] == "insufficient_data"


def test_scorecard_percentages_are_computed_from_real_check_rows_not_fabricated(store: SignalStore):
    provider_id, source_id = _register_provider_source(store)
    store.ensure_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    # Pass every attestation-only check with real evidence; leave
    # automated checks at whatever this empty test store honestly
    # computes them to (NOT_RUN -- no real connections/orders/signals
    # exist yet).
    attestation_checks = [c for c in ALL_CHECKS if CHECK_KIND[c] == CheckKind.ATTESTATION_ONLY]
    for check in attestation_checks:
        store.record_certification_check(
            provider_id=provider_id,
            source_id=source_id,
            asset_class="equity",
            account_route="acct1",
            check_name=check.value,
            status="PASS",
            evidence={"note": f"verified {check.value}"},
            checked_by="op@example.com",
        )
    scorecard = compute_scorecard(store, provider_id)
    execution_tests = scorecard["categories"]["execution_tests"]
    # execution_tests mixes attestation-only (now PASS) and automated
    # (still NOT_RUN) checks -- the real percentage must reflect that
    # mix honestly, not report 100% just because the human-attested
    # subset passed.
    assert execution_tests["percent"] is not None
    assert 0 < execution_tests["percent"] < 100
    assert execution_tests["pass_count"] < execution_tests["applicable_count"]

    # Outstanding list names the real, specific still-not-PASS checks.
    outstanding_names = {o["check_name"] for o in scorecard["outstanding"]}
    for check in ALL_CHECKS:
        if CHECK_KIND[check] == CheckKind.AUTOMATED:
            assert check.value in outstanding_names


def test_scorecard_never_reports_100_percent_for_a_category_with_a_failing_check(store: SignalStore):
    provider_id, source_id = _register_provider_source(store)
    store.ensure_certification_checks(
        provider_id=provider_id, source_id=source_id, asset_class="equity", account_route="acct1"
    )
    store.record_certification_check(
        provider_id=provider_id,
        source_id=source_id,
        asset_class="equity",
        account_route="acct1",
        check_name="disconnect_recovery",
        status="FAIL",
        evidence={"note": "reconnect did not recover within SLA"},
        checked_by="op@example.com",
    )
    scorecard = compute_scorecard(store, provider_id)
    connectivity = scorecard["categories"]["connectivity"]
    assert connectivity["percent"] is not None
    assert connectivity["percent"] < 100
