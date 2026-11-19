"""Activity capture — real before/after logging for every audited model.

The problem this solves
----------------------
The app already had two log stores and neither could answer "what was added,
edited or deleted?":

* ``audit_log`` had 65 hand-placed calls, and ``sales/ops/masters/ledgers``
  contained **zero** of them.  A booking or a direct sale left only the generic
  row ``http.post.sales.add_booking | path=/add_booking status=302`` — no bill
  number, no client, no amount.
* ``accounting_audit_log`` *did* store before/after JSON, but only from 39
  write sites in 15 files, and **no template rendered it at all**.

So instead of hand-editing thousands of route lines, this module listens to the
SQLAlchemy session itself:

``before_flush``   stage a field-level diff for every new / dirty / deleted row
                   of an audited model (primary key, human label, money amount,
                   changed columns with old -> new values).
``after_commit``   flush the staged diffs into ``activity_event`` with the
                   request's user, IP, session id, path and duration.
``after_rollback`` throw the staged diffs away.

That last line is the important one: a save that failed writes **nothing**, so an
entry can never appear in the log for an operation that did not happen (the
"302 means success" ambiguity the old log had).

Everything here is best-effort telemetry, exactly like ``utils/audit.py``: an
exception inside a listener is logged at debug level and swallowed, because a
broken log must never block a cashier's save.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from flask import g, has_request_context, request, session as flask_session
from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session as SASession

from models.__base import db, pk_model_now

LOG = logging.getLogger('activity')

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
# How many changed fields to serialise per row.  A full-history ledger rebuild
# can touch hundreds of columns; the log must stay bounded.
MAX_CHANGED_FIELDS = 40
MAX_CHANGED_EVENTS_PER_REQUEST = 60
MAX_JSON_CHARS = 20000

# Columns that are noise: they change on every save of the row they belong to,
# or they hold derived mirrors we can rebuild.  Logging them would bury the one
# field the operator actually changed.
_IGNORED_COLUMNS = {
    'updated_at', 'created_at', 'revision', 'idempotency_key',
    'idempotency_payload_hash', 'password_hash', 'password_plain',
}

# Fields the log should never store a copy of.
_SECRET_COLUMNS = {'password_hash', 'password_plain', 'client_ledger_qr_token_hash',
                   'client_ledger_qr_token_salt', 'google_client_secret',
                   'google_refresh_token', 'google_access_token', 'code_hash'}


def _audited_specs():
    """model class -> (module, label fields, money field)."""
    from models import (
        Account, AccountTransaction, Booking, Client, DeliveryPerson, DirectSale,
        Entry, GRN, ImportJob, Invoice, Material, MaterialReturn, Payment,
        PendingBill, StockAdjustment, Supplier, SupplierPayment, User,
    )

    return {
        DirectSale: ('sales', ('auto_bill_no', 'manual_bill_no', 'client_name'), 'amount'),
        Booking: ('sales', ('auto_bill_no', 'manual_bill_no', 'client_name'), 'amount'),
        Payment: ('sales', ('auto_bill_no', 'manual_bill_no', 'client_name'), 'amount'),
        MaterialReturn: ('sales', ('auto_bill_no', 'manual_bill_no', 'client_name'), 'amount'),
        Invoice: ('sales', ('invoice_no', 'client_name'), 'total_amount'),
        PendingBill: ('sales', ('bill_no', 'client_name'), 'amount'),
        GRN: ('ops', ('auto_bill_no', 'manual_bill_no', 'supplier'), 'paid_amount'),
        Entry: ('ops', ('auto_bill_no', 'bill_no', 'client'), None),
        StockAdjustment: ('ops', ('reference', 'material'), 'total_value'),
        SupplierPayment: ('accounts', ('auto_bill_no', 'manual_bill_no'), 'amount'),
        Account: ('accounts', ('name',), 'balance'),
        AccountTransaction: ('accounts', ('description', 'party_name'), 'amount'),
        Client: ('masters', ('name', 'code'), 'opening_balance'),
        Supplier: ('masters', ('name',), 'opening_balance'),
        Material: ('masters', ('code', 'name'), None),
        DeliveryPerson: ('masters', ('name',), 'opening_balance'),
        User: ('system', ('username',), None),
        ImportJob: ('import_export', ('status',), None),
    }


_SPECS = None


def specs():
    global _SPECS
    if _SPECS is None:
        try:
            _SPECS = _audited_specs()
        except Exception:  # pragma: no cover - import cycle guard
            LOG.debug('activity: audited model list unavailable', exc_info=True)
            _SPECS = {}
    return _SPECS


# ---------------------------------------------------------------------------
# Request context helpers
# ---------------------------------------------------------------------------
def _request_id():
    if not has_request_context():
        return None
    rid = getattr(g, 'ams_request_id', None)
    if rid:
        return rid
    # errors.py installs the reference code; reuse it when present so a 500 in
    # the Error tab and the activity row share one id.
    rid = getattr(g, 'ams_error_reference', None)
    try:
        g.ams_request_id = rid or None
    except Exception:
        pass
    return rid


def _actor():
    """(user_id, username, role) for the current request, best effort."""
    if not has_request_context():
        return (None, None, None)
    try:
        from flask_login import current_user

        if getattr(current_user, 'is_authenticated', False):
            return (
                getattr(current_user, 'id', None),
                (getattr(current_user, 'username', None) or '').strip() or None,
                (getattr(current_user, 'role', None) or '').strip() or None,
            )
    except Exception:
        pass
    username = None
    try:
        username = (flask_session.get('ams_username') or None)
    except Exception:
        username = None
    return (None, username, None)


def _client_ip():
    if not has_request_context():
        return None
    try:
        forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
        return forwarded or request.remote_addr
    except Exception:
        return None


def _sid():
    if not has_request_context():
        return None
    try:
        return flask_session.get('ams_sid')
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Value serialisation
# ---------------------------------------------------------------------------
def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return f'<{len(value)} bytes>'
    return str(value)


def _dumps(payload):
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        return None
    if len(text) > MAX_JSON_CHARS:
        text = text[:MAX_JSON_CHARS] + '…'
    return text


def _display(field, value):
    """A short, readable rendering for the diff table."""
    value = _jsonable(value)
    if isinstance(value, float):
        return f'{value:,.2f}'.rstrip('0').rstrip('.')
    if isinstance(value, str) and len(value) > 160:
        return value[:160] + '…'
    return '' if value is None else str(value)


def _minor(minor_value, legacy_value=None):
    """Prefer the integer minor-unit mirror; fall back to rounding the float."""
    if minor_value is not None:
        try:
            return int(minor_value)
        except (TypeError, ValueError):
            pass
    if legacy_value is not None:
        try:
            return int(round(float(legacy_value) * 100))
        except (TypeError, ValueError):
            return None
    return None


def _label(obj, label_fields):
    for field in label_fields:
        try:
            value = getattr(obj, field, None)
        except Exception:
            continue
        text = (str(value).strip() if value is not None else '')
        if text and text.upper() not in ('NONE', 'NULL'):
            return text[:200]
    return None


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------
def _buffer():
    buf = getattr(g, '_ams_activity_buffer', None)
    if buf is None:
        buf = []
        try:
            g._ams_activity_buffer = buf
        except Exception:
            return None
    return buf


def _stage(session, flush_context, instances):
    # A logging bug must never refuse a cashier's save: this is telemetry.
    try:
        _stage_inner(session, flush_context, instances)
    except Exception:
        LOG.debug('activity: staging failed for this flush', exc_info=True)


def _stage_inner(session, flush_context, instances):
    if not has_request_context():
        return
    table = specs()
    if not table:
        return
    buf = _buffer()
    if buf is None or len(buf) >= MAX_CHANGED_EVENTS_PER_REQUEST:
        return

    for obj in list(session.new):
        spec = table.get(type(obj))
        if spec:
            _stage_one(obj, spec, 'create', buf)
    for obj in list(session.dirty):
        spec = table.get(type(obj))
        if spec:
            _stage_one(obj, spec, 'update', buf)
    for obj in list(session.deleted):
        spec = table.get(type(obj))
        if spec:
            _stage_one(obj, spec, 'delete', buf)


def _stage_one(obj, spec, kind, buf):
    module, label_fields, money_field = spec
    mapper = sa_inspect(type(obj))
    label = _label(obj, label_fields)
    entity_id = None
    try:
        entity_id = sa_inspect(obj).identity[0] if sa_inspect(obj).identity else None
    except Exception:
        entity_id = getattr(obj, 'id', None)

    changed = []
    before = {}
    after = {}
    if kind == 'create':
        for column in mapper.columns:
            name = column.key
            if name in _SECRET_COLUMNS:
                continue
            try:
                value = getattr(obj, name, None)
            except Exception:
                continue
            if value is None or name in _IGNORED_COLUMNS:
                continue
            after[name] = _jsonable(value)
    elif kind == 'delete':
        for column in mapper.columns:
            name = column.key
            if name in _SECRET_COLUMNS:
                continue
            try:
                value = getattr(obj, name, None)
            except Exception:
                continue
            if value is None or name in _IGNORED_COLUMNS:
                continue
            before[name] = _jsonable(value)
    else:
        state = sa_inspect(obj)
        for prop in state.mapper.column_attrs:
            name = prop.key
            if name in _IGNORED_COLUMNS or name in _SECRET_COLUMNS:
                continue
            try:
                history = state.attrs[name].history
            except KeyError:
                # A deferred/unloaded attribute cannot be diffed without
                # forcing a SELECT; skip it rather than load the world.
                continue
            if not history.has_changes():
                continue
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else None
            if old == new:
                continue
            before[name] = _jsonable(old)
            after[name] = _jsonable(new)
            if len(changed) < MAX_CHANGED_FIELDS:
                changed.append({
                    'field': name,
                    'before': _display(name, old),
                    'after': _display(name, new),
                })
        if not changed:
            return  # dirty in name only (e.g. a loaded collection) — nothing to log

    # A "soft delete" is an UPDATE that only flips a status flag.  An operator
    # looking for "who deleted this" means the suspend, not the earlier edit, so
    # classify it as a delete and say which kind it was.
    result = 'success'
    if kind == 'update' and changed:
        fields = {c['field'] for c in changed}
        flags = {'is_active', 'is_void', 'is_cancelled', 'ended_at'}
        if fields <= flags:
            flag = next(iter(fields))
            after_value = after.get(flag)
            falsy = after_value in (False, 0, '0', 'False', 'false', None)
            if falsy:
                kind = 'delete'
                result = 'void' if flag in ('is_void', 'is_cancelled') else 'soft'
            else:
                result = 'restore'

    amount_minor = None
    if money_field:
        try:
            amount_minor = _minor(
                getattr(obj, f'{money_field}_minor', None),
                getattr(obj, money_field, None),
            )
        except Exception:
            amount_minor = None

    buf.append({
        '_obj': obj,
        '_labels': label_fields,
        'category': kind,
        'result': result,
        'module': module,
        'action': f'{kind}:{type(obj).__name__}',
        'entity_type': type(obj).__name__,
        'entity_id': entity_id,
        'entity_label': label,
        'amount_minor': amount_minor,
        'summary': _summary(kind, type(obj).__name__, label, changed, amount_minor, result=result),
        'before_json': _dumps(before) if before else None,
        'after_json': _dumps(after) if after else None,
        'changed_json': _dumps(changed) if changed else None,
    })


def _finalize(session, flush_context):
    try:
        _finalize_inner(session, flush_context)
    except Exception:
        LOG.debug('activity: finalize failed', exc_info=True)


def _finalize_inner(session, flush_context):
    """Fill in what only exists after the INSERT: the primary key and label.

    A ``before_flush`` listener sees a brand-new row with ``id=None`` and,
    on this codebase, sometimes before the bill number has been normalised.
    ``after_flush_postexec`` runs once the INSERT is done, so the id and the
    normalised bill number are final there.
    """
    if not has_request_context():
        return
    buf = getattr(g, '_ams_activity_buffer', None)
    if not buf:
        return
    for entry in buf:
        obj = entry.pop('_obj', None)
        labels = entry.pop('_labels', None) or ()
        if obj is None:
            continue
        if entry.get('entity_id') is None:
            try:
                identity = sa_inspect(obj).identity
                if identity:
                    entry['entity_id'] = identity[0]
            except Exception:
                pass
        better = _label(obj, labels)
        if better:
            entry['entity_label'] = better
            if entry.get('category') == 'create':
                entry['summary'] = _summary(
                    'create', entry.get('entity_type') or '', better, [],
                    entry.get('amount_minor'),
                )


def _summary(kind, entity, label, changed, amount_minor, result='success'):
    verb = {'create': 'Added', 'update': 'Edited', 'delete': 'Deleted'}.get(kind, kind.title())
    if result == 'soft':
        verb = 'Suspended'
    elif result == 'void':
        verb = 'Voided'
    elif result == 'restore':
        verb = 'Restored'
    parts = [verb, entity.replace('_', ' ')]
    if label:
        parts.append(f'“{label}”')
    if kind == 'update' and changed:
        fields = ', '.join(c['field'] for c in changed[:6])
        parts.append(f'({len(changed)} field{"s" if len(changed) != 1 else ""}: {fields}'
                     + ('…' if len(changed) > 6 else '') + ')')
    if amount_minor is not None:
        try:
            parts.append(f'Rs {amount_minor / 100:,.0f}')
        except Exception:
            pass
    return ' '.join(parts)[:500]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _write_rows(rows):
    """Insert staged rows on a short-lived connection.

    Deliberately not the request session: at ``after_commit`` time the request
    transaction is finished, and asking the pool for it again would either
    re-open a transaction or hit the same SQLite write lock the request just
    released.  ``busy_timeout`` is small so a busy shop never delays a page.
    """
    if not rows:
        return
    bind = db.session.get_bind()
    payload = []
    for row in rows:
        row['occurred_at'] = pk_model_now()
        payload.append(row)
    try:
        with SASession(bind) as s:
            if bind.dialect.name == 'sqlite':
                from sqlalchemy import text as _text
                s.execute(_text('PRAGMA busy_timeout = 1500'))
            s.bulk_insert_mappings(
                _event_model(),
                [{k: v for k, v in row.items() if k in _INSERT_FIELDS} for row in payload],
            )
            s.commit()
    except Exception:
        LOG.debug('activity: could not persist %d event(s)', len(payload), exc_info=True)
        _note_drop(len(payload))


_INSERT_FIELDS = (
    'occurred_at', 'category', 'result', 'module', 'action', 'entity_type',
    'entity_id', 'entity_label', 'amount_minor', 'summary', 'before_json',
    'after_json', 'changed_json', 'user_id', 'username', 'role', 'ip',
    'session_sid', 'request_id', 'method', 'path', 'status_code', 'duration_ms',
    'bucket_minute', 'hits',
)


def _event_model():
    from models import ActivityEvent
    return ActivityEvent


def _note_drop(count):
    """Remember that events were lost, so the UI can say so instead of lying."""
    try:
        g.ams_activity_dropped = int(getattr(g, 'ams_activity_dropped', 0)) + int(count)
    except Exception:
        pass


def dropped_in_request():
    return int(getattr(g, 'ams_activity_dropped', 0) or 0)


def _on_commit(session):
    """Flush staged diffs after the request's work is durable."""
    buf = getattr(g, '_ams_activity_buffer', None) if has_request_context() else None
    if not buf:
        return
    rows = []
    user_id, username, role = _actor()
    ip = _client_ip()
    sid = _sid()
    rid = _request_id()
    method = path = None
    status_code = None
    duration_ms = None
    if has_request_context():
        method = request.method
        path = _safe_path(request.path)
        status_code = getattr(g, 'ams_response_status', None)
        started = getattr(g, 'ams_request_started_at', None)
        if started:
            try:
                import time as _time
                duration_ms = int((_time.time() - started) * 1000)
            except Exception:
                duration_ms = None
    for row in buf:
        row.update({
            'user_id': user_id, 'username': username, 'role': role,
            'ip': ip, 'session_sid': sid, 'request_id': rid,
            'method': method, 'path': path,
            'status_code': status_code, 'duration_ms': duration_ms,
        })
        rows.append(row)
    buf.clear()
    _write_rows(rows)


def _on_rollback(session):
    buf = getattr(g, '_ams_activity_buffer', None) if has_request_context() else None
    if buf:
        buf.clear()


def _safe_path(raw):
    """Client-ledger QR links carry a bearer token — never store it."""
    if raw and str(raw).startswith('/c/'):
        return '/c/[client-ledger-token]'
    return (str(raw or ''))[:300]


# ---------------------------------------------------------------------------
# Public helpers used by hooks / routes
# ---------------------------------------------------------------------------
def log_event(category, action, *, result='success', module=None, entity_type=None,
              entity_id=None, entity_label=None, amount_minor=None, summary=None,
              before=None, after=None, changed=None, commit_now=True):
    """Write one explicit activity row (logins, imports, page-view buckets)."""
    error = None
    try:
        user_id, username, role = _actor()
        row = {
            'category': category,
            'result': result,
            'module': module,
            'action': action,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'entity_label': entity_label,
            'amount_minor': amount_minor,
            'summary': (summary or '')[:500] or None,
            'before_json': _dumps(before) if before else None,
            'after_json': _dumps(after) if after else None,
            'changed_json': _dumps(changed) if changed else None,
            'user_id': user_id,
            'username': username,
            'role': role,
            'ip': _client_ip(),
            'session_sid': _sid(),
            'request_id': _request_id(),
            'method': request.method if has_request_context() else None,
            'path': _safe_path(request.path) if has_request_context() else None,
            'status_code': getattr(g, 'ams_response_status', None) if has_request_context() else None,
            'occurred_at': pk_model_now(),
        }
        if commit_now:
            _write_rows([row])
        return True
    except Exception:
        error = True
        LOG.debug('activity: explicit event failed', exc_info=True)
    if error:
        _note_drop(1)
    return False


def log_page_view(path, label, *, username=None, user_id=None, commit_now=True):
    """Aggregate page views: one row per (user, path, minute) with a hit count.

    A row per page load would be the single biggest writer in the database —
    this app's own docs are full of SQLite write-lock pain.  One bucket row per
    minute is enough for every filter the UI offers and costs ~1 write/min/tab.
    """
    if not has_request_context():
        return False
    if (request.method or '').upper() not in ('GET',):
        return False
    if path.startswith('/static') or path.startswith('/api/') or path.startswith('/c/'):
        return False
    if path.startswith('/import_export/jobs/') or path.endswith('/heartbeat'):
        return False
    # One bucket write per (tab, path) per 45 s.  Without this every single GET
    # would open a write transaction — the exact SQLite lock pressure this app
    # already suffers from.  Reloads inside the window still count, they just
    # land in the same minute bucket.
    import time as _time
    now_ts = _time.time()
    touched = flask_session.get('ams_pv_touch')
    if not isinstance(touched, dict):
        touched = {}
    if now_ts - float(touched.get(path) or 0) < 45:
        return False
    touched = {k: v for k, v in touched.items() if now_ts - float(v or 0) < 600}
    touched[path] = now_ts
    try:
        flask_session['ams_pv_touch'] = touched
    except Exception:
        pass
    try:
        minute = pk_model_now().strftime('%Y%m%d%H%M')
        user_id = user_id if user_id is not None else _actor()[0]
        username = username or _actor()[1]
        _upsert_bucket(minute, user_id, username, path, label)
        return True
    except Exception:
        LOG.debug('activity: page-view bucket failed', exc_info=True)
        return False


def _upsert_bucket(minute, user_id, username, path, label):
    bind = db.session.get_bind()
    sql = (
        'INSERT INTO activity_event '
        '(occurred_at, category, result, module, action, entity_label, summary, user_id, '
        ' username, role, ip, path, method, bucket_minute, hits) '
        'VALUES (:occurred_at, :category, :result, :module, :action, :label, :summary, :user_id, '
        ' :username, :role, :ip, :path, :method, :bucket, 1)'
    )
    params = {
        'occurred_at': pk_model_now(),
        'category': 'view',
        'result': 'success',
        'module': (path.split('/')[1] or 'index') if path.count('/') >= 1 else 'index',
        'action': 'view',
        'label': (label or path)[:200],
        'summary': f'Viewed {(label or path)[:120]}',
        'user_id': user_id,
        'username': username,
        'role': _actor()[2],
        'ip': _client_ip(),
        'path': path[:300],
        'method': 'GET',
        'bucket': minute,
    }
    # Aggregate in place: SQLite has no ON CONFLICT for "hits = hits + 1" on an
    # expression index, but (bucket_minute, username, path) is a natural key.
    try:
        with SASession(bind) as s:
            if bind.dialect.name == 'sqlite':
                from sqlalchemy import text as _text
                s.execute(_text('PRAGMA busy_timeout = 250'))
            row = s.execute(
                db.text(
                    'SELECT id, hits FROM activity_event WHERE category = :c AND '
                    'bucket_minute = :b AND username IS :u AND path = :p LIMIT 1'
                ),
                {'c': 'view', 'b': minute, 'u': username, 'p': path[:300]},
            ).fetchone()
            if row:
                s.execute(
                    db.text('UPDATE activity_event SET hits = COALESCE(hits, 1) + 1 WHERE id = :i'),
                    {'i': row[0]},
                )
            else:
                s.execute(db.text(sql), params)
            s.commit()
    except Exception:
        # A busy database simply loses one page-view tick — never the page.
        raise


def purge(retention_days_events=365, retention_days_views=30, retention_days_sessions=90):
    """Delete old activity rows.  Called from tools/maintenance.py."""
    from datetime import timedelta

    now = pk_model_now()
    removed = {'activity_event': 0, 'activity_session': 0}
    bind = db.session.get_bind()
    try:
        with SASession(bind) as s:
            res = s.execute(
                db.text('DELETE FROM activity_event WHERE category = :c AND occurred_at < :cut'),
                {'c': 'view', 'cut': now - timedelta(days=int(retention_days_views))},
            )
            removed['activity_event'] += res.rowcount or 0
            res = s.execute(
                db.text('DELETE FROM activity_event WHERE category <> :c AND occurred_at < :cut'),
                {'c': 'view', 'cut': now - timedelta(days=int(retention_days_events))},
            )
            removed['activity_event'] += res.rowcount or 0
            res = s.execute(
                db.text('DELETE FROM activity_session WHERE ended_at IS NOT NULL AND ended_at < :cut'),
                {'cut': now - timedelta(days=int(retention_days_sessions))},
            )
            removed['activity_session'] += res.rowcount or 0
            s.commit()
    except Exception:
        LOG.warning('activity: purge failed', exc_info=True)
    return removed


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
_INSTALLED = False


def install_listeners():
    """Attach the capture listeners once per process."""
    global _INSTALLED
    if _INSTALLED:
        return False
    event.listen(db.session, 'before_flush', _stage)
    event.listen(db.session, 'after_flush_postexec', _finalize)
    event.listen(db.session, 'after_commit', _on_commit)
    event.listen(db.session, 'after_rollback', _on_rollback)
    _INSTALLED = True
    LOG.info('activity: ORM capture listeners installed for %d model(s)', len(specs()))
    return True
