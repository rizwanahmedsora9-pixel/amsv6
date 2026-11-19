"""logs — the redesigned Logs section (split from system.py).

One page, eight tabs, plus a Users tab.  Every tab is a URL
(``/logs/<tab>``) rather than a JS router, so a filtered view can be
bookmarked, refreshed, shared and opened in a second screen by the proprietor.

Tabs and their real data sources:

    logins         ActivityEvent(category=login)   — success / failed / blocked / logout
    added          ActivityEvent(category=create)  — ORM capture, real payload
    edited         ActivityEvent(category=update)  — field-level before → after diff
    deleted        ActivityEvent(category=delete)  — what was removed
    views          ActivityEvent(category=view)    — aggregated page views
    import_export  ActivityEvent(import_export) + ImportUpload/ImportJob
    errors         ActivityEvent(category=error) + instance/logs/*.jsonl/txt
    server         instance/logs/request.log, server.log, errorlog.txt
    users          activity_session presence + every registered account
"""
from __future__ import annotations

from ._common import *  # noqa

import json
from datetime import timedelta

from app.services import activity_queries as AQ
from app.services import system_logs as SL
from app.services import presence as PS

TAB_META = {
    'all': ('All logs', 'bi-collection', 'Everything captured, newest first — logins, saves, edits, deletes, page views, imports and errors in one stream.'),
    'logins': ('Logins', 'bi-box-arrow-in-right', 'Who signed in, from where, and how it ended.'),
    'added': ('Entries added', 'bi-plus-circle', 'Every record created through the UI, with its real payload.'),
    'edited': ('Entries edited', 'bi-pencil-square', 'Field-level changes: what it was, what it became.'),
    'deleted': ('Entries deleted', 'bi-trash', 'Records removed or suspended — and what was kept.'),
    'views': ('Page views', 'bi-eye', 'Which screens are used, by whom, how often.'),
    'import_export': ('Import / Export', 'bi-arrow-left-right', 'Database import and export operations with row counts.'),
    'errors': ('Error logs', 'bi-exclamation-octagon', 'Application failures, grouped so repeats collapse.'),
    'server': ('Server logs', 'bi-hdd-stack', 'Raw request and application log files.'),
    'users': ('Users', 'bi-people', 'Every registered account with live presence.'),
}

TAB_ORDER = tuple(TAB_META.keys())


def _raw_error_records(app, limit=40):
    """Parse instance/logs/500-errors.jsonl defensively (see errors.py)."""
    import json
    from pathlib import Path
    path = Path(app.instance_path) / 'logs' / SL.ERROR_FILE
    out = []
    try:
        with path.open('r', encoding='utf-8', errors='replace') as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({'at': '', 'reference': '—', 'exception': 'Unparsed log line',
                                'message': SL._signature(line), 'traceback': line[:2000]})
    except OSError:
        return []
    return list(reversed(out[-limit:]))


def _guard():
    """Logs are an admin/settings-permission area, same rule as before.

    Authentication comes FIRST.  Reading ``current_user.role`` before the
    login check raised ``AttributeError: 'AnonymousUserMixin' object has no
    attribute 'role'`` for any anonymous visitor and returned a 500 page
    instead of the login redirect every other protected screen gives.
    """
    if not getattr(current_user, 'is_authenticated', False):
        return redirect(url_for('login', next=request.path))
    if getattr(current_user, 'role', None) in ('admin', 'root'):
        return None
    from app.services.permissions import _user_can
    if _user_can('can_access_settings'):
        return None
    flash('Unauthorized: Admin access required.', 'danger')
    return redirect(url_for('index'))


def _tabs_summary(filters):
    counts = AQ.tab_counts(filters)
    tabs = []
    for key in TAB_ORDER:
        label, icon, hint = TAB_META[key]
        # The category filter only exists on the All tab; carrying it to a tab
        # that is already narrowed to one category would just confuse the URL.
        query = filters.as_query(include_category=(key == AQ.ALL_TAB))
        tabs.append({
            'key': key,
            'label': label,
            'icon': icon,
            'hint': hint,
            'count': counts.get(key),
            'url': url_for('system.logs_page', tab=key) + ('?' + query if query else ''),
        })
    return tabs


def _category_options():
    """Known categories in reading order, then any new one present in the data."""
    options = list(AQ.ALL_CATEGORIES)
    for value in AQ.distinct_values(ActivityEvent.category):
        if value and value not in options:
            options.append(value)
    return options


def _base_context(tab, filters):
    return {
        'tab': tab,
        'tabs': _tabs_summary(filters),
        'filters': filters,
        'filter_query': filters.as_query(),
        'tab_meta': TAB_META,
        'range_choices': AQ.RANGE_CHOICES,
        'coverage': AQ.coverage(),
        'legacy': AQ.legacy_counts(),
        'dropped_events': 0,
        'user_options': AQ.distinct_values(ActivityEvent.username),
        'module_options': AQ.distinct_values(ActivityEvent.module),
        'role_options': AQ.distinct_values(ActivityEvent.role),
        'result_options': AQ.distinct_values(ActivityEvent.result),
        'entity_options': AQ.distinct_values(ActivityEvent.entity_type),
        'category_options': _category_options(),
        'presence_counts': PS.state_counts(),
    }


@bp.route('/logs')
@bp.route('/logs/')
@login_required
def logs_index():
    guard = _guard()
    if guard:
        return guard
    filters = AQ.Filters(request.args)
    return redirect(url_for('system.logs_page', tab=AQ.ALL_TAB) + ('?' + filters.as_query() if filters.as_query() else ''))


#: Columns of the CSV the filter bar's "CSV" button downloads, in the same
#: order the table shows them (plus the identifiers an accountant needs).
CSV_COLUMNS = (
    'occurred_at', 'category', 'result', 'username', 'role', 'module', 'action',
    'summary', 'entity_type', 'entity_id', 'entity_label', 'amount', 'path',
    'ip', 'request_id',
)
CSV_MAX_ROWS = 50000


def _csv_query(tab, filters):
    """The query behind the CSV export, or None for tabs that have no rows to export."""
    if tab == AQ.ALL_TAB:
        return AQ.all_query(filters)
    if tab == 'errors':
        return AQ.tab_query('error', filters)
    if tab in AQ.CATEGORY_TABS:
        return AQ.tab_query(AQ.CATEGORY_TABS[tab], filters)
    return None


def _csv_response(tab, filters):
    """Download the current tab + filter as CSV.

    The button used to link to ``?format=csv`` with nothing listening on the
    other end, so it silently reloaded the page.  This exports exactly what the
    table shows (same filters, same sort, capped at CSV_MAX_ROWS) with a UTF-8
    BOM so Excel opens Urdu labels correctly.
    """
    import csv as _csv

    query = _csv_query(tab, filters)
    if query is None:
        flash('CSV export is available on the activity tabs (All logs, Logins, '
              'Entries added/edited/deleted, Page views, Import / Export, Errors).',
              'warning')
        return redirect(url_for('system.logs_page', tab=tab))

    stream = io.StringIO()
    stream.write('\ufeff')
    writer = _csv.writer(stream)
    writer.writerow(CSV_COLUMNS)
    for row in query.limit(CSV_MAX_ROWS).all():
        amount = '' if row.amount_minor is None else f'{row.amount_minor / 100:.2f}'
        writer.writerow([
            row.occurred_at.strftime('%Y-%m-%d %H:%M:%S') if row.occurred_at else '',
            row.category, row.result, row.username, row.role, row.module,
            row.action, row.summary, row.entity_type, row.entity_id,
            row.entity_label, amount, row.path, row.ip, row.request_id,
        ])
    stamp = AQ.now().strftime('%Y%m%d-%H%M%S')
    payload = stream.getvalue().encode('utf-8')
    response = Response(payload, mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename="ams-logs-{tab}-{stamp}.csv"'
    response.headers['Content-Length'] = str(len(payload))
    return response


@bp.route('/logs/<tab>')
@login_required
def logs_page(tab):
    guard = _guard()
    if guard:
        return guard
    if tab not in TAB_META:
        return redirect(url_for('system.logs_page', tab=AQ.ALL_TAB))
    filters = AQ.Filters(request.args)
    if (request.args.get('format') or '').strip().lower() == 'csv':
        return _csv_response(tab, filters)
    page = request.args.get('page', 1, type=int)
    ctx = _base_context(tab, filters)

    # 'errors' maps to category='error' but has its own richer view (grouping,
    # on-disk records), so it is excluded here and handled below.
    if tab == AQ.ALL_TAB or (tab in AQ.CATEGORY_TABS and tab != 'errors'):
        # 'all' is the same table without a category predicate: one stream of
        # logins, saves, edits, deletes, views, imports and errors, newest
        # first.  Paging, filtering and the "part of the same save" marking are
        # identical to the category tabs.
        query = AQ.all_query(filters) if tab == AQ.ALL_TAB else AQ.tab_query(AQ.CATEGORY_TABS[tab], filters)
        ctx['pager'] = AQ.paginate(query, page)
        rows = ctx['pager']['rows']
        for row in rows:
            row.changes = AQ.decode_changes(row)
            row.is_derived = False
        # One operator action can write several rows (a booking also creates its
        # pending bill and stock movement).  Rows from the same request, within
        # a couple of seconds, are marked as derived so the table shows one
        # action instead of looking like three separate saves.
        seen = []
        for row in sorted(rows, key=lambda r: (r.occurred_at or AQ.now(), r.id or 0)):
            for earlier in seen:
                same_request = (row.request_id and row.request_id == earlier.request_id)
                same_save = (
                    row.path and earlier.path == row.path
                    and row.occurred_at and earlier.occurred_at
                    and abs((row.occurred_at - earlier.occurred_at).total_seconds()) <= 2
                )
                if same_request or same_save:
                    row.is_derived = True
                    break
            seen.append(row)
        ctx['query_string'] = filters.as_query()
        if tab == 'views':
            ctx['top_pages_list'] = AQ.top_pages(filters)
            ctx['per_day_list'] = AQ.per_day(filters, category='view')

    elif tab == 'errors':
        query = AQ.tab_query('error', filters)
        ctx['pager'] = AQ.paginate(query, page)
        # Grouped view of the same window, so "same error ×23" is one line.
        window = AQ.tab_query('error', filters).limit(2000).all()
        grouped = {}
        for row in window:
            key = (row.action or 'Error', (row.summary or '')[:120])
            bucket = grouped.setdefault(key, {
                'action': row.action, 'count': 0, 'first': row.occurred_at,
                'last': row.occurred_at, 'sample': row.summary, 'users': set(),
                'paths': set(), 'reference': row.entity_label,
            })
            bucket['count'] += 1
            bucket['last'] = max(bucket['last'] or row.occurred_at, row.occurred_at)
            bucket['first'] = min(bucket['first'] or row.occurred_at, row.occurred_at)
            if row.username:
                bucket['users'].add(row.username)
            if row.path:
                bucket['paths'].add(row.path)
        ctx['grouped_errors'] = sorted(grouped.values(), key=lambda b: b['count'], reverse=True)[:25]
        # On-disk structured errors (instance/logs/500-errors.jsonl).
        try:
            from app.services.errors import recent_errors
            records = recent_errors(limit=40)
        except Exception:
            records = []
        if not records:
            # Fall back to the raw file: a line the JSON parser rejects must not
            # make the whole tab look empty.
            records = _raw_error_records(current_app)
        ctx['disk_errors'] = records
        ctx['log_files'] = SL.available_files(current_app)
        ctx['level_counts'] = SL.level_counts(current_app, 'errorlog.txt', limit=2000)
        ctx['error_tail'] = [SL.parse_line(line, i) for i, line in
                             enumerate(SL.tail_lines(current_app, 'errorlog.txt', 200)[0])]

    elif tab == 'server':
        filename = (request.args.get('file') or 'request.log').strip()
        files = SL.available_files(current_app)
        names = {f['name'] for f in files}
        if filename not in names:
            filename = 'request.log' if 'request.log' in names else (files[0]['name'] if files else '')
        limit = request.args.get('limit', 400, type=int)
        level = (request.args.get('level') or '').strip()
        contains = (request.args.get('contains') or '').strip()
        logger_name = (request.args.get('logger') or '').strip()
        entries, size = SL.read_entries(
            current_app, filename, limit=limit, level=level, contains=contains, logger=logger_name,
        )
        ctx.update({
            'files': files,
            'filename': filename,
            'entries': list(reversed(entries)),
            'file_size': size,
            'file_size_human': SL.human_size(size),
            'limit': limit,
            'level': level,
            'contains': contains,
            'logger_name': logger_name,
            'levels_available': ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
            'level_counts': SL.level_counts(current_app, filename, limit=4000) if filename else {},
        })
        # Quick "what does a request line look like" summary.
        request_entries = [
            e for e in entries
            if '"/' in (e.get('message') or '') and ' - ' in (e.get('message') or '')
        ]
        ctx['request_sample'] = list(reversed(request_entries[-15:]))

    elif tab == 'users':
        ctx['user_rows'] = PS.user_presence_summary()
        ctx['live'] = PS.live_sessions()
        ctx['presence_counts'] = PS.state_counts(ctx['live'])

    elif tab == 'import_export':
        from models import ImportUpload, ImportJob

        jobs = (
            ImportJob.query.order_by(ImportJob.created_at.desc()).limit(100).all()
        )
        uploads = {u.id: u for u in ImportUpload.query.order_by(ImportUpload.created_at.desc()).limit(100).all()}
        ctx['import_jobs'] = jobs
        ctx['import_uploads'] = uploads
        query = AQ.tab_query('import_export', filters)
        ctx['pager'] = AQ.paginate(query, page)

    return render_template('logs/index.html', **ctx)


@bp.route('/logs/server/download/<path:filename>')
@login_required
def logs_download(filename):
    guard = _guard()
    if guard:
        return guard
    data, name = SL.download_bytes(current_app, filename)
    if data is None:
        flash('That log file does not exist any more.', 'warning')
        return redirect(url_for('system.logs_page', tab='server'))
    # No path traversal: system_logs only resolves names inside instance/logs.
    return send_file(
        io.BytesIO(data),
        mimetype='text/plain',
        as_attachment=True,
        download_name=name or 'log.txt',
    )


@bp.route('/api/activity/heartbeat', methods=['POST'])
@login_required
def activity_heartbeat():
    """Presence beat from every visible tab (see layout.html)."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        idle_sec = int(payload.get('idle_sec') or 0)
    except (TypeError, ValueError):
        idle_sec = 0
    ok = PS.record_heartbeat(
        current_user,
        path=(payload.get('path') or '')[:300] or None,
        title=(payload.get('title') or '')[:200] or None,
        visible=bool(payload.get('visible', True)),
        idle_sec=max(0, min(idle_sec, 86400 * 7)),
    )
    return jsonify({'ok': bool(ok)}), (200 if ok else 204)


@bp.route('/api/activity/state')
@login_required
def activity_state():
    """Lightweight presence snapshot for the auto-refresh on the Users tab."""
    guard = _guard()
    if guard:
        return jsonify({'error': 'unauthorized'}), 403
    rows = PS.live_sessions()
    return jsonify({
        'counts': PS.state_counts(rows),
        'generated_at': AQ.now().strftime('%H:%M:%S'),
        'users': [
            {
                'username': r['username'],
                'state': r['state'],
                'path': r['path'],
                'label': r['label'],
                'ip': r['ip'],
                'beat_age': r['beat_age'],
                'input_age': r['input_age'],
                'last_beat': r['last_beat'].strftime('%H:%M:%S') if r['last_beat'] else None,
            }
            for r in rows
        ],
    })
