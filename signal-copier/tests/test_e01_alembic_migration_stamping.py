"""E01/C03 (bounded): app/db.py's Alembic integration -- a fresh database
gets stamped at head, a pre-existing (pre-Alembic) database gets stamped
without re-running the schema creation or touching its data, and an
already-stamped database is never re-stamped.
"""
import sqlite3


from alembic import command
from app.db import SCHEMA, SignalStore, _alembic_config
from app.models import Side, Signal


def test_fresh_database_is_stamped_at_head(tmp_path):
    db_path = tmp_path / "fresh.db"
    SignalStore(db_path)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    conn.close()

    assert row == ("0004",)  # current head -- see alembic/versions/0004_add_signal_episodes_table.py


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

    assert version_row == ("0004",)  # current head -- see alembic/versions/0004_add_signal_episodes_table.py
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
