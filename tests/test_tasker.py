"""Tasker — the isolated delivery tracking board.

Pins down the owner's rules (2026-11-22):

* workflow is strictly forward: NEW -> NOTED -> LOADED -> DISPATCHED -> DELIVERED,
  every step stamped with who + when;
* two admin-set deadlines (note + delivery); missing either turns the task
  red and raises the alarm until an ADMIN resets that time manually;
* create / reset / cancel / reopen are admin-only, the stage buttons are for
  any signed-in user;
* full isolation: every FK on tasker_* tables stays inside tasker_*, and a
  full task lifecycle does not touch a single row of clients, materials,
  sales, payments, stock or any other domain table.
"""
from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from models import (
    Client,
    Material,
    TaskerCleanupLog,
    TaskerDeadlineReset,
    TaskerEvent,
    TaskerItem,
    TaskerSetting,
    TaskerTask,
    User,
    db,
    pk_model_now,
)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'tasker.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'isolated-tasker-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'TaskerAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'tasker-admin-pass-1')
    app = create_app({'TESTING': True, 'INSTANCE_PATH': tmp_path / 'instance',
                      'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
                      'SESSION_COOKIE_SECURE': False})
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    admin = app.test_client()
    assert admin.post('/login', data={'username': 'TaskerAdmin',
                                      'password': 'tasker-admin-pass-1'}).status_code == 302
    return app, admin


@pytest.fixture
def clerk(desk):
    """A plain signed-in user (NOT admin) — the 'other user' who notes tasks."""
    app, _ = desk
    with app.app_context():
        db.session.add(User(username='TaskerClerk',
                            password_hash=generate_password_hash('clerk-pass-1'),
                            role='user', status='active'))
        db.session.commit()
    web = app.test_client()
    assert web.post('/login', data={'username': 'TaskerClerk',
                                    'password': 'clerk-pass-1'}).status_code == 302
    return web


def _payload(**over):
    now = pk_model_now()
    body = {
        'client_ref_id': None,
        'client_code': 'C-1',
        'client_name': 'Test Client',
        'items': [
            {'material_name': 'Maple Cement 50kg', 'qty': 100, 'unit': 'Bags',
             'warranty': 'n/a'},
            {'material_name': '6mm Steel', 'qty': 2, 'unit': 'Tons',
             'warranty': '1 year'},
        ],
        'note_deadline_at': (now + timedelta(minutes=30)).isoformat(timespec='seconds'),
        'delivery_deadline_at': (now + timedelta(hours=4)).isoformat(timespec='seconds'),
        'task_note': 'deliver to site',
    }
    body.update(over)
    return body


def _create(web, **over):
    resp = web.post('/tasker/api/tasks', json=_payload(**over))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()['success'] is True
    return resp.get_json()['task']


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def test_login_wall(desk):
    app, _ = desk
    anon = app.test_client()
    assert anon.get('/tasker').status_code in (302, 401)
    assert anon.get('/tasker/api/board').status_code in (302, 401)
    assert anon.get('/tasker/api/alarm_state').status_code in (302, 401)


def test_page_renders_for_plain_user(desk, clerk):
    resp = clerk.get('/tasker')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'tkBoardBody' in html
    assert 'New Task' in html  # button exists (hidden by JS for non-admins)
    # The admin-only selection column is not rendered at all for a plain user.
    assert 'id="tkSelectAll"' not in html


# ---------------------------------------------------------------------------
# Lifecycle — strictly forward, stamped
# ---------------------------------------------------------------------------
def test_full_lifecycle_and_stamps(desk, clerk):
    app, admin = desk
    task = _create(admin)
    assert task['status'] == 'NEW'
    assert task['task_no'].startswith('TK-')
    assert len(task['items']) == 2
    assert task['items'][1]['warranty'] == '1 year'

    # Forward-only: cannot deliver straight from NEW.
    bad = clerk.post(f"/tasker/api/tasks/{task['id']}/action", json={'action': 'delivered'})
    assert bad.status_code == 400

    # The floor workflow, pressed by the plain user.
    for action, expect in [('noted', 'NOTED'), ('loaded', 'LOADED'),
                           ('dispatched', 'DISPATCHED'), ('delivered', 'DELIVERED')]:
        resp = clerk.post(f"/tasker/api/tasks/{task['id']}/action",
                          json={'action': action, 'note': 'ok'})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['task']['status'] == expect

    detail = clerk.get(f"/tasker/api/tasks/{task['id']}").get_json()
    for key in ('noted', 'loaded', 'dispatched', 'delivered'):
        assert detail['stamps'][f'{key}_by'] == 'TaskerClerk'
        assert detail['stamps'][f'{key}_at']
    events = [e['event'] for e in detail['events']]
    assert events[0] == 'CREATED'
    assert 'NOTED' in events and 'DELIVERED' in events

    # One-way: a delivered task cannot go back.
    again = clerk.post(f"/tasker/api/tasks/{task['id']}/action", json={'action': 'noted'})
    assert again.status_code == 400


def test_stage_buttons_are_open_to_any_user_but_forward_only(desk, clerk):
    _, admin = desk
    task = _create(admin)
    assert clerk.post(f"/tasker/api/tasks/{task['id']}/action",
                      json={'action': 'loaded'}).status_code == 400  # NEW -> LOADED forbidden
    assert clerk.post(f"/tasker/api/tasks/{task['id']}/action",
                      json={'action': 'noted'}).status_code == 200


# ---------------------------------------------------------------------------
# Deadlines + alarm + admin time reset
# ---------------------------------------------------------------------------
def test_note_deadline_missed_raises_alarm_until_admin_resets(desk, clerk):
    app, admin = desk
    past = (pk_model_now() - timedelta(hours=1)).isoformat(timespec='seconds')
    task = _create(admin, note_deadline_at=past)

    board = clerk.get('/tasker/api/board').get_json()
    assert board['counters']['overdue'] == 1
    row = board['tasks'][0]
    assert row['note_overdue'] is True
    assert row['note_alarm_on'] is True

    alarm = clerk.get('/tasker/api/alarm_state').get_json()
    assert alarm['overdue_count'] == 1
    assert alarm['tasks'][0]['kind'] == 'NOTE'
    assert alarm['tasks'][0]['task_no'] == task['task_no']

    # Normal users cannot silence the alarm.
    assert clerk.post(f"/tasker/api/tasks/{task['id']}/reset_deadline",
                      json={'note_deadline_at': future()}).status_code == 403
    assert clerk.get('/tasker/api/alarm_state').get_json()['overdue_count'] == 1

    # Admin sees it and sets a NEW manual time — the alarm stops.
    resp = admin.post(f"/tasker/api/tasks/{task['id']}/reset_deadline",
                      json={'note_deadline_at': future(), 'reason': 'seen at the yard'})
    assert resp.status_code == 200
    assert resp.get_json()['task']['note_overdue'] is False
    assert clerk.get('/tasker/api/alarm_state').get_json()['overdue_count'] == 0

    with app.app_context():
        resets = TaskerDeadlineReset.query.filter_by(task_id=task['id']).all()
        assert len(resets) == 1
        assert resets[0].kind == 'NOTE'
        assert resets[0].by_user == 'TaskerAdmin'
        assert resets[0].reason == 'seen at the yard'
        events = [e.event for e in TaskerEvent.query.filter_by(task_id=task['id']).all()]
        assert 'ALARM_ON' in events and 'DEADLINE_RESET' in events and 'ALARM_OFF' in events


def test_delivery_deadline_missed_is_its_own_alarm(desk, clerk):
    app, admin = desk
    past = (pk_model_now() - timedelta(hours=1)).isoformat(timespec='seconds')
    task = _create(admin, delivery_deadline_at=past)

    alarm = clerk.get('/tasker/api/alarm_state').get_json()
    assert alarm['delivery_count'] == 1
    assert alarm['note_count'] == 0

    # Delivered in time-warp terms: the delivery alarm drops on delivery.
    for action in ('noted', 'loaded', 'dispatched', 'delivered'):
        assert clerk.post(f"/tasker/api/tasks/{task['id']}/action",
                          json={'action': action}).status_code == 200
    assert clerk.get('/tasker/api/alarm_state').get_json()['overdue_count'] == 0


def test_reset_rejects_a_deadline_in_the_past(desk):
    _, admin = desk
    task = _create(admin)
    resp = admin.post(f"/tasker/api/tasks/{task['id']}/reset_deadline",
                      json={'delivery_deadline_at':
                            (pk_model_now() - timedelta(minutes=5)).isoformat(timespec='seconds')})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Admin-only management
# ---------------------------------------------------------------------------
def test_create_cancel_reopen_are_admin_only(desk, clerk):
    app, admin = desk
    assert clerk.post('/tasker/api/tasks', json=_payload()).status_code == 403

    task = _create(admin)
    assert clerk.post(f"/tasker/api/tasks/{task['id']}/action",
                      json={'action': 'cancel'}).status_code == 403

    assert admin.post(f"/tasker/api/tasks/{task['id']}/action",
                      json={'action': 'cancel', 'note': 'client postponed'}).status_code == 200
    board = admin.get('/tasker/api/board').get_json()
    assert board['tasks'][0]['status'] == 'CANCELLED'

    # Reopen restores the stage the task was at before the cancel.
    assert admin.post(f"/tasker/api/tasks/{task['id']}/action",
                      json={'action': 'reopen'}).status_code == 200
    assert admin.get('/tasker/api/board').get_json()['tasks'][0]['status'] == 'NEW'


# ---------------------------------------------------------------------------
# Read-only combo lookups
# ---------------------------------------------------------------------------
def test_combo_lookups_read_only(desk, clerk):
    app, _ = desk
    with app.app_context():
        db.session.add(Client(code='CL-77', name='Bilal Traders'))
        db.session.add(Material(code='M-5', name='Pioneer Cement', unit='Bags'))
        db.session.commit()
        before_clients = Client.query.count()
        before_materials = Material.query.count()

    found = clerk.get('/tasker/api/clients/search?q=bilal').get_json()
    assert any(c['name'] == 'Bilal Traders' for c in found)
    found = clerk.get('/tasker/api/materials/search?q=pioneer').get_json()
    assert any(m['name'] == 'Pioneer Cement' and m['unit'] == 'Bags' for m in found)

    with app.app_context():
        assert Client.query.count() == before_clients
        assert Material.query.count() == before_materials


# ---------------------------------------------------------------------------
# Isolation — the hard requirement
# ---------------------------------------------------------------------------
def test_tasker_foreign_keys_stay_inside_tasker(desk):
    app, _ = desk
    with app.app_context():
        for table in db.metadata.tables.values():
            if not table.name.startswith('tasker_'):
                continue
            for fk in table.foreign_keys:
                target = fk.column.table.name
                assert target.startswith('tasker_'), (
                    f'{table.name} references {target} — isolation broken!'
                )


def test_full_lifecycle_touches_no_other_domain_table(desk, clerk):
    from models import Booking, DirectSale, Entry, Payment, Supplier

    app, admin = desk
    with app.app_context():
        db.session.add(Client(code='CL-1', name='Isolation Client'))
        db.session.add(Supplier(name='Isolation Supplier'))
        db.session.add(Material(code='M-1', name='Isolation Cement', unit='Bags'))
        db.session.commit()
        domain = (Client, Supplier, Material, Booking, DirectSale, Entry, Payment)
        before = {m.__name__: m.query.count() for m in domain}

    task = _create(admin, client_name='Isolation Client')
    for action in ('noted', 'loaded', 'dispatched', 'delivered'):
        clerk.post(f"/tasker/api/tasks/{task['id']}/action", json={'action': action})
    admin.post(f"/tasker/api/tasks/{task['id']}/reset_deadline",
               json={'note_deadline_at': future()})
    clerk.get('/tasker/api/board')
    clerk.get('/tasker/api/alarm_state')

    with app.app_context():
        after = {m.__name__: m.query.count() for m in domain}
        assert after == before, 'Tasker wrote to a non-tasker domain table!'
        # The work itself landed only in tasker_* rows.
        assert TaskerTask.query.count() == 1
        assert TaskerItem.query.count() == 2
        assert TaskerEvent.query.count() >= 5


def future():
    return (pk_model_now() + timedelta(hours=2)).isoformat(timespec='seconds')


# ---------------------------------------------------------------------------
# Cleanup / auto-clean — admin-only permanent delete, even for DELIVERED tasks
# ---------------------------------------------------------------------------
def _deliver(web, task_id):
    for action in ('noted', 'loaded', 'dispatched', 'delivered'):
        resp = web.post(f'/tasker/api/tasks/{task_id}/action', json={'action': action})
        assert resp.status_code == 200, resp.get_json()


def _backdate(app, task_ids, days=40):
    with app.app_context():
        for task_id in task_ids:
            task = TaskerTask.query.get(task_id)
            task.updated_at = pk_model_now() - timedelta(days=days)
        db.session.commit()


def test_admin_can_delete_a_delivered_task_and_plain_user_cannot(desk, clerk):
    app, admin = desk
    task = _create(admin)
    _deliver(clerk, task['id'])
    assert admin.get(f"/tasker/api/tasks/{task['id']}").get_json()['status'] == 'DELIVERED'

    # "even completed marked" — a normal user may never delete a completed task.
    denied = clerk.post(f"/tasker/api/tasks/{task['id']}/delete", json={'confirm': 'DELETE'})
    assert denied.status_code == 403
    with app.app_context():
        assert TaskerTask.query.get(task['id']) is not None

    # A confirmation word is required server-side too.
    no_confirm = admin.post(f"/tasker/api/tasks/{task['id']}/delete", json={'confirm': 'nope'})
    assert no_confirm.status_code == 400

    resp = admin.post(f"/tasker/api/tasks/{task['id']}/delete",
                      json={'confirm': 'DELETE', 'reason': 'duplicate entry'})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()['deleted']['task_no'] == task['task_no']

    with app.app_context():
        assert TaskerTask.query.get(task['id']) is None
        assert TaskerItem.query.filter_by(task_id=task['id']).count() == 0
        assert TaskerEvent.query.filter_by(task_id=task['id']).count() == 0
        assert TaskerDeadlineReset.query.filter_by(task_id=task['id']).count() == 0
        log = TaskerCleanupLog.query.filter_by(task_id=task['id']).one()
        assert log.mode == 'MANUAL'
        assert log.deleted_by == 'TaskerAdmin'
        assert log.status == 'DELIVERED'
        assert log.reason == 'duplicate entry'


def test_task_number_is_never_reused_after_a_delete(desk):
    _, admin = desk
    first = _create(admin)
    assert first['task_no'] == 'TK-0001'
    assert admin.post(f"/tasker/api/tasks/{first['id']}/delete",
                      json={'confirm': 'DELETE'}).status_code == 200
    second = _create(admin)
    assert second['task_no'] == 'TK-0002', 'a cleaned-up task number must not be reused'


def test_bulk_delete_and_cleanup_are_admin_only(desk, clerk):
    app, admin = desk
    a = _create(admin)
    b = _create(admin)

    assert clerk.post('/tasker/api/tasks/bulk_delete',
                      json={'task_ids': [a['id'], b['id']], 'confirm': 'DELETE'}).status_code == 403
    assert clerk.post('/tasker/api/cleanup',
                      json={'statuses': ['DELIVERED'], 'older_than_days': 0,
                            'confirm': 'DELETE'}).status_code == 403
    assert clerk.get('/tasker/api/settings').status_code == 403

    resp = admin.post('/tasker/api/tasks/bulk_delete',
                      json={'task_ids': [a['id'], b['id']], 'confirm': 'DELETE',
                            'reason': 'wrong client'})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()['deleted'] == 2
    with app.app_context():
        assert TaskerTask.query.count() == 0
        assert TaskerCleanupLog.query.filter_by(mode='BULK').count() == 2


def test_manual_cleanup_only_removes_old_closed_tasks(desk):
    app, admin = desk
    delivered = _create(admin)
    cancelled = _create(admin)
    active = _create(admin)

    _deliver(admin, delivered['id'])
    assert admin.post(f"/tasker/api/tasks/{cancelled['id']}/action",
                      json={'action': 'cancel'}).status_code == 200
    _backdate(app, [delivered['id'], cancelled['id'], active['id']], days=40)

    # Dry run reports but changes nothing.
    preview = admin.post('/tasker/api/cleanup',
                         json={'statuses': ['DELIVERED', 'CANCELLED'],
                               'older_than_days': 30, 'dry_run': True})
    assert preview.status_code == 200, preview.get_json()
    assert preview.get_json()['matched'] == 2
    with app.app_context():
        assert TaskerTask.query.count() == 3

    # Real run needs DELETE and leaves the active task alone.
    denied = admin.post('/tasker/api/cleanup',
                        json={'statuses': ['DELIVERED', 'CANCELLED'], 'older_than_days': 30})
    assert denied.status_code == 400

    resp = admin.post('/tasker/api/cleanup',
                      json={'statuses': ['DELIVERED', 'CANCELLED'], 'older_than_days': 30,
                            'confirm': 'DELETE', 'reason': 'year-end cleanup'})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()['deleted'] == 2

    with app.app_context():
        assert TaskerTask.query.count() == 1
        assert TaskerTask.query.get(active['id']) is not None
        logs = TaskerCleanupLog.query.filter_by(mode='CLEANUP').all()
        assert len(logs) == 2
        assert {l.task_id for l in logs} == {delivered['id'], cancelled['id']}


def test_auto_clean_removes_only_old_closed_tasks(desk):
    app, admin = desk
    delivered = _create(admin)
    cancelled = _create(admin)
    active = _create(admin)

    _deliver(admin, delivered['id'])
    admin.post(f"/tasker/api/tasks/{cancelled['id']}/action", json={'action': 'cancel'})
    _backdate(app, [delivered['id'], cancelled['id'], active['id']], days=40)

    saved = admin.post('/tasker/api/settings', json={
        'auto_clean_enabled': True,
        'auto_clean_days': 30,
        'auto_clean_include_cancelled': True,
    })
    assert saved.status_code == 200, saved.get_json()

    board = admin.get('/tasker/api/board').get_json()
    assert board['cleanup']['deleted'] == 2
    with app.app_context():
        assert TaskerTask.query.count() == 1
        assert TaskerTask.query.get(active['id']) is not None
        logs = TaskerCleanupLog.query.filter_by(mode='AUTO').all()
        assert len(logs) == 2
        assert {l.deleted_by for l in logs} == {'auto'}
        assert {l.task_id for l in logs} == {delivered['id'], cancelled['id']}


def test_auto_clean_can_exclude_cancelled(desk):
    app, admin = desk
    delivered = _create(admin)
    cancelled = _create(admin)
    _deliver(admin, delivered['id'])
    admin.post(f"/tasker/api/tasks/{cancelled['id']}/action", json={'action': 'cancel'})
    _backdate(app, [delivered['id'], cancelled['id']], days=40)

    admin.post('/tasker/api/settings', json={
        'auto_clean_enabled': True,
        'auto_clean_days': 30,
        'auto_clean_include_cancelled': False,
    })
    admin.get('/tasker/api/board')
    with app.app_context():
        assert TaskerTask.query.get(cancelled['id']) is not None
        assert TaskerTask.query.get(delivered['id']) is None


def test_auto_clean_settings_validation_and_history(desk):
    app, admin = desk
    assert admin.post('/tasker/api/settings', json={'auto_clean_days': 0}).status_code == 400
    assert admin.post('/tasker/api/settings', json={'auto_clean_days': 4000}).status_code == 400

    resp = admin.post('/tasker/api/settings', json={
        'auto_clean_enabled': True, 'auto_clean_days': 45,
        'auto_clean_include_cancelled': False,
    })
    assert resp.status_code == 200
    assert resp.get_json()['settings']['auto_clean_enabled'] is True
    assert resp.get_json()['settings']['auto_clean_days'] == 45

    got = admin.get('/tasker/api/settings').get_json()
    assert got['settings']['auto_clean_days'] == 45
    assert got['settings']['auto_clean_include_cancelled'] is False

    # Cleanup history is exposed to the admin.
    task = _create(admin)
    admin.post(f"/tasker/api/tasks/{task['id']}/delete", json={'confirm': 'DELETE'})
    history = admin.get('/tasker/api/settings').get_json()['recent_cleanups']
    assert history[0]['task_no'] == task['task_no']
    assert history[0]['mode'] == 'MANUAL'
