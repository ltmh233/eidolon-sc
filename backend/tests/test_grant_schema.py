"""Offline grant-operation upgrade, retaining every historical row and result."""
import sqlite3
from pathlib import Path

import pytest

from eidolon.data_access import Database
from eidolon.work_schema import migrate


def history(path):
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / 'fixtures/sc-grants-v2.sql').read_text())


def test_v2_requires_offline_upgrade_and_preserves_duplicate_history(tmp_path):
    path = tmp_path / 'old.db'
    history(path)
    with pytest.raises(RuntimeError, match='offline'):
        Database(path)
    result = migrate(path, tmp_path / 'before.db')
    assert result['version'] == 3
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT rowid,granted_at,count,operation_id FROM reset_card_grants').fetchall() == [
            (1, '2026-01-01T00:00:00+00:00', 2, None),
            (2, '2026-01-01T00:00:00+00:00', 2, None),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO reset_card_grants (granted_at,count) VALUES ('2026-01-01T00:00:00+00:00',1)")
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert migrate(path, tmp_path / 'before.db')['already_migrated']
    with sqlite3.connect(tmp_path / 'before.db') as db:
        assert db.execute('PRAGMA user_version').fetchone() == (2,)
        assert len(db.execute('SELECT * FROM reset_card_grants').fetchall()) == 2


def test_missing_optional_table_and_unknown_history_are_handled_explicitly(tmp_path):
    path = tmp_path / 'minimal.db'
    history(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE reset_card_grants')
    assert migrate(path, tmp_path / 'minimal-backup.db')['version'] == 3
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM reset_card_grants').fetchall() == []
    for fault in ('extra-column', 'negative-count', 'unknown-trigger'):
        bad = tmp_path / f'{fault}.db'
        history(bad)
        with sqlite3.connect(bad) as db:
            if fault == 'extra-column':
                db.execute('ALTER TABLE reset_card_grants ADD COLUMN surprise TEXT')
            elif fault == 'negative-count':
                db.execute('UPDATE reset_card_grants SET count=-1')
            else:
                db.execute('CREATE TRIGGER custom_grant AFTER INSERT ON reset_card_grants BEGIN SELECT 1; END')
            before = list(db.iterdump())
        with pytest.raises(RuntimeError, match='historical grant'):
            migrate(bad, tmp_path / f'{fault}-backup.db')
        with sqlite3.connect(bad) as db:
            assert list(db.iterdump()) == before, fault


def test_interrupted_or_disk_full_upgrade_preserves_source_and_backup(tmp_path, monkeypatch):
    import subprocess
    import sys
    from eidolon import grant_schema
    path = tmp_path / 'interrupted.db'
    history(path)
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
    code = """
import os, sys
from pathlib import Path
from eidolon import grant_schema
from eidolon.work_schema import migrate
original = grant_schema.install
def interrupted(db):
    original(db)
    os._exit(77)
grant_schema.install = interrupted
migrate(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    result = subprocess.run([sys.executable, '-c', code, str(path), str(tmp_path / 'killed-backup.db')], timeout=10)
    assert result.returncode == 77
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
        assert db.execute('PRAGMA user_version').fetchone() == (2,)
    original = grant_schema.install
    def full(db):
        original(db)
        raise sqlite3.OperationalError('injected database or disk is full')
    with monkeypatch.context() as patch:
        patch.setattr(grant_schema, 'install', full)
        with pytest.raises(sqlite3.OperationalError, match='disk is full'):
            migrate(path, tmp_path / 'full-backup.db')
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
    for name in ('killed-backup.db', 'full-backup.db'):
        with sqlite3.connect(tmp_path / name) as db:
            assert list(db.iterdump()) == before, name
    assert migrate(path, tmp_path / 'success-backup.db')['version'] == 3
