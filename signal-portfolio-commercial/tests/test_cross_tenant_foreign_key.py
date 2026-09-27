"""CP-012 "Cross-tenant foreign keys": a CustomerProfile's compound FK to
Membership(tenant_id, user_id) must reject any row that isn't an actual
existing membership -- in particular it must reject a
(tenant_id, user_id) pair built by mixing a real tenant with a real
user_id that belongs to a DIFFERENT tenant's membership, which a naive
single-column FK to `tenants.tenant_id` plus a naive single-column FK to
`user_identities.user_id` would happily allow."""
from sqlalchemy.exc import IntegrityError

from app.models.tenancy import CustomerProfile, Membership, MembershipRole, Tenant, UserIdentity


def test_a_customer_profile_cannot_reference_a_mismatched_tenant_user_pair(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
            UserIdentity(user_id="user-a", email="a@example.com"),
            UserIdentity(user_id="user-b", email="b@example.com"),
        ]
    )
    db_session.flush()
    # user-a is only ever a member of tenant-a; user-b only of tenant-b.
    db_session.add(Membership(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.CUSTOMER))
    db_session.add(Membership(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.CUSTOMER))
    db_session.flush()

    # tenant-a exists and user-b exists, but (tenant-a, user-b) is not a
    # real membership -- the compound FK must reject this, even though
    # each half of the pair is individually a real row somewhere.
    bad_profile = CustomerProfile(
        tenant_id="tenant-a",
        user_id="user-b",
        display_name="Should not be creatable",
        residence_jurisdiction="US",
    )
    db_session.add(bad_profile)
    try:
        db_session.flush()
        raise AssertionError("expected the compound FK to reject a mismatched tenant/user pair")
    except IntegrityError:
        db_session.rollback()


def test_a_customer_profile_for_a_real_matching_pair_succeeds(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            UserIdentity(user_id="user-a", email="a@example.com"),
        ]
    )
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.CUSTOMER))
    db_session.flush()

    good_profile = CustomerProfile(
        tenant_id="tenant-a", user_id="user-a", display_name="Real customer", residence_jurisdiction="US"
    )
    db_session.add(good_profile)
    db_session.flush()

    assert db_session.get(CustomerProfile, "tenant-a") is not None
