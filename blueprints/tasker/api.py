"""Tasker — JSON API: board, workflow actions, deadlines, alarm, lookups.

Read-only master lookups (clients / materials) live here purely for the combo
boxes.  Every mutation writes ONLY tasker_* tables.
"""
from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required, current_user

from models import TaskerDeadlineReset, TaskerItem, TaskerTask, db, pk_model_now

from ._common import (
    STAGE_ORDER,
    TERMINAL_STATUSES,
    event_to_dict,
    is_admin,
    log_event,
    next_task_no,
    parse_deadline,
    require_admin,
    reset_to_dict,
    search_clients,
    search_materials,
    sync_alarms,
    tasker_bp,
    task_to_dict,
)

# action name -> (from statuses, to status, stamp field)
_STAGE_ACTIONS = {
    'noted': (('NEW',), 'NOTED', 'noted'),
    'loaded': (('NOTED',), 'LOADED', 'loaded'),
    'dispatched': (('LOADED',), 'DISPATCHED', 'dispatched'),
    'delivered': (('DISPATCHED',), 'DELIVERED', 'delivered'),
}


# ---------------------------------------------------------------------------
# Board / alarm reads
# ---------------------------------------------------------------------------
def _board_query():
    return TaskerTask.query.order_by(
        TaskerTask.status.in_(TERMINAL_STATUSES).asc(),
        TaskerTask.delivery_deadline_at.is_(None).asc(),
        TaskerTask.delivery_deadline_at.asc(),
        TaskerTask.created_at.desc(),
    )


@tasker_bp.route('/api/board')
@login_required
def api_board():
    """Total list + counters.  Also syncs alarm transitions (tasker_* only)."""
    now = pk_model_now()
    tasks = _board_query().all()
    for t in tasks:
        sync_alarms(t, now=now)

    flt = (request.args.get('filter') or 'all').strip().lower()
    q = (request.args.get('q') or '').strip().lower()

    def _match(t):
        if flt == 'active' and t.status in TERMINAL_STATUSES:
            return False
        if flt == 'overdue' and not (t.note_alarm_on or t.delivery_alarm_on):
            return False
        if flt == 'done' and t.status != 'DELIVERED':
            return False
        if flt == 'cancelled' and t.status != 'CANCELLED':
            return False
        if q:
            hay = ' '.join(
                [t.task_no or '', t.client_name or '', t.client_code or '']
                + [i.material_name or '' for i in (t.items or [])]
            ).lower()
            if q not in hay:
                return False
        return True

    visible = [t for t in tasks if _match(t)]

    counts = {s: 0 for s in STAGE_ORDER}
    counts['CANCELLED'] = 0
    overdue = 0
    for t in tasks:
        counts[t.status] = counts.get(t.status, 0) + 1
        if t.note_alarm_on or t.delivery_alarm_on:
            overdue += 1

    db.session.commit()
    return jsonify({
        'now': now.isoformat(timespec='seconds'),
        'is_admin': is_admin(),
        'counters': {
            'total': len(tasks),
            'new': counts.get('NEW', 0),
            'noted': counts.get('NOTED', 0),
            'loaded': counts.get('LOADED', 0),
            'dispatched': counts.get('DISPATCHED', 0),
            'delivered': counts.get('DELIVERED', 0),
            'cancelled': counts.get('CANCELLED', 0),
            'overdue': overdue,
        },
        'tasks': [task_to_dict(t) for t in visible],
    })


@tasker_bp.route('/api/alarm_state')
@login_required
def api_alarm_state():
    """Cheap poll for the global alarm widget (read-only — no writes)."""
    now = pk_model_now()
    rows = TaskerTask.query.filter(
        TaskerTask.status.notin_(TERMINAL_STATUSES)
    ).all()
    tripped = []
    note_n = delivery_n = 0
    for t in rows:
        if t.note_deadline_at and t.status == 'NEW' and now > t.note_deadline_at:
            note_n += 1
            tripped.append({'task_no': t.task_no, 'client_name': t.client_name,
                            'kind': 'NOTE', 'overdue_since': t.note_deadline_at.isoformat(timespec='seconds')})
        if t.delivery_deadline_at and now > t.delivery_deadline_at:
            delivery_n += 1
            tripped.append({'task_no': t.task_no, 'client_name': t.client_name,
                            'kind': 'DELIVERY', 'overdue_since': t.delivery_deadline_at.isoformat(timespec='seconds')})
    return jsonify({
        'now': now.isoformat(timespec='seconds'),
        'overdue_count': note_n + delivery_n,
        'note_count': note_n,
        'delivery_count': delivery_n,
        'tasks': tripped,
    })


@tasker_bp.route('/api/tasks/<int:task_id>')
@login_required
def api_task_detail(task_id):
    task = TaskerTask.query.get_or_404(task_id)
    data = task_to_dict(task)
    data['events'] = [event_to_dict(e) for e in task.events]
    data['deadline_resets'] = [reset_to_dict(r) for r in task.deadline_resets]
    return jsonify(data)


# ---------------------------------------------------------------------------
# Read-only combo lookups
# ---------------------------------------------------------------------------
@tasker_bp.route('/api/clients/search')
@login_required
def api_clients_search():
    return jsonify(search_clients(request.args.get('q', '')))


@tasker_bp.route('/api/materials/search')
@login_required
def api_materials_search():
    return jsonify(search_materials(request.args.get('q', '')))


# ---------------------------------------------------------------------------
# Create (admin only)
# ---------------------------------------------------------------------------
@tasker_bp.route('/api/tasks', methods=['POST'])
@login_required
def api_create_task():
    guard = require_admin()
    if guard is not None:
        return guard

    payload = request.get_json(silent=True) or {}
    client_name = str(payload.get('client_name') or '').strip()
    if not client_name:
        return jsonify({'success': False, 'error': 'Client is required.'}), 400

    raw_items = payload.get('items') or []
    items = []
    for idx, raw in enumerate(raw_items, start=1):
        name = str((raw or {}).get('material_name') or '').strip()
        if not name:
            continue
        try:
            qty = float((raw or {}).get('qty') or 0)
        except (TypeError, ValueError):
            return jsonify({'success': False,
                            'error': f'Item {idx}: invalid quantity.'}), 400
        items.append({
            'material_ref_id': (raw or {}).get('material_ref_id'),
            'material_code': str((raw or {}).get('material_code') or '').strip(),
            'material_name': name,
            'qty': qty,
            'unit': str((raw or {}).get('unit') or '').strip(),
            'warranty': str((raw or {}).get('warranty') or '').strip(),
            'note': str((raw or {}).get('note') or '').strip(),
        })
    if not items:
        return jsonify({'success': False, 'error': 'At least one item is required.'}), 400

    try:
        note_dl = parse_deadline(payload.get('note_deadline_at'))
        del_dl = parse_deadline(payload.get('delivery_deadline_at'))
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400

    task = TaskerTask(
        task_no=next_task_no(),
        client_ref_id=payload.get('client_ref_id'),
        client_code=str(payload.get('client_code') or '').strip(),
        client_name=client_name,
        status='NEW',
        note_deadline_at=note_dl,
        delivery_deadline_at=del_dl,
        task_note=str(payload.get('task_note') or '').strip(),
        created_by=current_user.username,
        created_at=pk_model_now(),
    )
    db.session.add(task)
    db.session.flush()
    for idx, raw in enumerate(items, start=1):
        db.session.add(TaskerItem(task_id=task.id, line_no=idx, **raw))
    log_event(task, 'CREATED',
              f"{len(items)} item(s); note DL={note_dl}; delivery DL={del_dl}")
    sync_alarms(task, commit=False)
    db.session.commit()
    return jsonify({'success': True, 'task': task_to_dict(task)})


# ---------------------------------------------------------------------------
# Workflow actions (stage buttons: any signed-in user; cancel/reopen: admin)
# ---------------------------------------------------------------------------
@tasker_bp.route('/api/tasks/<int:task_id>/action', methods=['POST'])
@login_required
def api_task_action(task_id):
    task = TaskerTask.query.get_or_404(task_id)
    payload = request.get_json(silent=True) or {}
    action = str(payload.get('action') or '').strip().lower()
    note = str(payload.get('note') or '').strip()
    now = pk_model_now()

    if action in _STAGE_ACTIONS:
        allowed_from, to_status, stamp = _STAGE_ACTIONS[action]
        if task.status not in allowed_from:
            return jsonify({
                'success': False,
                'error': f'Cannot mark {to_status} while task is {task.status}.',
            }), 400
        task.status = to_status
        setattr(task, f'{stamp}_by', current_user.username)
        setattr(task, f'{stamp}_at', now)
        if action == 'delivered' and note:
            task.delivered_note = note[:300]
        log_event(task, to_status, note)
    elif action == 'cancel':
        if not is_admin():
            return jsonify({'success': False, 'error': 'Admin only.'}), 403
        if task.status in TERMINAL_STATUSES:
            return jsonify({'success': False,
                            'error': f'Cannot cancel a {task.status} task.'}), 400
        task.status_before_cancel = task.status
        task.status = 'CANCELLED'
        task.cancelled_by = current_user.username
        task.cancelled_at = now
        task.cancel_reason = note[:300]
        log_event(task, 'CANCELLED', note)
    elif action == 'reopen':
        if not is_admin():
            return jsonify({'success': False, 'error': 'Admin only.'}), 403
        if task.status != 'CANCELLED':
            return jsonify({'success': False,
                            'error': 'Only a cancelled task can be reopened.'}), 400
        task.status = task.status_before_cancel or 'NEW'
        task.status_before_cancel = None
        task.cancelled_by = None
        task.cancelled_at = None
        log_event(task, 'REOPENED', note)
    else:
        return jsonify({'success': False, 'error': f'Unknown action: {action!r}'}), 400

    sync_alarms(task, now=now, commit=False)
    db.session.commit()
    return jsonify({'success': True, 'task': task_to_dict(task)})


# ---------------------------------------------------------------------------
# Deadline reset (admin only) — "admin saw it and set a new time manually"
# ---------------------------------------------------------------------------
@tasker_bp.route('/api/tasks/<int:task_id>/reset_deadline', methods=['POST'])
@login_required
def api_task_reset_deadline(task_id):
    guard = require_admin()
    if guard is not None:
        return guard

    task = TaskerTask.query.get_or_404(task_id)
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason') or '').strip()
    now = pk_model_now()
    touched = False

    for kind, field in (('NOTE', 'note_deadline_at'), ('DELIVERY', 'delivery_deadline_at')):
        if field not in payload:
            continue
        try:
            new_dl = parse_deadline(payload.get(field))
        except ValueError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        if new_dl is not None and new_dl <= now:
            return jsonify({'success': False,
                            'error': 'The new deadline must be in the future.'}), 400
        old_dl = getattr(task, field)
        if new_dl == old_dl:
            continue
        setattr(task, field, new_dl)
        task.deadline_reset_count = (task.deadline_reset_count or 0) + 1
        db.session.add(TaskerDeadlineReset(
            task_id=task.id,
            kind=kind,
            old_deadline=old_dl,
            new_deadline=new_dl,
            by_user=current_user.username,
            at=now,
            reason=reason[:200],
        ))
        log_event(task, 'DEADLINE_RESET',
                  f'{kind}: {old_dl} -> {new_dl}' + (f' ({reason})' if reason else ''))
        touched = True

    if not touched:
        return jsonify({'success': False,
                        'error': 'Nothing to reset — provide a new note or delivery deadline.'}), 400

    sync_alarms(task, now=now, commit=False)
    db.session.commit()
    return jsonify({'success': True, 'task': task_to_dict(task)})
