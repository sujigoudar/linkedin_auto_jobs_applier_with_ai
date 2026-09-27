"""AD-14 "Managed-program setup" -- app/services/managed_program.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.models.managed_program import ManagedProgramState
from app.services.managed_program import (
    InvalidManagedProgramError,
    ProgramNotEligibleForReviewError,
    create_managed_program,
    get_managed_program,
    list_managed_programs,
    request_managed_program_review,
)


def _create(**overrides):
    defaults = dict(
        tenant_id="tenant-a",
        program_name="Test Program",
        broker_program_id="broker-prog-1",
        mode="pamm",
        allocation_policy_id="alloc-1",
        nav_policy_id="nav-1",
        dealing_schedule_id="dealing-1",
        fee_policy_id=None,
        agreement_evidence_ids=["evidence-1"],
    )
    defaults.update(overrides)
    return defaults


def test_list_managed_programs_is_empty_before_any_are_saved(db_session):
    assert list_managed_programs(db_session, tenant_id="tenant-a") == []


def test_create_rejects_missing_agreement_evidence(db_session):
    with pytest.raises(InvalidManagedProgramError, match="no automated signing"):
        create_managed_program(db_session, **_create(agreement_evidence_ids=[]))


def test_create_rejects_an_unknown_mode(db_session):
    with pytest.raises(InvalidManagedProgramError):
        create_managed_program(db_session, **_create(mode="not-a-real-mode"))


def test_create_rejects_a_missing_allocation_policy(db_session):
    with pytest.raises(InvalidManagedProgramError):
        create_managed_program(db_session, **_create(allocation_policy_id=""))


def test_create_then_reload_persists_the_real_program(db_session):
    program = create_managed_program(db_session, **_create())
    db_session.commit()

    programs = list_managed_programs(db_session, tenant_id="tenant-a")
    assert len(programs) == 1
    assert programs[0].program_id == program.program_id
    assert programs[0].state == ManagedProgramState.DRAFT


def test_get_managed_program_is_none_for_a_cross_tenant_program(db_session):
    program = create_managed_program(db_session, **_create())
    db_session.commit()
    assert get_managed_program(db_session, program.program_id, tenant_id="tenant-b") is None


def test_request_review_moves_a_draft_to_submitted(db_session):
    program = create_managed_program(db_session, **_create())
    db_session.commit()

    request_managed_program_review(db_session, program)
    db_session.commit()
    assert program.state == ManagedProgramState.SUBMITTED_FOR_REVIEW


def test_request_review_refuses_a_non_draft_program(db_session):
    program = create_managed_program(db_session, **_create())
    db_session.commit()
    request_managed_program_review(db_session, program)
    db_session.commit()

    with pytest.raises(ProgramNotEligibleForReviewError):
        request_managed_program_review(db_session, program)
