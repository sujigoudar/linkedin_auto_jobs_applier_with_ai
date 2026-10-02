"""Admission gate: determine if a new entry is eligible to proceed to sizing and selection.

This module implements the pure eligibility decision described in
docs/workflow-contract/WORKFLOW_SPECIFICATION.md §2 steps 11-15 (source age,
event gates, executable quote, trigger) and §5.1 (account eligibility filters).

A signal's admission is independent of the specific chosen account (pure decision
on input conditions); selection comes later and persists DecisionTrace rows.
All decision codes are ordered per ADMISSION_BLOCKING_ORDER.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.workflow.reasons import ADMISSION_BLOCKING_ORDER, Reason


@dataclass(frozen=True)
class AdmissionInputs:
    """Computed signal eligibility snapshot for pure admission evaluation.

    Attributes:
        authorization: "authorized" if source/analyst has valid entitlement;
            any other value blocks with SOURCE_AUTHORITY.
        interpretation: "entry" if this is a new-entry instruction;
            any other value blocks with NOT_CURRENT_ACTIONABLE_ENTRY.
        eligible_physical_accounts: List of candidate physical account IDs
            that passed the full filter sequence (§5.1). Empty list →
            NO_ELIGIBLE_ROUTE blocking reason.
        budget_state: "enough" if risk/capital constraints are satisfied;
            any other value blocks with BUDGET_NOT_ADMISSIBLE.
        margin_regime: One of "legacy_pdt_verified", "new_intraday_verified",
            or "unknown". "unknown" blocks with REGIME_UNKNOWN.
        halt: One of "clear", "account_halt", "portfolio_halt", "owner_halt".
            Non-"clear" values block with HALTED.
        uncertain_effect: Boolean. True blocks with UNCERTAIN_EFFECT
            (submission may have been accepted but status unresolved).
    """

    authorization: str
    interpretation: str
    eligible_physical_accounts: list[str]
    budget_state: str
    margin_regime: str
    halt: str
    uncertain_effect: bool


@dataclass(frozen=True)
class AdmissionDecision:
    """Admission evaluation result.

    Attributes:
        admit_new_entry: True only if every blocking reason is absent.
        selected_physical_account_count: When admit_new_entry is True, the
            number of eligible candidates (always ≥ 1). When False, always 0.
        blocking_reasons: List of reason codes (from ADMISSION_BLOCKING_ORDER)
            in strict order. Empty list means admitted. Never out of order.
        broker_call_count: Always 0 in current pure-logic implementation.
            Reserved for future integration when address queries call brokers.
    """

    admit_new_entry: bool
    selected_physical_account_count: int
    blocking_reasons: list[str]
    broker_call_count: int


def evaluate_admission(inputs: AdmissionInputs) -> AdmissionDecision:
    """Pure admission decision: no I/O, deterministic, idempotent.

    Checks eligibility in order of ADMISSION_BLOCKING_ORDER. The first
    blocker ends the evaluation; all blocking reasons are still collected
    (for operator reference) and returned in the declared order.

    Args:
        inputs: Eligibility snapshot (no live state, no broker calls).

    Returns:
        AdmissionDecision with blocking_reasons in ADMISSION_BLOCKING_ORDER
        and admit_new_entry True only when blocking_reasons is empty.
    """
    blocking_reasons = []

    # Check SOURCE_AUTHORITY: is the source/analyst authorized?
    if inputs.authorization != "authorized":
        blocking_reasons.append(Reason.SOURCE_AUTHORITY.value)

    # Check NOT_CURRENT_ACTIONABLE_ENTRY: is this a new-entry instruction?
    if inputs.interpretation != "entry":
        blocking_reasons.append(Reason.NOT_CURRENT_ACTIONABLE_ENTRY.value)

    # Check NO_ELIGIBLE_ROUTE: are there any candidate accounts?
    if not inputs.eligible_physical_accounts:
        blocking_reasons.append(Reason.NO_ELIGIBLE_ROUTE.value)

    # Check BUDGET_NOT_ADMISSIBLE: do risk/capital constraints allow?
    if inputs.budget_state != "enough":
        blocking_reasons.append(Reason.BUDGET_NOT_ADMISSIBLE.value)

    # Check REGIME_UNKNOWN: is margin regime known (fail closed)?
    if inputs.margin_regime == "unknown":
        blocking_reasons.append(Reason.REGIME_UNKNOWN.value)

    # Check HALTED: is entry halted at any level?
    if inputs.halt != "clear":
        blocking_reasons.append(Reason.HALTED.value)

    # Check UNCERTAIN_EFFECT: is outcome unresolved (ambiguous dispatch)?
    if inputs.uncertain_effect:
        blocking_reasons.append(Reason.UNCERTAIN_EFFECT.value)

    # Sort blocking_reasons by ADMISSION_BLOCKING_ORDER.
    blocking_reasons_ordered = []
    for ordered_reason in ADMISSION_BLOCKING_ORDER:
        if ordered_reason in blocking_reasons:
            blocking_reasons_ordered.append(ordered_reason)

    admit_new_entry = len(blocking_reasons_ordered) == 0
    # selected_physical_account_count: I01 invariant says exactly one account
    # will be selected in single-destination mode. Return 1 if admitted (one
    # will be chosen from eligible candidates), else 0 if any blocker applies.
    selected_physical_account_count = 1 if admit_new_entry else 0

    return AdmissionDecision(
        admit_new_entry=admit_new_entry,
        selected_physical_account_count=selected_physical_account_count,
        blocking_reasons=blocking_reasons_ordered,
        broker_call_count=0,
    )
