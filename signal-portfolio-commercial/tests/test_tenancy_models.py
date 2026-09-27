"""app/models/tenancy.py's basic shape, against a real Postgres database
(see tests/conftest.py) -- compound foreign keys and unique constraints
are enforced by the database, not by application code, so these need a
real engine to mean anything."""
from app.models.tenancy import CustomerProfile, Membership, MembershipRole, Tenant, UserIdentity


def _tenant(session, tenant_id="tenant-1"):
    tenant = Tenant(tenant_id=tenant_id, display_name="Acme Customer LLC", environment="LOCAL_SIM")
    session.add(tenant)
    session.flush()
    return tenant


def _user(session, user_id="user-1", email="user1@example.com"):
    user = UserIdentity(user_id=user_id, email=email)
    session.add(user)
    session.flush()
    return user


def test_a_membership_links_a_real_tenant_and_user_with_a_role(db_session):
    _tenant(db_session)
    _user(db_session)
    membership = Membership(tenant_id="tenant-1", user_id="user-1", role=MembershipRole.OWNER)
    db_session.add(membership)
    db_session.flush()

    fetched = db_session.get(Membership, {"tenant_id": "tenant-1", "user_id": "user-1"})
    assert fetched is not None
    assert fetched.role == MembershipRole.OWNER


def test_a_customer_profile_requires_an_existing_membership(db_session):
    _tenant(db_session)
    _user(db_session)
    db_session.add(Membership(tenant_id="tenant-1", user_id="user-1", role=MembershipRole.CUSTOMER))
    db_session.flush()

    profile = CustomerProfile(
        tenant_id="tenant-1",
        user_id="user-1",
        display_name="Acme Customer",
        residence_jurisdiction="US",
    )
    db_session.add(profile)
    db_session.flush()

    fetched = db_session.get(CustomerProfile, "tenant-1")
    assert fetched is not None
    assert fetched.user_id == "user-1"


def test_a_second_membership_in_the_same_tenant_for_the_same_user_is_rejected(db_session):
    from sqlalchemy.exc import IntegrityError

    _tenant(db_session)
    _user(db_session)
    db_session.add(Membership(tenant_id="tenant-1", user_id="user-1", role=MembershipRole.OWNER))
    db_session.flush()

    db_session.add(Membership(tenant_id="tenant-1", user_id="user-1", role=MembershipRole.CUSTOMER))
    try:
        db_session.flush()
        assert False, "expected the unique (tenant_id, user_id) constraint to reject this"
    except IntegrityError:
        db_session.rollback()
