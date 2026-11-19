"""Self-check for the Logs section — proves capture works, without fakes.

    python3 tools/logs_selfcheck.py

It copies the source tree to a temp directory (never touching the real
``instance/`` database), boots the app there, drives the real routes (login,
add client, edit client, suspend client, page view, presence beat) and asserts:

  * an activity row exists for each operation, with the right category;
  * the edit row carries a field-level diff (old -> new);
  * the suspend is classified as a delete, not as an edit;
  * a *failed* save writes **no** activity row;
  * a bad login is recorded as failed, the good one as success;
  * every Logs tab renders HTTP 200;
  * the retention purge runs.

Exit code 0 = the logging pipeline is intact.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _inner() -> int:
    """The checks — run inside the throwaway copy of the source tree."""
    sys.path.insert(0, str(_ROOT))
    os.environ['AMS_ENABLE_BACKGROUND_WORKERS'] = '0'
    os.environ['AMS_LOGIN_THROTTLE'] = '0'

    failures = []
    checks = 0

    def check(label, condition, detail=''):
        nonlocal checks
        checks += 1
        if condition:
            print(f'  ok   {label}')
        else:
            failures.append(label)
            print(f'  FAIL {label}' + (f' — {detail}' if detail else ''))

    try:
        from app import create_app

        app = create_app()
        app.config['WTF_CSRF_ENABLED'] = False
        client = app.test_client()

        from models import Account, ActivityEvent, Client, ClientCategory, db

        with app.app_context():
            if not ClientCategory.query.filter_by(name='General').first():
                db.session.add(ClientCategory(name='General'))
            if not Account.query.filter_by(name='SELFCHECK CASH').first():
                db.session.add(Account(name='SELFCHECK CASH', category='cash',
                                       account_type='cash', is_active=True, balance=0))
            db.session.commit()

        print('\n1. authentication')
        bad = client.post('/login', data={'username': 'Admin', 'password': 'definitely-wrong'})
        check('bad password rejected', bad.status_code in (200, 401, 429), bad.status_code)
        good = client.post('/login', data={'username': 'Admin', 'password': 'Admin@fbm12345'},
                           follow_redirects=True)
        check('admin login accepted', good.status_code == 200, good.status_code)

        print('\n2. create / edit / soft delete of a real record')
        client.post('/add_client', data={
            'name': 'SELFCHECK CLIENT', 'code': 'SC1', 'category': 'General',
            'opening_balance': '5000', 'phone': '03000000001',
        }, follow_redirects=True)
        with app.app_context():
            row = Client.query.filter_by(code='SC1').first()
            cid = row.id if row else None
        check('client created', cid is not None)

        if cid:
            client.post(f'/edit_client/{cid}', data={
                'name': 'SELFCHECK CLIENT RENAMED', 'code': 'SC1', 'category': 'General',
                'opening_balance': '9500', 'phone': '03000000002',
            }, follow_redirects=True)
            client.post(f'/delete_client/{cid}', follow_redirects=True)

        print('\n3. a failing save must not be logged')
        with app.app_context():
            before_fail = ActivityEvent.query.filter(
                ActivityEvent.category.in_(['create', 'update', 'delete'])).count()
        client.post('/add_client', data={'name': '', 'category': 'General'}, follow_redirects=True)
        with app.app_context():
            after_fail = ActivityEvent.query.filter(
                ActivityEvent.category.in_(['create', 'update', 'delete'])).count()
        check('refused save wrote no activity row', before_fail == after_fail,
              f'{before_fail} -> {after_fail}')

        print('\n4. page view + presence')
        client.get('/')
        client.get('/clients')
        client.post('/api/activity/heartbeat', json={
            'path': '/clients', 'title': 'Clients', 'visible': True, 'idle_sec': 3})

        print('\n5. stored rows')
        with app.app_context():
            events = ActivityEvent.query.order_by(ActivityEvent.occurred_at.asc()).all()
            by_cat = {}
            for event in events:
                by_cat.setdefault(event.category, []).append(event)

            check('login success recorded',
                  any(e.result == 'success' for e in by_cat.get('login', [])))
            check('login failure recorded',
                  any(e.result == 'failed' for e in by_cat.get('login', [])))
            check('create recorded', bool(by_cat.get('create')))
            check('update recorded', bool(by_cat.get('update')))
            check('suspend recorded as delete',
                  any(e.result == 'soft' for e in by_cat.get('delete', [])))
            check('edit row carries a field diff',
                  any(e.changed_json and 'opening_balance' in e.changed_json
                      for e in by_cat.get('update', [])))
            check('page views recorded', bool(by_cat.get('view')),
                  f'{len(by_cat.get("view", []))} bucket(s)')
            check('no activity row is empty of detail',
                  all((e.summary or e.action) for e in events))
            for event in events[:10]:
                print(f'       [{event.category:6}/{event.result:9}] '
                      f'{event.entity_label or "":22} {(event.summary or event.action)[:66]}')

        print('\n6. every tab renders')
        for tab in ('logins', 'added', 'edited', 'deleted', 'views',
                    'import_export', 'errors', 'server', 'users'):
            resp = client.get(f'/logs/{tab}')
            check(f'/logs/{tab}', resp.status_code == 200, resp.status_code)
        check('/api/activity/state', client.get('/api/activity/state').status_code == 200)
        check('/settings/sessions redirects', client.get('/settings/sessions').status_code == 302)
        check('/settings/activity redirects', client.get('/settings/activity').status_code == 302)

        print('\n7. retention job runs')
        with app.app_context():
            from app.services.activity_capture import purge
            purge()
        check('purge completed', True)

    except Exception as exc:  # pragma: no cover - diagnostics
        import traceback
        traceback.print_exc()
        failures.append(f'exception: {exc}')

    print()
    if failures:
        print(f'SELF-CHECK FAILED — {len(failures)} of {checks} check(s):')
        for item in failures:
            print('  -', item)
        return 1
    print(f'SELF-CHECK PASSED — {checks}/{checks} checks.')
    return 0


def main() -> int:
    tmp = tempfile.mkdtemp(prefix='ams-logs-selfcheck-')
    try:
        work = Path(tmp) / 'ams'
        shutil.copytree(
            _ROOT, work,
            ignore=shutil.ignore_patterns('instance', '.git', '__pycache__',
                                          '*.pyc', 'APPLICATION_AUDIT', 'backups'),
        )
        import subprocess
        env = dict(os.environ, AMS_ENABLE_BACKGROUND_WORKERS='0', AMS_LOGIN_THROTTLE='0')
        proc = subprocess.run(
            [sys.executable, str(work / 'tools' / 'logs_selfcheck.py'), '--inner'],
            cwd=str(work), env=env, capture_output=True, text=True,
        )
        out = (proc.stdout or '') + (proc.stderr or '')
        for line in out.splitlines():
            low = line.lower()
            if low.startswith('  ok'):
                print(line)
            elif low.startswith('  fail') or 'SELF-CHECK' in line:
                print(line)
        print()
        if proc.returncode == 0:
            print('SELF-CHECK PASSED (isolated copy — the real database was not touched).')
            return 0
        print('SELF-CHECK FAILED — see the lines above.')
        return proc.returncode or 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(_inner() if '--inner' in sys.argv else main())
