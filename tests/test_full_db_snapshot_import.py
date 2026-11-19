"""Rehearse snapshot imports with disposable targets only."""
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

import pytest

from full_db_sync.engine import (
    FullDbSyncError,
    export_snapshot,
    import_snapshot,
    table_columns,
    table_rows,
    verify_snapshot,
)

REPO = Path(__file__).resolve().parents[1]
LEGACY = (REPO / 'OLD DATABASES WHICH WE NEED TO MIGRATE SAFELY IN NEW SCHEMA'
          / 'AMS_FULL_20261116-044312.amsdb')


def _read_only(path):
    return closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True))


def _project_rows(con, table, columns):
    column_sql = ', '.join('"' + c.replace('"', '""') + '"' for c in columns)
    return Counter(con.execute(f'SELECT {column_sql} FROM "{table}"'))


def test_production_snapshot_upload(tmp_path, monkeypatch):
    from app import create_app
    from blueprints.import_export import _pages_import_export_page as page

    target = tmp_path / 'current.db'
    monkeypatch.setenv('APP_DB_PATH', str(target))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'isolated-test-secret')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'RehearsalAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'Rehearsal-test-only-pass!')
    monkeypatch.setenv('FULL_DB_SYNC_ENABLED', '1')
    monkeypatch.setattr(page, '_validate_pandas_installed', lambda: False)
    config = {'INSTANCE_PATH': tmp_path / 'instance', 'ENABLE_BACKGROUND_WORKERS': False,
              'DAILY_BACKUP_SCHEDULER': False, 'SESSION_COOKIE_SECURE': False}
    app = create_app(config)
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')

    def csrf(client):
        with client.session_transaction() as sess:
            return sess['_csrf_token']

    client = app.test_client()
    assert client.get('/login').status_code == 200
    response = client.post('/login', data={
        '_csrf_token': csrf(client), 'username': 'RehearsalAdmin',
        'password': 'Rehearsal-test-only-pass!',
    })
    assert response.status_code == 302
    page = client.get('/import_export/')
    assert page.status_code == 200
    assert b'action="/import_export/full_db_import"' in page.data
    assert b'fullDbFile' in page.data
    assert b'Database snapshot import and export do not' in page.data

    with _read_only(target) as db:
        schema = db.execute('SELECT * FROM sqlite_master ORDER BY name').fetchall()
        history = db.execute('SELECT * FROM migration_history ORDER BY filename').fetchall()
        version = db.execute('SELECT * FROM schema_version').fetchall()
        assert db.execute('SELECT COUNT(*) FROM direct_sale').fetchone() == (0,)

    source_digest = hashlib.sha256(LEGACY.read_bytes()).digest()
    assert verify_snapshot(LEGACY)['ok']
    with LEGACY.open('rb') as upload:
        response = client.post('/import_export/full_db_import', data={
            '_csrf_token': csrf(client), 'mode': 'clean_replace', 'file': (upload, LEGACY.name),
        })
    assert response.status_code == 302
    assert 'full_db_import_report=' in response.location
    assert hashlib.sha256(LEGACY.read_bytes()).digest() == source_digest
    assert not list(Path(app.config['IMPORT_UPLOADS_DIR']).iterdir())

    report_path, = Path(app.config['IMPORT_REPORTS_DIR']).glob('full_db_import_report_*.json')
    report = json.loads(report_path.read_text())
    assert report['verification'] == 'PASS'
    assert report['rows_inserted_total'] == 1066
    assert report['tables_shared'] == 81
    assert report['count_mismatches'] == {}
    assert report['unique_indexes_lost'] == []
    assert report['legacy_entry_seq_assigned']['direct_sale'] == 28
    assert report['legacy_columns_defaulted']['booking_item'] == {'cancelled_qty': 0}
    assert report['schema_tables_preserved'] == ['migration_history', 'schema_version']
    with _read_only(Path(report['backup_path'])) as backup:
        assert backup.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert backup.execute('SELECT username FROM user').fetchall() == [('RehearsalAdmin',)]
        assert backup.execute('SELECT COUNT(*) FROM direct_sale').fetchone() == (0,)

    with _read_only(LEGACY) as source, _read_only(target) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT * FROM sqlite_master ORDER BY name').fetchall() == schema
        assert db.execute('SELECT * FROM migration_history ORDER BY filename').fetchall() == history
        assert db.execute('SELECT * FROM schema_version').fetchall() == version
        # Make the client-specific findings explicit as well as checking every
        # column below. Inactive is a saved status, not a missing client.
        for con in (source, db):
            assert con.execute('SELECT is_active, COUNT(*) FROM client GROUP BY is_active').fetchall() == [
                (0, 1), (1, 37),
            ]
            assert con.execute('SELECT COUNT(*) FROM client WHERE transferred_to_id IS NOT NULL').fetchone() == (0,)
        assert {t: report['rows_inserted'][t] for t in (
            'client', 'entry', 'direct_sale', 'booking', 'pending_bill', 'payment',
        )} == {'client': 38, 'entry': 52, 'direct_sale': 28, 'booking': 18, 'pending_bill': 32, 'payment': 1}
        for table, count in table_rows(source).items():
            if table in report['schema_tables_preserved']:
                continue
            assert report['rows_inserted'][table] == count
            columns = table_columns(source, table)
            want = _project_rows(source, table, columns)
            got = _project_rows(db, table, columns)
            if table == 'audit_log':  # The upload adds its own audit event after commit.
                assert not (want - got)
            else:
                assert got == want, table
        for table, count in report['legacy_entry_seq_assigned'].items():
            assert db.execute(f'SELECT entry_seq FROM "{table}" ORDER BY id').fetchall() == [
                (n,) for n in range(1, count + 1)
            ]
        assert db.execute('SELECT DISTINCT cancelled_qty FROM booking_item').fetchall() == [(0.0,)]
        assert db.execute('SELECT DISTINCT last_active_at, activity_state FROM user_login_session').fetchall() == [
            (None, None)
        ]

    health = json.loads((tmp_path / 'health_snapshot.json').read_text())
    assert health['reset_source'] == 'full_db_import'
    rebooted = create_app(config)
    assert not rebooted.config.get('AMS_BOOTSTRAP_ERROR')
    assert rebooted.config['AMS_MIGRATION_REPORT']['applied'] == 0
    with _read_only(target) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT COUNT(*) FROM direct_sale').fetchone() == (28,)

    # Verify the real records also reach both authenticated ledger screens.
    from flask import template_rendered

    views = {}

    def rendered(sender, template, context, **extra):
        views[template.name] = context

    with _read_only(LEGACY) as source:
        clients = source.execute('SELECT id, code, name FROM client ORDER BY id').fetchall()
        codes = {code.strip().lower(): ident for ident, code, _ in clients}
        names = {name.strip().lower(): ident for ident, _, name in clients}
        expected = {}
        for table, name_col in (('entry', 'client'), ('direct_sale', 'client_name'),
                                ('pending_bill', 'client_name')):
            # Known client codes take precedence; historical name is only used
            # if the row has no code. Unlinked rows still exist in the database.
            rows = source.execute(f'SELECT id, client_code, {name_col} FROM {table} WHERE is_void=0').fetchall()
            expected[table] = {ident: set() for ident, _, _ in clients}
            for row_id, code, name in rows:
                code = (code or '').strip().lower()
                owner = codes.get(code) if code else names.get((name or '').strip().lower())
                if owner is not None:
                    expected[table][owner].add(row_id)
        expected['booking'] = {ident: set() for ident, _, _ in clients}
        for ident, name in source.execute('SELECT id, client_name FROM booking WHERE is_void=0'):
            expected['booking'][names[name.strip().lower()]].add(ident)
        expected['payment'] = {ident: set() for ident, _, _ in clients}
        for ident, client_id in source.execute('SELECT id, client_id FROM payment WHERE is_void=0'):
            expected['payment'][client_id].add(ident)
        assert {t: sum(map(len, groups.values())) for t, groups in expected.items()} == {
            'entry': 46, 'direct_sale': 25, 'pending_bill': 29, 'booking': 18, 'payment': 1,
        }
    with _read_only(target) as db:
        admin_id, = db.execute("SELECT id FROM user WHERE role='admin' AND status='active'").fetchone()
    restored_client = rebooted.test_client()
    # The production password is not required or read by this test.
    with restored_client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
    with template_rendered.connected_to(rendered, rebooted):
        for client_id, _, _ in clients:
            assert restored_client.get(f'/ledger/{client_id}?per_page=0').status_code == 200
            ledger = views['client_ledger.html']
            assert {b.id for b in ledger['pending_bills']} == expected['pending_bill'][client_id]
            for prefix, table in (('DirectSale', 'direct_sale'), ('Booking', 'booking'), ('Payment', 'payment')):
                assert {row.id for kind, row in ledger['transactions_map'].items()
                        if kind.startswith(prefix)} == expected[table][client_id]
            assert restored_client.get(f'/client_ledger/{client_id}').status_code == 200
            movements = views['ledger.html']['pagination']
            assert movements.total == len(expected['entry'][client_id])
            assert {e.id for e in movements.items} <= expected['entry'][client_id]


@pytest.fixture
def databases(tmp_path):
    source, target = tmp_path / 'old.amsdb', tmp_path / 'current.db'
    common = '''
        CREATE TABLE user (id INTEGER PRIMARY KEY);
        CREATE TABLE client (id INTEGER PRIMARY KEY);
        CREATE TABLE entry (id INTEGER PRIMARY KEY, client_id INTEGER REFERENCES client(id));
        INSERT INTO client VALUES (1);
    '''
    with closing(sqlite3.connect(source)) as db:
        db.executescript(common + '''
            CREATE TABLE direct_sale (id INTEGER PRIMARY KEY);
            INSERT INTO direct_sale VALUES (8), (3), (1);
        ''')
    with closing(sqlite3.connect(target)) as db:
        db.executescript(common + '''
            CREATE TABLE direct_sale (id INTEGER PRIMARY KEY, entry_seq INTEGER);
            INSERT INTO direct_sale VALUES (1, 10), (2, NULL);
        ''')
    return source, target


def test_legacy_append_numbers_only_new_rows(databases):
    source, target = databases
    report = import_snapshot(source, target, mode='append')
    assert report['legacy_entry_seq_assigned'] == {'direct_sale': 2}
    with _read_only(target) as db:
        assert db.execute('SELECT * FROM direct_sale ORDER BY id').fetchall() == [
            (1, 10), (2, None), (3, 11), (8, 12),
        ]


def test_current_snapshot_preserves_existing_numbers(databases):
    source, target = databases
    with closing(sqlite3.connect(source)) as db, db:
        db.execute('ALTER TABLE direct_sale ADD COLUMN entry_seq INTEGER')
        db.execute('UPDATE direct_sale SET entry_seq = 20 WHERE id = 8')
    report = import_snapshot(source, target)
    assert report['legacy_entry_seq_assigned'] == {}
    with _read_only(target) as db:
        assert db.execute('SELECT * FROM direct_sale ORDER BY id').fetchall() == [
            (1, None), (3, None), (8, 20),
        ]


@pytest.mark.parametrize(('change', 'error'), [
    ('ALTER TABLE direct_sale ADD COLUMN extra TEXT', 'source columns absent'),
    ('CREATE TABLE extra (id); INSERT INTO extra VALUES (1)', 'populated source tables'),
    ('ALTER TABLE entry DROP COLUMN client_id', 'unsupported columns'),
    ('INSERT INTO entry VALUES (1, 99)', 'foreign-key violation'),
])
def test_incompatible_snapshot_never_changes_target(databases, change, error):
    source, target = databases
    before = target.read_bytes()
    with closing(sqlite3.connect(source)) as db:
        db.executescript(change)
    with pytest.raises(FullDbSyncError, match=error):
        import_snapshot(source, target)
    assert target.read_bytes() == before


def test_snapshot_row_count_tampering_is_rejected(databases, tmp_path):
    source, target = databases
    snapshot = tmp_path / 'export.amsdb'
    export_snapshot(source, snapshot)
    with closing(sqlite3.connect(snapshot)) as db, db:
        db.execute('DELETE FROM direct_sale WHERE id = 8')
    before = target.read_bytes()
    with pytest.raises(FullDbSyncError, match='row counts differ from snapshot metadata'):
        import_snapshot(snapshot, target)
    assert target.read_bytes() == before
