"""Tasker — shared helpers for the isolated tracking board.

Everything here touches ONLY ``tasker_*`` tables (plus read-only SELECTs of
Client / Material for the combo lookups).  Nothing in this module imports or
writes sales, delivery, billing, stock or cash code.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from models import (
    Client,
    Material,
    TaskerCleanupLog,
    TaskerDeadlineReset,
    TaskerEvent,
    TaskerItem,
    TaskerSetting,
    TaskerTask,
    db,
    pk_model_now,
)

log = logging.getLogger('tasker')

# ---------------------------------------------------------------------------
# Module configuration (read by utils.module_loader on startup)
# ---------------------------------------------------------------------------
MODULE_CONFIG = {
    'name': 'Tasker',
    'description': (
        'Isolated delivery tracking board: client + items + warranty + '
        'admin deadlines, NEW -> NOTED -> LOADED -> DISPATCHED -> DELIVERED, '
        'with a red + audible alarm when a deadline is missed.'
    ),
    'url_prefix': '/tasker',
    'enabled': True,
    'version': '1.2.0',
    'author': 'AMS',
    'requires_login': True,
}

tasker_bp = Blueprint('tasker', __name__)

# Stage order for the forward-only workflow.
STAGE_ORDER = ('NEW', 'NOTED', 'LOADED', 'DISPATCHED', 'DELIVERED')
TERMINAL_STATUSES = ('DELIVERED', 'CANCELLED')

# Auto-clean is evaluated lazily (board poll / global alarm poll).  The sweep
# itself is cheap, but we still rate-limit it per process so a busy shop with
# many open tabs does not turn every poll into a write.
AUTO_CLEAN_MIN_INTERVAL_SEC = 300


# ---------------------------------------------------------------------------
# Small guards
# ---------------------------------------------------------------------------
def is_admin():
    """Only the admin role may create tasks or reset deadlines (owner rule)."""
    return bool(
        current_user.is_authenticated
        and getattr(current_user, 'role', '') in ('admin', 'root')
    )


def require_admin():
    """403 JSON for non-admins.  Returns None when the caller is admin."""
    if is_admin():
        return None
    return jsonify({'success': False, 'error': 'Admin only.'}), 403


def _actor():
    return (getattr(current_user, 'username', None) or '').strip() or None


def log_event(task, event, detail=''):
    """Append to the task's own event trail (tasker_event only)."""
    db.session.add(TaskerEvent(
        task_id=task.id,
        event=event,
        by_user=_actor(),
        at=pk_model_now(),
        detail=(detail or '')[:300],
    ))


# ---------------------------------------------------------------------------
# Deadline / alarm logic
# ---------------------------------------------------------------------------
def note_overdue_now(task, now):
    """Overdue while still un-noted past the admin's note deadline."""
    return bool(
        task.note_deadline_at
        and task.status == 'NEW'
        and now > task.note_deadline_at
    )


def delivery_overdue_now(task, now):
    """Overdue while not delivered/cancelled past the delivery deadline."""
    return bool(
        task.delivery_deadline_at
        and task.status not in TERMINAL_STATUSES
        and now > task.delivery_deadline_at
    )


def sync_alarms(task, now=None, commit=False):
    """Recompute the two alarm flags, logging ON/OFF transitions.

    Called on board reads and every action so the red state and the event
    trail stay consistent without any background worker.
    """
    now = now or pk_model_now()
    changed = False

    n_over = note_overdue_now(task, now)
    if n_over and not task.note_alarm_on:
        task.note_alarm_on = True
        task.note_overdue_since = now
        log_event(task, 'ALARM_ON', f'Note deadline missed ({task.note_deadline_at})')
        changed = True
    elif not n_over and task.note_alarm_on:
        task.note_alarm_on = False
        task.note_overdue_since = None
        log_event(task, 'ALARM_OFF', 'Note deadline alarm cleared')
        changed = True

    d_over = delivery_overdue_now(task, now)
    if d_over and not task.delivery_alarm_on:
        task.delivery_alarm_on = True
        task.delivery_overdue_since = now
        log_event(task, 'ALARM_ON', f'Delivery deadline missed ({task.delivery_deadline_at})')
        changed = True
    elif not d_over and task.delivery_alarm_on:
        task.delivery_alarm_on = False
        task.delivery_overdue_since = None
        log_event(task, 'ALARM_OFF', 'Delivery deadline alarm cleared')
        changed = True

    if changed and commit:
        db.session.commit()
    return changed


def iso(dt):
    return dt.isoformat(timespec='seconds') if dt else None


def fmt_num(value):
    try:
        f = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return int(f) if f == int(f) else f


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def item_to_dict(item: TaskerItem):
    return {
        'id': item.id,
        'line_no': item.line_no,
        'material_ref_id': item.material_ref_id,
        'material_code': item.material_code or '',
        'material_name': item.material_name or '',
        'qty': fmt_num(item.qty),
        'unit': item.unit or '',
        'warranty': item.warranty or '',
        'note': item.note or '',
    }


def task_to_dict(task: TaskerTask, with_items=True):
    now = pk_model_now()
    data = {
        'id': task.id,
        'task_no': task.task_no,
        'client_ref_id': task.client_ref_id,
        'client_code': task.client_code or '',
        'client_name': task.client_name or '',
        'status': task.status,
        'note_deadline_at': iso(task.note_deadline_at),
        'delivery_deadline_at': iso(task.delivery_deadline_at),
        'note_overdue': note_overdue_now(task, now),
        'delivery_overdue': delivery_overdue_now(task, now),
        'note_alarm_on': bool(task.note_alarm_on),
        'delivery_alarm_on': bool(task.delivery_alarm_on),
        'deadline_reset_count': task.deadline_reset_count or 0,
        'stamps': {
            'noted_by': task.noted_by, 'noted_at': iso(task.noted_at),
            'loaded_by': task.loaded_by, 'loaded_at': iso(task.loaded_at),
            'dispatched_by': task.dispatched_by, 'dispatched_at': iso(task.dispatched_at),
            'delivered_by': task.delivered_by, 'delivered_at': iso(task.delivered_at),
        },
        'delivered_note': task.delivered_note or '',
        'task_note': task.task_note or '',
        'created_by': task.created_by,
        'created_at': iso(task.created_at),
        'cancelled_by': task.cancelled_by,
        'cancelled_at': iso(task.cancelled_at),
        'cancel_reason': task.cancel_reason or '',
    }
    if with_items:
        data['items'] = [item_to_dict(i) for i in (task.items or [])]
    return data


def event_to_dict(ev: TaskerEvent):
    return {
        'event': ev.event,
        'by_user': ev.by_user,
        'at': iso(ev.at),
        'detail': ev.detail or '',
    }


def reset_to_dict(r: TaskerDeadlineReset):
    return {
        'kind': r.kind,
        'old_deadline': iso(r.old_deadline),
        'new_deadline': iso(r.new_deadline),
        'by_user': r.by_user,
        'at': iso(r.at),
        'reason': r.reason or '',
    }


# ---------------------------------------------------------------------------
# Deadline parsing / task numbering
# ---------------------------------------------------------------------------
def parse_deadline(raw):
    """Accept 'YYYY-MM-DDTHH:MM[:SS]' (datetime-local) as naive PK-local time."""
    txt = (raw or '').strip()
    if not txt:
        return None
    try:
        parsed = datetime.fromisoformat(txt)
    except ValueError:
        raise ValueError(f'Invalid date/time: {raw!r} (expected YYYY-MM-DDTHH:MM)')
    return parsed.replace(tzinfo=None)


def next_task_no():
    """Next permanent task number.

    The number must never be reused, even after a task is deleted, so the
    high-water mark is taken from both live tasks and the cleanup log.
    """
    top = db.session.query(db.func.max(TaskerTask.id)).scalar() or 0
    try:
        for (no,) in db.session.query(TaskerCleanupLog.task_no).all():
            m = re.fullmatch(r'TK-(\d+)', (no or '').strip())
            if m:
                top = max(top, int(m.group(1)))
    except Exception:
        # Older database where create_all has not added the log table yet —
        # fall back to the live-task maximum.
        db.session.rollback()
    return f'TK-{top + 1:04d}'


# ---------------------------------------------------------------------------
# Cleanup / auto-clean (admin only; tasker_* tables only)
# ---------------------------------------------------------------------------
def settings_dict(row=None):
    """Auto-clean switches as plain JSON data (no DB write)."""
    return {
        'auto_clean_enabled': bool(row and row.auto_clean_enabled),
        'auto_clean_days': int(row.auto_clean_days or 30) if row else 30,
        'auto_clean_include_cancelled': (
            bool(row.auto_clean_include_cancelled) if row else True
        ),
        'updated_by': (row.updated_by if row else None),
        'updated_at': iso(row.updated_at) if row else None,
    }


def get_or_create_settings():
    row = TaskerSetting.query.get(1)
    if row is None:
        row = TaskerSetting(id=1)
        db.session.add(row)
        db.session.flush()
    return row


def cleanup_to_dict(c: TaskerCleanupLog):
    return {
        'id': c.id,
        'task_id': c.task_id,
        'task_no': c.task_no,
        'client_name': c.client_name or '',
        'status': c.status or '',
        'items_count': c.items_count or 0,
        'mode': c.mode,
        'deleted_by': c.deleted_by or '',
        'deleted_at': iso(c.deleted_at),
        'reason': c.reason or '',
    }


def delete_task(task, mode, by_user, reason='', now=None):
    """Permanently remove one task and keep a snapshot in the cleanup log.

    Items, events and deadline resets go with it through the model cascades.
    Nothing outside tasker_* is touched.
    """
    now = now or pk_model_now()
    summary = {
        'id': task.id,
        'task_no': task.task_no,
        'status': task.status or '',
        'client_name': task.client_name or '',
    }
    db.session.add(TaskerCleanupLog(
        task_id=task.id,
        task_no=task.task_no,
        client_name=task.client_name or '',
        status=task.status or '',
        items_count=len(task.items or []),
        mode=mode,
        deleted_by=by_user or None,
        deleted_at=now,
        reason=(reason or '')[:300],
    ))
    db.session.delete(task)
    return summary


def delete_tasks(tasks, mode, by_user, reason='', now=None):
    return [delete_task(t, mode, by_user, reason, now) for t in tasks]


def run_auto_clean(now=None, force=False):
    """Lazily delete closed tasks that passed the admin-set retention.

    Called from the board poll and the cheap global alarm poll, so closed tasks
    are cleaned even if nobody opens the Tasker page.  Only DELIVERED (and,
    when enabled, CANCELLED) tasks are ever candidates — an active task can
    never be auto-deleted.
    """
    row = TaskerSetting.query.get(1)
    if row is None or not row.auto_clean_enabled:
        return {'deleted': 0, 'skipped': 'disabled'}

    now = now or pk_model_now()
    state = current_app.extensions.setdefault('tasker_auto_clean', {})
    last = state.get('last_run_at')
    if not force and last and (now - last).total_seconds() < AUTO_CLEAN_MIN_INTERVAL_SEC:
        return {'deleted': 0, 'skipped': 'throttled'}
    state['last_run_at'] = now

    days = max(1, int(row.auto_clean_days or 30))
    statuses = ['DELIVERED']
    if row.auto_clean_include_cancelled:
        statuses.append('CANCELLED')
    cutoff = now - timedelta(days=days)

    rows = (
        TaskerTask.query
        .filter(TaskerTask.status.in_(statuses))
        .filter(db.func.coalesce(TaskerTask.updated_at, TaskerTask.created_at) <= cutoff)
        .order_by(TaskerTask.id.asc())
        .all()
    )
    if not rows:
        return {'deleted': 0, 'skipped': None}

    deleted = delete_tasks(
        rows, mode='AUTO', by_user='auto',
        reason=f'auto-clean: closed tasks older than {days} day(s)', now=now,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception('Tasker auto-clean failed')
        return {'deleted': 0, 'skipped': 'error'}
    return {'deleted': len(deleted), 'skipped': None, 'tasks': deleted}


# ---------------------------------------------------------------------------
# Read-only combo lookups (SELECT only — never writes master data)
# ---------------------------------------------------------------------------
def search_clients(q, limit=25):
    query = Client.query
    q = (q or '').strip()
    if q:
        query = query.filter(db.or_(
            Client.name.ilike(f'%{q}%'),
            Client.code.ilike(f'%{q}%'),
        ))
    rows = query.order_by(Client.is_active.desc(), Client.name.asc(), Client.id.asc()).limit(limit).all()
    return [
        {'id': c.id, 'name': c.name, 'code': c.code or '',
         'category': c.category or '', 'is_active': bool(c.is_active)}
        for c in rows
    ]


def search_materials(q, limit=25):
    query = Material.query
    q = (q or '').strip()
    if q:
        query = query.filter(db.or_(
            Material.name.ilike(f'%{q}%'),
            Material.code.ilike(f'%{q}%'),
        ))
    rows = query.order_by(Material.is_active.desc(), Material.name.asc(), Material.id.asc()).limit(limit).all()
    return [
        {'id': m.id, 'name': m.name, 'code': m.code or '',
         'unit': m.unit or '', 'is_active': bool(m.is_active)}
        for m in rows
    ]
