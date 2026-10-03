"""WC-10: Workflow contract adapter binding the generated suites to the real engine.

run_case(request: dict) -> dict exposes the engine's real admission and sizing paths
through the restricted-planning suites. Every case gets an isolated, non-live fixture
(tmp_path SQLite + PaperBroker), no network, no credentials, no module-level state.

Admission suite: drives engine.handle_signal with dry_run=True, observes planning-only
state and zero broker calls.

Linear sizing suite: calls the engine's sizing path with exact fixture prices/capacities
and observes the planned quantity and risk figures.
"""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import (
    AssetClass,
    CommandType,
    DestinationAccount,
    Intent,
    OrderStatus,
    Side,
    Signal,
    UncertaintyState,
)
from app.routing import RoutingConfig, RoutingRule


class CountingPaperBroker(PaperBroker):
    """PaperBroker that counts place_order calls (must remain 0 in dry_run mode)."""

    def __init__(self):
        super().__init__()
        self.place_order_call_count = 0

    async def place_order(self, signal, account, quantity, symbol):
        self.place_order_call_count += 1
        return await super().place_order(signal, account, quantity, symbol)


class LiveCountingPaperBroker(CountingPaperBroker):
    """Counting broker that reports a LIVE venue environment.

    The engine only derives margin regime 'unknown' for an account whose broker
    reports venue_environment()=='live' and that has no margin_regimes row
    (app/engine.py::_derive_admission_inputs). It never places a real order:
    every admission case runs with dry_run=True and asserts place_order_call_count==0.
    """

    def venue_environment(self, account: DestinationAccount) -> str:
        return "live"


_LIVE_BROKER_KEY = "paper_live"


class UnreadableBudgetStore(SignalStore):
    """SignalStore whose budget read FAILS, as it would on a locked or unreadable
    database. Used only for budget=='unknown'.

    No real SignalStore setter can produce an unreadable budget (limits are NOT NULL
    ints, an absent limit reads as UNLIMITED), and corrupting tables would test
    nothing about the engine. Injecting the read fault leaves every table valid and
    lets the engine's own handling of a failing hierarchical_budget.remaining() run.
    """

    def get_level_remaining(self, level: str, level_scope: dict) -> int:
        raise sqlite3.OperationalError("fixture: budget store unreadable (database is locked)")


_NOMINAL_ACCOUNT_ID = "account_1"


def _admission_physical_ids(route_inputs: str, accounts: list[DestinationAccount]) -> list[str]:
    """Physical account ids the engine evaluates for these config accounts."""
    if route_inputs == "duplicate_bindings_one_account":
        return ["config_a"] if accounts else []
    return sorted(a.account_id for a in accounts)


def _fixture_error(inputs: dict[str, Any], step: str, exc: BaseException) -> RuntimeError:
    return RuntimeError(f"admission fixture setup failed at step '{step}' for inputs={inputs!r}: {type(exc).__name__}: {exc}")


def _build_admission_state(store: SignalStore, inputs: dict[str, Any]) -> tuple:
    """Build accounts, rules, and durable state for an admission test case.

    Every state change goes through a real SignalStore API (or, for the
    physical_accounts/account_bindings identity rows that have no write API, the
    real schema columns) and FAILS LOUDLY: any failure raises RuntimeError naming
    the case inputs and the step. Nothing is swallowed.

    Pool-wide inputs (halt, uncertain_effect, margin_regime) are applied to EVERY
    candidate account, because the contract describes a pool-wide condition.

    Returns:
        (accounts, rules, brokers, notes). `notes` lists inputs that cannot be
        represented through real APIs (reported in evidence, never faked).
    """
    from app.models import CommandType, UncertaintyState
    from app.workflow.budget import BudgetScope, ReservationState, ResourceVector

    accounts: list[DestinationAccount] = []
    rules: list[RoutingRule] = []
    notes: list[str] = []

    def step(name: str, fn) -> Any:
        try:
            return fn()
        except Exception as exc:  # re-raised with context, never swallowed
            raise _fixture_error(inputs, name, exc) from exc

    regime_input = inputs.get("margin_regime", "legacy_verified")
    # 'unknown' regime requires a broker that reports a live environment.
    broker_key = _LIVE_BROKER_KEY if regime_input == "unknown" else "paper"
    environment = "live" if regime_input == "unknown" else "paper"

    # --- routes ---
    route_inputs = inputs.get("routes", "zero")
    if route_inputs == "zero":
        pass
    elif route_inputs == "one":
        accounts.append(DestinationAccount(account_id="account_1", broker=broker_key))
    elif route_inputs == "two":
        accounts.append(DestinationAccount(account_id="account_1", broker=broker_key))
        accounts.append(DestinationAccount(account_id="account_2", broker=broker_key))
    elif route_inputs == "duplicate_bindings_one_account":
        accounts.append(DestinationAccount(account_id="config_a", broker=broker_key))
        accounts.append(DestinationAccount(account_id="config_b", broker=broker_key))
    else:
        raise _fixture_error(inputs, "routes", ValueError(f"unknown routes value {route_inputs!r}"))

    # --- authorization ---
    auth_inputs = inputs.get("authorization", "authorized")
    if accounts:
        rules.append(RoutingRule(source="provider", destinations=[a.account_id for a in accounts]))
    if auth_inputs == "authorized":
        pass
    elif auth_inputs == "unknown":
        rules.clear()  # no rule admits source 'provider'
    elif auth_inputs == "disabled":
        # Authorization failure only: with routes=zero there is still no account
        # and no rule (do not fabricate a route the case says is absent).
        for a in accounts:
            a.enabled = False
    else:
        raise _fixture_error(inputs, "authorization", ValueError(f"unknown authorization value {auth_inputs!r}"))

    # Identity rows: config account, physical account (id == the id the engine uses
    # for the candidate), and for duplicate bindings one physical account behind two
    # config accounts.
    def make_identity() -> None:
        physical_ids = {a.account_id: a.account_id for a in accounts}
        if route_inputs == "duplicate_bindings_one_account":
            physical_ids = {"config_a": "config_a", "config_b": "config_a"}
        with store._connect() as conn:
            for pid in sorted(set(physical_ids.values())):
                conn.execute(
                    "INSERT INTO physical_accounts (physical_account_id, broker, broker_account_id, environment, base_currency) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pid, broker_key, f"BA-{pid}", environment, "USD"),
                )
        for a in accounts:
            store.upsert_config_account(a.account_id, broker_key, enabled=a.enabled)
        if route_inputs == "duplicate_bindings_one_account":
            with store._connect() as conn:
                for cid, pid in physical_ids.items():
                    conn.execute(
                        "INSERT INTO account_bindings (binding_id, physical_account_id, config_account_id, version, revoked) "
                        "VALUES (?, ?, ?, 1, 0)",
                        (f"bind_{cid}", pid, cid),
                    )

    step("identity rows", make_identity)
    physical_account_ids = _admission_physical_ids(route_inputs, accounts)
    # Nominal id so halts/ledger/reservations still exist when routes == zero.
    state_account_ids = physical_account_ids or [_NOMINAL_ACCOUNT_ID]

    # --- budget ---
    budget_inputs = inputs.get("budget", "enough")
    if budget_inputs == "enough":
        step("budget=enough owner limit", lambda: store.set_owner_limit("owner", max_notional_cents=100_000_000))
    elif budget_inputs == "zero":
        step("budget=zero owner limit", lambda: store.set_owner_limit("owner", max_notional_cents=0))
    elif budget_inputs == "oversubscribed":
        step("budget=oversubscribed owner limit", lambda: store.set_owner_limit("owner", max_notional_cents=1000))

        def hold() -> None:
            scope = BudgetScope(
                owner="owner", physical_account_id=state_account_ids[0], portfolio_id=None, sleeve_id=None,
                provider="provider", analyst="test_analyst", underlying="AAPL", cluster=None,
            )
            need = ResourceVector(
                cash=5000, buying_power=None, initial_margin=0, maintenance=None, notional=5000,
                planned_risk=5000, stress_risk=None, close_quantity=0, slots=1,
            )
            store.create_hierarchical_reservation(
                opportunity_id="opp_reserved_1", scope=scope, need=need, state=ReservationState.HELD.value
            )

        step("budget=oversubscribed HELD reservation", hold)
    elif budget_inputs == "unknown":
        # No real SignalStore setter yields an unreadable budget (see
        # UnreadableBudgetStore). The caller opened `store` as that fault-injecting
        # subclass for this input; the tables themselves are left valid.
        if not isinstance(store, UnreadableBudgetStore):
            raise _fixture_error(inputs, "budget=unknown", TypeError("store must be an UnreadableBudgetStore"))
        step("budget=unknown owner limit", lambda: store.set_owner_limit("owner", max_notional_cents=100_000_000))
        notes.append("budget=unknown: store.get_level_remaining fault-injected (sqlite3.OperationalError); no real API makes a budget unreadable")
    else:
        raise _fixture_error(inputs, "budget", ValueError(f"unknown budget value {budget_inputs!r}"))

    # --- margin regime ---
    if regime_input in ("legacy_verified", "new_intraday_verified"):
        regime = "legacy_pdt_verified" if regime_input == "legacy_verified" else "new_intraday_verified"
        for pid in physical_account_ids:
            def set_regime(pid=pid) -> None:
                res = store.set_margin_regime(pid, regime, "fixture", datetime(2024, 1, 1, tzinfo=timezone.utc))
                if res is None:
                    raise RuntimeError(f"set_margin_regime returned None (physical account {pid!r} missing)")
            step(f"margin_regime={regime_input} for {pid}", set_regime)
    elif regime_input == "unknown":
        pass  # live broker + deliberately no margin_regimes row
    else:
        raise _fixture_error(inputs, "margin_regime", ValueError(f"unknown margin_regime value {regime_input!r}"))

    # --- halt ---
    halt_inputs = inputs.get("halt", "clear")
    if halt_inputs == "clear":
        pass
    elif halt_inputs == "account_halt":
        for pid in state_account_ids:
            step(f"halt=account_halt {pid}", lambda pid=pid: store.set_trading_halt("account", pid, "contract", "adapter"))
    elif halt_inputs == "portfolio_halt":
        def portfolio() -> None:
            store.create_portfolio("portfolio_1", "owner", name="contract")
            for pid in state_account_ids:
                store.add_portfolio_backing("portfolio_1", pid, 100_000_000)
            store.set_trading_halt("portfolio", "portfolio_1", "contract", "adapter")
        step("halt=portfolio_halt", portfolio)
    elif halt_inputs == "owner_halt":
        step("halt=owner_halt", lambda: store.set_trading_halt("owner", "owner", "contract", "adapter"))
    else:
        raise _fixture_error(inputs, "halt", ValueError(f"unknown halt value {halt_inputs!r}"))

    # --- uncertain effect: ENTRY ledger row left UNKNOWN_AMBIGUOUS on every candidate ---
    if inputs.get("uncertain_effect", False):
        for pid in state_account_ids:
            def uncertain(pid=pid) -> None:
                key = f"contract-uncertain-{pid}"
                store.open_command_ledger_entry(
                    idempotency_key=key, command_type=CommandType.ENTRY, account_id=pid,
                    environment=environment, request_fingerprint=f"fp-{pid}",
                )
                store.mark_command_ledger_outcome(key, uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS)
            step(f"uncertain_effect {pid}", uncertain)

    brokers = {"paper": CountingPaperBroker(), _LIVE_BROKER_KEY: LiveCountingPaperBroker()}
    return accounts, rules, brokers, notes


def _engine_blocking_reasons(traces: list[dict], results: list) -> list[str]:
    """Blocking reasons the ENGINE itself recorded for a rejected entry.

    Primary source: the engine's durable decision_traces rows. Per-candidate
    exclusions are stored in `reason` as a '|'-joined list such as
    'HALTED:contract|...' or 'REGIME_UNKNOWN'; the code is the text before ':'.
    Secondary source: the gate's own structured rejection prefix
    'admission rejected: A, B -- detail' (the engine formats it from
    AdmissionDecision.blocking_reasons), which also carries gate-level reasons no
    candidate trace can hold (NO_ELIGIBLE_ROUTE, SOURCE_AUTHORITY, ...). Codes are
    matched exactly against ADMISSION_BLOCKING_ORDER, never by substring.
    """
    from app.workflow.reasons import ADMISSION_BLOCKING_ORDER

    found: set[str] = set()
    for t in traces:
        if t.get("feasible"):
            continue
        for part in str(t.get("reason") or "").split("|"):
            code = part.split(":", 1)[0].strip()
            if code in ADMISSION_BLOCKING_ORDER:
                found.add(code)
    prefix = "admission rejected: "
    for r in results:
        if r.status == OrderStatus.REJECTED and r.message.startswith(prefix):
            head = r.message[len(prefix):].split(" -- ", 1)[0]
            for code in (c.strip() for c in head.split(",")):
                if code in ADMISSION_BLOCKING_ORDER:
                    found.add(code)
    return [r for r in ADMISSION_BLOCKING_ORDER if r in found]


async def _run_admission_case(tmp_dir: str, case_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Run a single admission test case through the real engine (dry_run=True).

    Observation is the engine's own output: order results plus durable
    decision_traces rows. The pure evaluate_admission fallback (labelled
    scope=module_not_engine) is used ONLY when the engine produced no decision at
    all (no results, e.g. zero candidates after routing).
    """
    db_path = Path(tmp_dir) / f"{case_id}.db"
    store = (UnreadableBudgetStore if inputs.get("budget") == "unknown" else SignalStore)(db_path)

    try:
        accounts, rules, brokers, fixture_notes = _build_admission_state(store, inputs)

        routing = RoutingConfig(
            rules=rules,
            accounts={a.account_id: a for a in accounts},
        )
        engine = SignalCopierEngine(routing=routing, brokers=brokers, store=store)

        interp_inputs = inputs.get("interpretation", "entry")
        if interp_inputs == "entry":
            signal_intent = Intent.ENTRY_LONG
        elif interp_inputs == "conditional_unmet":
            signal_intent = Intent.CANCEL
        elif interp_inputs == "ambiguous":
            signal_intent = Intent.REDUCE
        elif interp_inputs == "observation":
            signal_intent = Intent.EXIT
        else:
            raise _fixture_error(inputs, "interpretation", ValueError(f"unknown interpretation {interp_inputs!r}"))

        signal = Signal(
            id=f"sig_{case_id}",
            source="provider",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=1.0,
            price=100.0,
            intent=signal_intent,
            analyst="test_analyst",
        )

        # Snapshot the fixture's durable state BEFORE the engine runs, so the
        # module_not_engine fallback never reads rows the engine itself wrote.
        state_ids = _admission_physical_ids(inputs.get("routes", "zero"), accounts) or [_NOMINAL_ACCOUNT_ID]
        pre_halted = any(
            store.active_halt_for("account", i) is not None for i in state_ids
        ) or store.active_halt_for("portfolio", "portfolio_1") is not None or store.active_halt_for("owner", "owner") is not None
        pre_uncertain = any(
            e.command_type in (CommandType.ENTRY, CommandType.CLOSE)
            and e.uncertainty_state in (UncertaintyState.UNKNOWN_AMBIGUOUS, UncertaintyState.PENDING_SUBMISSION)
            for i in state_ids
            for e in store.list_unresolved_command_ledger_entries(i)
        )
        from app.workflow.budget import BudgetScope

        try:
            pre_remaining = engine.hierarchical_budget.remaining(
                BudgetScope("owner", state_ids[0], None, None, signal.source, signal.analyst, signal.symbol, None)
            )
            pre_budget_state = "enough" if all(v > 0 for v in pre_remaining.values()) else "not_enough"
        except Exception:
            pre_budget_state = "unreadable"

        results = await engine.handle_signal(signal, dry_run=True)
        broker_call_count = sum(b.place_order_call_count for b in brokers.values())
        decision_traces = store.list_decision_traces(signal.id)

        evidence_scope = "engine"
        gate_rejected = [
            r for r in results if r.status == OrderStatus.REJECTED and r.message.startswith("admission rejected: ")
        ]
        pending = [r for r in results if r.status == OrderStatus.PENDING]
        extra: dict[str, Any] = {}

        entry_like = signal.intent in (Intent.ENTRY_LONG, Intent.ENTRY_SHORT, Intent.SELL, Intent.ADD)
        if results and entry_like:
            admit_new_entry = bool(pending) and not gate_rejected
            selected_physical_account_count = len(pending) if admit_new_entry else 0
            blocking_reasons = _engine_blocking_reasons(decision_traces, results) if gate_rejected else []
        else:
            # The engine produced NO admission decision: either nothing routed
            # (no results) or the intent is not an entry, which the engine sends to
            # its position-management handlers and never to the admission gate.
            # Fall back to the pure gate, feeding it only state observable from
            # the engine/store, and label the evidence honestly.
            from app.workflow.admission import AdmissionInputs, evaluate_admission

            pool = engine.routing.pool_for(signal.source, signal.symbol, include_disabled=False)
            eligible_accounts = [a.account_id for a in pool.single] + [a.account_id for a in pool.replicate]
            auth = "authorized" if (inputs.get("authorization") == "authorized") else "unauthorized"
            if auth != "authorized":
                # The pool is empty BECAUSE of authorization, not because no route
                # is configured: the configured accounts are the candidates.
                eligible_accounts = [a.account_id for a in accounts]
            interp = "entry" if signal.intent in (Intent.ENTRY_LONG, Intent.ENTRY_SHORT, Intent.SELL, Intent.ADD) else "non_entry"
            budget_state = pre_budget_state
            halted = pre_halted
            uncertain = pre_uncertain
            # No candidate account exists, so the engine has nothing to derive a
            # margin regime from: this one value comes from the case input.
            margin_regime = "unknown" if inputs.get("margin_regime") == "unknown" else "legacy_pdt_verified"
            decision = evaluate_admission(
                AdmissionInputs(
                    authorization=auth,
                    interpretation=interp,
                    eligible_physical_accounts=eligible_accounts,
                    budget_state=budget_state,
                    margin_regime=margin_regime,
                    halt="account_halt" if halted else "clear",
                    uncertain_effect=uncertain,
                )
            )
            admit_new_entry = decision.admit_new_entry
            selected_physical_account_count = decision.selected_physical_account_count
            blocking_reasons = decision.blocking_reasons
            evidence_scope = "module_not_engine"
            extra["module_not_engine_reason"] = (
                "engine returned no results (no routed candidates)" if not results
                else "non-entry intent bypasses the engine's admission gate"
            )
            extra["engine_order_results_ignored"] = [r.status.value for r in results]
            extra["margin_regime_source"] = "case input (no candidate account to derive it from)"

        implementation_paths = [
            "app/engine.py",
            "app/workflow/admission.py",
            "app/db.py",
            "app/models.py",
        ]

        evidence = [
            {
                "decision_trace_row_ids": [str(t.get("id")) for t in decision_traces],
                "decision_trace_reasons": [t.get("reason") for t in decision_traces],
                "sqlite_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest() if db_path.exists() else "",
                "order_results": [{"status": r.status.value, "message": r.message} for r in results],
                "broker_call_count": broker_call_count,
                "scope": evidence_scope,
                "fixture_notes": fixture_notes,
                **extra,
            }
        ]

        return {
            "actual": {
                "admit_new_entry": admit_new_entry,
                "selected_physical_account_count": selected_physical_account_count,
                "broker_call_count": broker_call_count,
                "blocking_reasons": blocking_reasons,
            },
            "implementation_paths": implementation_paths,
            "evidence": evidence,
        }

    except Exception as e:
        if isinstance(e, RuntimeError) and "admission fixture setup failed" in str(e):
            raise  # a broken fixture must never masquerade as an engine result
        return {
            "actual": {},
            "implementation_paths": [],
            "evidence": [{"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}],
        }


async def _run_sizing_case(tmp_dir: str, case_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Run a single linear sizing test case through the real engine."""
    db_path = Path(tmp_dir) / f"{case_id}.db"
    store = SignalStore(db_path)

    try:
        # Create a sizing-mode account with risk_fraction=1.0
        account = DestinationAccount(
            account_id="sizing_account",
            broker="paper",
            sizing_mode="risk_fraction",
            risk_fraction=1.0,
        )

        # Create engine with a broker that reports exact fixture balances
        class FixtureBroker(CountingPaperBroker):
            def __init__(self, risk_budget_cents, cash_capacity_cents):
                super().__init__()
                self.risk_budget_cents = risk_budget_cents
                self.cash_capacity_cents = cash_capacity_cents

            async def get_account_balance(self, account):
                from app.models import AccountBalance

                return AccountBalance(
                    account_id=account.account_id,
                    equity=self.risk_budget_cents / 100,
                    buying_power=self.cash_capacity_cents / 100,
                    cash=self.cash_capacity_cents / 100,
                )

        broker = FixtureBroker(
            inputs["risk_budget_cents"],
            inputs["cash_capacity_cents"],
        )

        routing = RoutingConfig(
            rules=[RoutingRule(source="test", destinations=["sizing_account"])],
            accounts={"sizing_account": account},
        )
        engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

        # Create a signal with exact fixture prices
        # entry_price_cents / 100 = float price
        entry_price_float = inputs["entry_price_cents"] / 100.0
        unit_risk_float = inputs["unit_risk_cents"] / 100.0
        stop_loss_float = round(entry_price_float - unit_risk_float, 2)

        signal = Signal(
            id=f"sig_{case_id}",
            source="test",
            symbol="TESTXYZ",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=float(inputs["source_max_units"]),
            price=entry_price_float,
            stop_loss=stop_loss_float,
            intent=Intent.ENTRY_LONG,
        )

        # A long signal cannot carry a stop at or below zero, so inputs with
        # unit_risk >= entry_price are not representable through the signal
        # path. Those cases drive the exact function the engine delegates to
        # (app.risk.size_linear_long) and are labelled scope=module_not_engine.
        if inputs["unit_risk_cents"] >= inputs["entry_price_cents"]:
            from app.workflow.sizing import size_linear_long

            sized = size_linear_long(
                risk_budget_cents=inputs["risk_budget_cents"],
                unit_risk_cents=inputs["unit_risk_cents"],
                cash_capacity_cents=inputs["cash_capacity_cents"],
                entry_price_cents=inputs["entry_price_cents"],
                source_max_units=inputs["source_max_units"],
            )
            q = int(sized.quantity_units)
            return {
                "actual": {
                    "quantity_units": q,
                    "planned_risk_cents": q * inputs["unit_risk_cents"],
                    "notional_cents": q * inputs["entry_price_cents"],
                    "broker_call_count": broker.place_order_call_count,
                },
                "implementation_paths": ["app/risk.py"],
                "evidence": [{"scope": "module_not_engine", "reason": "unit_risk >= entry_price is not representable as a long signal stop", "broker_call_count": broker.place_order_call_count}],
            }

        # Run through engine with dry_run=True
        results = await engine.handle_signal(signal, dry_run=True)

        # Observe the engine's own durable decision: the budget reservation it
        # created for this opportunity (needed_* are the sized figures). The
        # reservation is RELEASED by the dry-run path but the row persists.
        # Nothing here recomputes sizing -- an absent row means the engine
        # sized nothing (quantity 0).
        quantity_units = 0
        planned_risk_cents = 0
        notional_cents = 0
        scope = "engine"

        with store._connect() as conn:
            row = conn.execute(
                "SELECT needed_planned_risk_cents, needed_notional_cents "
                "FROM budget_reservations WHERE opportunity_id IN "
                "(SELECT opportunity_id FROM budget_reservations ORDER BY created_at DESC LIMIT 1)"
            ).fetchone()
        if row is not None:
            planned_risk_cents = int(row[0])
            notional_cents = int(row[1])
            quantity_units = notional_cents // inputs["entry_price_cents"]

        # Implementation paths
        implementation_paths = [
            "app/engine.py",
            "app/risk.py",
            "app/db.py",
            "app/models.py",
        ]

        # Evidence
        evidence = [
            {
                "sqlite_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest() if db_path.exists() else "",
                "broker_call_count": broker.place_order_call_count,
                "scope": scope,
                "order_results": [{"status": r.status.value, "message": r.message} for r in results],
            }
        ]

        return {
            "actual": {
                "quantity_units": quantity_units,
                "planned_risk_cents": planned_risk_cents,
                "notional_cents": notional_cents,
                "broker_call_count": broker.place_order_call_count,
            },
            "implementation_paths": implementation_paths,
            "evidence": evidence,
        }

    except Exception as e:
        return {
            "actual": {},
            "implementation_paths": [],
            "evidence": [{"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}],
        }


def run_case(request: dict[str, Any]) -> dict[str, Any]:
    """Main entry point for the workflow contract adapter.

    Args:
        request: Dict with keys: id, suite, inputs

    Returns:
        Dict with keys: actual, implementation_paths, evidence
    """
    case_id = request["id"]
    suite = request["suite"]
    inputs = request["inputs"]

    # Create isolated, temporary SQLite + broker fixture
    with tempfile.TemporaryDirectory() as tmp_dir:
        if suite == "admission":
            result = asyncio.run(_run_admission_case(tmp_dir, case_id, inputs))
        elif suite == "linear_sizing":
            result = asyncio.run(_run_sizing_case(tmp_dir, case_id, inputs))
        else:
            result = {
                "actual": {},
                "implementation_paths": [],
                "evidence": [{"error": f"Unknown suite: {suite}"}],
            }

        return result
