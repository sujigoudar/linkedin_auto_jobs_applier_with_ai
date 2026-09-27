"""ID-04 "Service eligibility onboarding" -- app/services/eligibility.py's
own tests. Real Postgres, real tenant-scoped session (tests/conftest.py's
`db_session`)."""
import pytest

from app.models.product import Product, ProductLifecycleState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.eligibility import (
    InvalidEligibilityFactsError,
    evaluate_eligibility,
    get_eligibility_assessment,
    save_eligibility_facts,
)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_get_eligibility_assessment_is_none_before_any_save(db_session):
    _seed_membership(db_session)
    assert get_eligibility_assessment(db_session, tenant_id="tenant-a", user_id="user-a") is None


def test_save_eligibility_facts_requires_a_residence_country(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidEligibilityFactsError):
        save_eligibility_facts(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            residence_country="",
            tax_residence=[],
            customer_type="individual",
            requested_service_modes=["alerts"],
            document_versions=["v1"],
            facts_confirmed=True,
        )


def test_save_eligibility_facts_rejects_an_unknown_requested_service(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidEligibilityFactsError):
        save_eligibility_facts(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            residence_country="US",
            tax_residence=[],
            customer_type="individual",
            requested_service_modes=["not-a-real-service"],
            document_versions=["v1"],
            facts_confirmed=True,
        )


def test_save_then_reload_persists_the_real_submitted_facts(db_session):
    _seed_membership(db_session)
    save_eligibility_facts(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        residence_country="US",
        tax_residence=["US"],
        customer_type="individual",
        requested_service_modes=["alerts"],
        document_versions=["terms-v1"],
        facts_confirmed=True,
    )
    db_session.commit()

    reloaded = get_eligibility_assessment(db_session, tenant_id="tenant-a", user_id="user-a")
    assert reloaded is not None
    assert reloaded.residence_country == "US"
    assert reloaded.document_versions == ["terms-v1"]
    assert reloaded.facts_confirmed is True


def test_unsupported_jurisdiction_is_a_named_blocker_not_silent_denial(db_session):
    _seed_membership(db_session)
    assessment = save_eligibility_facts(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        residence_country="FR",
        tax_residence=[],
        customer_type="individual",
        requested_service_modes=["alerts"],
        document_versions=["v1"],
        facts_confirmed=True,
    )
    decisions = evaluate_eligibility(db_session, assessment)
    assert decisions[0].service == "alerts"
    assert decisions[0].decision == "UNSUPPORTED"
    assert decisions[0].reason == "JURISDICTION_NOT_SUPPORTED"


def test_entity_customer_type_is_a_named_blocker(db_session):
    _seed_membership(db_session)
    assessment = save_eligibility_facts(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        residence_country="US",
        tax_residence=[],
        customer_type="entity",
        requested_service_modes=["alerts"],
        document_versions=["v1"],
        facts_confirmed=True,
    )
    decisions = evaluate_eligibility(db_session, assessment)
    assert decisions[0].decision == "UNSUPPORTED"
    assert decisions[0].reason == "ENTITY_ONBOARDING_NOT_IMPLEMENTED"


def test_unconfirmed_facts_are_pending_never_eligible(db_session):
    _seed_membership(db_session)
    assessment = save_eligibility_facts(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        residence_country="US",
        tax_residence=[],
        customer_type="individual",
        requested_service_modes=["alerts"],
        document_versions=["v1"],
        facts_confirmed=False,
    )
    decisions = evaluate_eligibility(db_session, assessment)
    assert decisions[0].decision == "PENDING"
    assert decisions[0].reason == "FACTS_NOT_CONFIRMED"


def test_research_is_eligible_even_with_no_published_product(db_session):
    _seed_membership(db_session)
    assessment = save_eligibility_facts(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        residence_country="US",
        tax_residence=[],
        customer_type="individual",
        requested_service_modes=["research"],
        document_versions=["v1"],
        facts_confirmed=True,
    )
    decisions = evaluate_eligibility(db_session, assessment)
    assert decisions[0].decision == "ELIGIBLE"


def test_alerts_is_unsupported_until_a_real_product_publishes_that_service(db_session):
    _seed_membership(db_session)
    assessment = save_eligibility_facts(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        residence_country="US",
        tax_residence=[],
        customer_type="individual",
        requested_service_modes=["alerts"],
        document_versions=["v1"],
        facts_confirmed=True,
    )
    decisions = evaluate_eligibility(db_session, assessment)
    assert decisions[0].decision == "UNSUPPORTED"
    assert decisions[0].reason == "NO_PUBLISHED_PRODUCT_FOR_SERVICE"

    db_session.add(
        Product(
            tenant_id="tenant-b",
            product_name="Alerts Product",
            slug="alerts-product",
            service_modes=["alerts"],
            lifecycle_state=ProductLifecycleState.PUBLISHED,
        )
    )
    db_session.commit()

    decisions = evaluate_eligibility(db_session, assessment)
    assert decisions[0].decision == "ELIGIBLE"
    assert decisions[0].reason == "POLICY_SATISFIED"
