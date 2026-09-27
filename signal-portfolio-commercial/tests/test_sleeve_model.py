"""app/models/sleeve.py -- basic persistence, and confirms sleeves are
subject to the same row-level security as other tenant-scoped tables
(app/db.py's _TENANT_SCOPED_TABLES)."""
from app.db import set_tenant_scope
from app.models.sleeve import Sleeve


def _sleeve(tenant_id, sleeve_id):
    return Sleeve(
        sleeve_id=sleeve_id,
        tenant_id=tenant_id,
        provider="acme-signals",
        analyst="jane",
        strategy_horizon="intraday-momentum",
        asset_class="EQUITY",
        parser_version="v1",
        execution_policy_id="policy-1",
        cost_model_id="cost-1",
        capacity_policy_id="capacity-1",
        risk_unit_id="risk-1",
        history_origin="broker-confirmed",
    )


def test_a_sleeve_persists_its_fields(db_session):
    db_session.add(_sleeve("tenant-a", "sleeve-1"))
    db_session.commit()

    fetched = db_session.get(Sleeve, "sleeve-1")
    assert fetched.provider == "acme-signals"
    assert fetched.asset_class == "EQUITY"


def test_sleeves_are_row_level_security_scoped(db_session, tenant_session_factory):
    db_session.add(_sleeve("tenant-a", "sleeve-a"))
    db_session.add(_sleeve("tenant-b", "sleeve-b"))
    db_session.commit()

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        rows = session.query(Sleeve).all()
        assert [r.sleeve_id for r in rows] == ["sleeve-a"]
    finally:
        session.rollback()
        session.close()
