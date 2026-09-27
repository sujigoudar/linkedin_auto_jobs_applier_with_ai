"""AD-11 "Customers and scoped support record" -- app/services/customer_support_view.py's
own tests. Real Postgres, real tenant-scoped session."""
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.customer_support_view import get_customer_support_record, list_customers
from app.services.eligibility import save_eligibility_facts
from app.services.support_case import create_support_case


def _seed_customer(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_list_customers_is_empty_before_any_sign_up(db_session):
    assert list_customers(db_session, tenant_id="tenant-a") == []


def test_list_customers_shows_a_real_customer_membership(db_session):
    _seed_customer(db_session)
    customers = list_customers(db_session, tenant_id="tenant-a")
    assert len(customers) == 1
    assert customers[0].user_id == "user-a"
    assert customers[0].open_cases_count == 0


def test_list_customers_excludes_operator_memberships(db_session):
    _seed_customer(db_session)
    db_session.add(UserIdentity(user_id="owner-a", email="owner-a@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="owner-a", role=MembershipRole.OWNER))
    db_session.commit()

    customers = list_customers(db_session, tenant_id="tenant-a")
    assert {c.user_id for c in customers} == {"user-a"}


def test_list_customers_counts_real_open_cases(db_session):
    _seed_customer(db_session)
    create_support_case(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        category="billing",
        related_object_id=None,
        subject="Billing question",
        description="Why was I charged",
        attachment_ids=[],
    )
    db_session.commit()

    customers = list_customers(db_session, tenant_id="tenant-a")
    assert customers[0].open_cases_count == 1


def test_get_customer_support_record_is_none_for_an_unknown_user(db_session):
    _seed_customer(db_session)
    assert get_customer_support_record(db_session, tenant_id="tenant-a", user_id="never-existed") is None


def test_get_customer_support_record_is_none_for_a_cross_tenant_user(db_session):
    _seed_customer(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_customer(db_session, tenant_id="tenant-b", user_id="user-b")
    assert get_customer_support_record(db_session, tenant_id="tenant-a", user_id="user-b") is None


def test_get_customer_support_record_is_none_for_a_non_customer_membership(db_session):
    _seed_customer(db_session)
    db_session.add(UserIdentity(user_id="owner-a", email="owner-a@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="owner-a", role=MembershipRole.OWNER))
    db_session.commit()

    assert get_customer_support_record(db_session, tenant_id="tenant-a", user_id="owner-a") is None


def test_get_customer_support_record_shows_real_eligibility_and_cases(db_session):
    _seed_customer(db_session)
    save_eligibility_facts(
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
    create_support_case(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        category="billing",
        related_object_id=None,
        subject="Billing question",
        description="Why was I charged",
        attachment_ids=[],
    )
    db_session.commit()

    record = get_customer_support_record(db_session, tenant_id="tenant-a", user_id="user-a")
    assert record is not None
    assert record.eligibility_decisions[0].decision == "ELIGIBLE"
    assert len(record.cases) == 1
    assert record.cases[0].subject == "Billing question"
