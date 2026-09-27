"""AD-14 "Managed-program setup" -- the real save/list/submit-for-review
service backing F-MANAGED-PROGRAM. See dashboard_spec/screens/AD-14.md
for the full screen contract this implements a bounded slice of.

Every allocation/NAV/dealing/fee reference is a required, opaque
policy id -- never a raw percentage or rate. "Cannot activate using
local percentage table alone" (AD-14's own acceptance text) is enforced
by construction: this model has no numeric rate field to begin with,
only references to policies that must exist elsewhere (broker-approved
conventions this build does not itself compute or store).

`agreement_evidence_ids` must be nonempty -- "Evidence... rights/legal/
broker approvals; No automated signing" (AD-14's own field help text):
a program can never be saved with zero supporting evidence references.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.managed_program import ManagedProgram, ManagedProgramMode, ManagedProgramState

_MAX_PROGRAM_NAME_LENGTH = 100


class InvalidManagedProgramError(Exception):
    pass


class ProgramNotEligibleForReviewError(Exception):
    pass


def list_managed_programs(session: Session, *, tenant_id: str) -> list[ManagedProgram]:
    return list(
        session.scalars(
            select(ManagedProgram).where(ManagedProgram.tenant_id == tenant_id).order_by(ManagedProgram.created_at.desc())
        ).all()
    )


def get_managed_program(session: Session, program_id: str, *, tenant_id: str) -> ManagedProgram | None:
    program = session.get(ManagedProgram, program_id)
    if program is None or program.tenant_id != tenant_id:
        return None
    return program


def create_managed_program(
    session: Session,
    *,
    tenant_id: str,
    program_name: str,
    broker_program_id: str,
    mode: str,
    allocation_policy_id: str,
    nav_policy_id: str,
    dealing_schedule_id: str,
    fee_policy_id: str | None,
    agreement_evidence_ids: list[str],
) -> ManagedProgram:
    if not program_name or not (1 <= len(program_name) <= _MAX_PROGRAM_NAME_LENGTH):
        raise InvalidManagedProgramError(f"program_name must be 1..{_MAX_PROGRAM_NAME_LENGTH} characters")
    try:
        mode_enum = ManagedProgramMode(mode)
    except ValueError as exc:
        raise InvalidManagedProgramError(f"{mode!r} is not an approved contract structure") from exc
    for field_name, value in (
        ("broker_program_id", broker_program_id),
        ("allocation_policy_id", allocation_policy_id),
        ("nav_policy_id", nav_policy_id),
        ("dealing_schedule_id", dealing_schedule_id),
    ):
        if not value or not value.strip():
            raise InvalidManagedProgramError(f"{field_name} is required")
    if not agreement_evidence_ids:
        raise InvalidManagedProgramError("agreement_evidence_ids is required -- no automated signing without evidence")

    program = ManagedProgram(
        tenant_id=tenant_id,
        program_name=program_name,
        broker_program_id=broker_program_id,
        mode=mode_enum,
        allocation_policy_id=allocation_policy_id,
        nav_policy_id=nav_policy_id,
        dealing_schedule_id=dealing_schedule_id,
        fee_policy_id=fee_policy_id or None,
        agreement_evidence_ids=list(agreement_evidence_ids),
    )
    session.add(program)
    session.flush()
    return program


def request_managed_program_review(session: Session, program: ManagedProgram) -> ManagedProgram:
    """"Confirm: Submit program release review, not custody or deposit
    action" -- moves a DRAFT to SUBMITTED_FOR_REVIEW only. Actual
    program activation/release remains a separate, not-yet-built
    admission decision."""
    if program.state != ManagedProgramState.DRAFT:
        raise ProgramNotEligibleForReviewError(f"program is {program.state.value}, not DRAFT")
    program.state = ManagedProgramState.SUBMITTED_FOR_REVIEW
    session.flush()
    return program
