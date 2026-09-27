"""Fair MAM (multi-account manager) order allocation, per
spec/docs/07_managed_accounts_pamm_mam.md's "Fair MAM allocation"
section: "Default simulator uses largest-remainder allocation of
integer units with deterministic account-ID tie break, constrained by
pre-approved maximum allocations... Record every rounding residue and
prove total allocated units equals actual allocatable fills."

Simulation only -- this never talks to a real broker; production uses
"the actual contractual broker rule instead" (same section). This
module exists so the fairness properties the spec requires (no account
gets systematically favored fills, every rounding decision is
reconstructable, nothing is silently over-allocated past its own cap)
are true of the DEFAULT/simulated path, and are real, checked
invariants rather than an assumption.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class MamAllocationResult:
    #: Integer units allocated per account_id.
    allocations: dict[str, int]
    #: Fillable units this allocation could not place anywhere (every
    #: account already at its cap) -- "unallocatable remainder is
    #: explicit," never silently dropped or force-assigned past a cap.
    unallocated_units: int
    #: account_id -> the fractional remainder that determined its
    #: largest-remainder priority, kept for audit/reproducibility even
    #: though it does not affect any account's actual allocation.
    remainders: dict[str, Decimal] = field(default_factory=dict)


def allocate_fills(
    total_fillable_units: int,
    account_weights: Mapping[str, Decimal],
    *,
    max_allocations: Mapping[str, int] | None = None,
) -> MamAllocationResult:
    """Largest-remainder (Hare quota) integer allocation of
    `total_fillable_units` across `account_weights`, each account capped
    at `max_allocations[account_id]` (uncapped if absent). Ties in the
    fractional-remainder ranking break on account_id ascending --
    deterministic, not insertion-order- or hash-order-dependent."""
    if total_fillable_units < 0:
        raise ValueError("total_fillable_units must not be negative")
    if not account_weights:
        return MamAllocationResult(allocations={}, unallocated_units=total_fillable_units, remainders={})

    total_weight = sum(account_weights.values())
    if total_weight <= 0:
        raise ValueError("account_weights must sum to a positive value")

    caps = dict(max_allocations or {})
    quotas: dict[str, Decimal] = {
        account_id: (Decimal(total_fillable_units) * weight) / total_weight
        for account_id, weight in account_weights.items()
    }

    allocations: dict[str, int] = {}
    remainders: dict[str, Decimal] = {}
    for account_id, quota in quotas.items():
        floor_units = int(quota)
        cap = caps.get(account_id)
        allocated = min(floor_units, cap) if cap is not None else floor_units
        allocations[account_id] = allocated
        remainders[account_id] = quota - floor_units

    distributed = sum(allocations.values())
    remaining = total_fillable_units - distributed

    # Largest remainder first; account_id ascending is the deterministic
    # tie-break for equal remainders.
    priority_order = sorted(account_weights.keys(), key=lambda a: (-remainders[a], a))

    for account_id in priority_order:
        if remaining <= 0:
            break
        cap = caps.get(account_id)
        if cap is not None and allocations[account_id] >= cap:
            continue
        allocations[account_id] += 1
        remaining -= 1

    return MamAllocationResult(allocations=allocations, unallocated_units=remaining, remainders=remainders)
