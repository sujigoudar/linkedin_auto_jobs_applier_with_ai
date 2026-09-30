"""E01/C03 (bounded): app/db.py's Alembic integration -- a fresh database
gets stamped at head, a pre-existing (pre-Alembic) database gets stamped
without re-running the schema creation or touching its data, and an
already-stamped database is never re-stamped.
"""
import sqlite3


from alembic import command
from alembic.config import Config as AlembicConfig
from app.db import SCHEMA, SignalStore, _ALEMBIC_DIR, _alembic_config
from app.models import Side, Signal


def test_fresh_database_is_stamped_at_head(tmp_path):
    db_path = tmp_path / "fresh.db"
    SignalStore(db_path)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    conn.close()

    assert row == ("0028",)  # current head -- see alembic/versions/0028_add_provider_source_connection_tables.py


def test_legacy_pre_alembic_database_is_stamped_not_recreated(tmp_path):
    """Simulates a database created by an OLDER version of this app,
    before Alembic existed here -- schema already present, but no
    alembic_version table at all. Opening it with SignalStore now must
    stamp it (so alembic history is meaningful going forward) without
    ever re-running the schema SQL against a database that already has
    those tables (which would be a no-op for CREATE TABLE IF NOT EXISTS
    anyway, but must never touch existing rows).
    """
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    conn.execute(
        "INSERT INTO signals (id, source, symbol, side, asset_class, received_at, raw) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (signal.id, "test", "AAPL", "buy", "crypto", signal.received_at.isoformat(), "{}"),
    )
    conn.commit()
    conn.close()

    SignalStore(db_path)

    conn = sqlite3.connect(db_path)
    version_row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    signal_row = conn.execute("SELECT id FROM signals WHERE id = ?", (signal.id,)).fetchone()
    conn.close()

    assert version_row == ("0028",)  # current head -- see alembic/versions/0028_add_provider_source_connection_tables.py
    assert signal_row is not None  # the pre-existing row survived untouched


def test_reopening_an_already_stamped_database_never_stamps_again(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    SignalStore(db_path)  # first open: stamps it

    calls = []
    monkeypatch.setattr(command, "stamp", lambda cfg, rev: calls.append(rev))

    SignalStore(db_path)  # second open: must see the existing alembic_version table and skip

    assert calls == []


def test_alembic_config_points_at_this_specific_database(tmp_path):
    db_path = tmp_path / "specific.db"
    cfg = _alembic_config(db_path)
    assert cfg.get_main_option("sqlalchemy.url") == f"sqlite:///{db_path}"


def _table_columns(db_path) -> dict[str, set[str]]:
    """table name -> set of column names, for every real table (never the
    sqlite_* bookkeeping tables or alembic's own alembic_version)."""
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            ).fetchall()
        ]
        return {
            table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in tables
        }
    finally:
        conn.close()


def _table_indexes(db_path) -> dict[str, set[str]]:
    """table name -> set of index names, for every real table (never the
    sqlite_* bookkeeping tables or alembic's own alembic_version) --
    sqlite's own auto-created indexes (e.g. for a UNIQUE column, named
    `sqlite_autoindex_*`) are excluded since those are never declared by
    either path directly and so aren't a meaningful comparison."""
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            ).fetchall()
        ]
        return {
            table: {
                row[1]
                for row in conn.execute(f"PRAGMA index_list({table})")
                if not row[1].startswith("sqlite_autoindex_")
            }
            for table in tables
        }
    finally:
        conn.close()


def test_alembic_upgrade_head_from_genuinely_empty_database_matches_bootstrap(tmp_path):
    """Regression test for the bug where `0001_initial_schema.py`'s
    `upgrade()` imported and ran `app/db.py`'s LIVE `SCHEMA` string --
    which already contains every column/table added by every later
    revision -- instead of a frozen snapshot of its own. That made a
    genuinely empty database's `alembic upgrade head` create the full,
    final `orders` table (with `reserved_notional` and everything else
    0002-0015 add) already at revision 0001, so `0002`'s own
    `add_column("orders", "reserved_notional", ...)` collided with a
    column already there and raised `sqlite3.OperationalError: duplicate
    column name: reserved_notional` -- and every later `create_table`
    revision (0003, 0004, 0006, 0007, 0009, 0011, 0013, 0014, 0015, 0016)
    would have hit the identical "already exists" failure had execution
    ever reached it.

    This never affected a real `SignalStore`-backed deployment (that
    path runs `SCHEMA` directly via its own idempotent bootstrap, then
    stamps head without replaying migrations -- see
    `test_fresh_database_is_stamped_at_head` above) -- only a database
    provisioned purely through the Alembic CLI, which is exactly what
    this test does: it never constructs a `SignalStore` at all, going
    straight through `alembic.command.upgrade` the same way a human
    following this project's own docs, or CI/infra tooling, would.
    """
    alembic_db_path = tmp_path / "alembic_cli_only.db"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{alembic_db_path}")

    # The bug: this used to raise OperationalError partway through the
    # chain. It must now run cleanly through every revision to head.
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(alembic_db_path)
    version_row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    conn.close()
    assert version_row == ("0028",)  # reached real head, not stuck partway through

    bootstrap_db_path = tmp_path / "bootstrap.db"
    SignalStore(bootstrap_db_path)  # the real, SignalStore-backed path

    alembic_schema = _table_columns(alembic_db_path)
    bootstrap_schema = _table_columns(bootstrap_db_path)

    # `signals.import_batch` used to be reachable only via app/db.py's
    # frozen, pre-Alembic `_COLUMN_MIGRATIONS` list, never via a numbered
    # Alembic revision (see 0016_add_signals_import_batch.py) -- the two
    # paths now match exactly, with no exclusion needed.

    assert alembic_schema.keys() == bootstrap_schema.keys(), (
        "the CLI-only alembic upgrade path and SignalStore's own bootstrap "
        "must create the exact same set of tables"
    )
    for table in alembic_schema:
        assert alembic_schema[table] == bootstrap_schema[table], (
            f"table {table!r} has different columns via the alembic-only path "
            f"vs. SignalStore's bootstrap: {alembic_schema[table]!r} != "
            f"{bootstrap_schema[table]!r}"
        )

    # Same check for indexes: `idx_orders_signal_id`/`idx_orders_status`
    # used to be reachable only via app/db.py's SCHEMA string, never via a
    # numbered Alembic revision (see
    # 0017_add_orders_signal_id_status_indexes.py) -- the two paths now
    # create the exact same indexes too.
    alembic_indexes = _table_indexes(alembic_db_path)
    bootstrap_indexes = _table_indexes(bootstrap_db_path)

    assert alembic_indexes.keys() == bootstrap_indexes.keys(), (
        "the CLI-only alembic upgrade path and SignalStore's own bootstrap "
        "must create indexes on the exact same set of tables"
    )
    for table in alembic_indexes:
        assert alembic_indexes[table] == bootstrap_indexes[table], (
            f"table {table!r} has different indexes via the alembic-only path "
            f"vs. SignalStore's bootstrap: {alembic_indexes[table]!r} != "
            f"{bootstrap_indexes[table]!r}"
        )
