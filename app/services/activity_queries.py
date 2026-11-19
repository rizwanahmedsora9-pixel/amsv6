"""Queries behind the Logs section — one filter vocabulary for every tab.

Design rules
------------
* Every tab reads real rows. Nothing is synthesised, and an empty result says
  *why* it is empty (no rows yet / capture started later / filtered out).
* Filters are shared: date range, user, role, module, result, IP, free text,
  amount range.  The same query string works on every tab, so a filter survives
  tab switching (the tabs are links, not a JS router).
* Paging is keyset-free (OFFSET) but capped, because the UI never needs page
  400 and a huge OFFSET on SQLite is a scan.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, or_, and_

from models import db, ActivityEvent
from models.__base import pk_model_now

PAGE_SIZE = 50
MAX_PAGE = 400

CATEGORY_TABS = {
    'logins': 'login',
    'added': 'create',
    'edited': 'update',
    'deleted': 'delete',
    'views': 'view',
    'import_export': 'import_export',
    'errors': 'error',
}

#: The 'All logs' tab: one stream of every category.  Not a category itself, so
#: it is kept out of ``CATEGORY_TABS`` and handled by :func:`all_query`.
ALL_TAB = 'all'

#: Categories shown on the All tab, in the order they appear in the filter.
ALL_CATEGORIES = (
    'login', 'create', 'update', 'delete', 'view', 'import_export', 'error',
)

RANGE_CHOICES = (
    ('today', 'Today'),
    ('yesterday', 'Yesterday'),
    ('7d', 'Last 7 days'),
    ('30d', 'Last 30 days'),
    ('90d', 'Last 90 days'),
    ('all', 'All time'),
)


def now():
    return pk_model_now()


def resolve_range(value):
    """(start, end, label) for a range key. Business day = Pakistan time."""
    value = (value or '7d').strip()
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    if value == 'today':
        return today, None, 'Today'
    if value == 'yesterday':
        return today - timedelta(days=1), today, 'Yesterday'
    if value == '7d':
        return today - timedelta(days=6), None, 'Last 7 days'
    if value == '30d':
        return today - timedelta(days=29), None, 'Last 30 days'
    if value == '90d':
        return today - timedelta(days=89), None, 'Last 90 days'
    if value == 'custom':
        return None, None, 'Custom range'
    return None, None, 'All time'


def parse_date(raw, end_of_day=False):
    raw = (raw or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y'):
        try:
            parsed = datetime.strptime(raw, fmt)
            if end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            return parsed
        except ValueError:
            continue
    return None


def parse_amount(raw):
    """'1200' or '1,200.50' -> minor units int."""
    raw = (raw or '').strip().replace(',', '')
    if not raw:
        return None
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return None


class Filters:
    """The shared filter set, parsed once from the query string."""

    FIELDS = ('range', 'from', 'to', 'user', 'role', 'module', 'result', 'ip',
              'q', 'amount_min', 'amount_max', 'entity', 'category', 'sort')

    def __init__(self, args):
        get = args.get
        self.range = (get('range') or '7d').strip()
        self.date_from_raw = (get('from') or '').strip()
        self.date_to_raw = (get('to') or '').strip()
        self.user = (get('user') or '').strip()
        self.role = (get('role') or '').strip()
        self.module = (get('module') or '').strip()
        self.result = (get('result') or '').strip()
        self.ip = (get('ip') or '').strip()
        self.q = (get('q') or '').strip()
        self.amount_min = parse_amount(get('amount_min'))
        self.amount_max = parse_amount(get('amount_max'))
        self.entity = (get('entity') or '').strip()
        # Only the 'All logs' tab uses this (a category tab is already narrowed
        # to one category); it stays a plain attribute so the other tabs ignore
        # it instead of returning an empty list.
        self.category = (get('category') or '').strip()
        self.sort = (get('sort') or 'newest').strip()
        self.start, self.end, self.range_label = resolve_range(self.range)
        custom_from = parse_date(self.date_from_raw)
        custom_to = parse_date(self.date_to_raw, end_of_day=True)
        if custom_from or custom_to:
            self.range = 'custom'
            self.start = custom_from
            self.end = custom_to
            self.range_label = 'Custom range'

    # -- URL ---------------------------------------------------------------
    def as_query(self, include_category=True, **overrides):
        """Query string for links (tabs, paging) that keeps the filters.

        ``include_category`` is False for the category tabs: a category chosen
        on the 'All logs' tab must not follow the user to 'Entries added' and
        silently narrow it.
        """
        pairs = {
            'range': self.range if self.range != 'custom' else 'custom',
            'from': self.date_from_raw,
            'to': self.date_to_raw,
            'user': self.user,
            'role': self.role,
            'module': self.module,
            'result': self.result,
            'ip': self.ip,
            'q': self.q,
            'amount_min': '' if self.amount_min is None else f'{self.amount_min / 100:.2f}',
            'amount_max': '' if self.amount_max is None else f'{self.amount_max / 100:.2f}',
            'entity': self.entity,
            'category': self.category if include_category else '',
            'sort': self.sort,
        }
        pairs.update({k: v for k, v in overrides.items() if k in pairs})
        from urllib.parse import urlencode
        return urlencode({k: v for k, v in pairs.items() if v not in (None, '')})

    def is_active(self):
        return any((self.user, self.role, self.module, self.result, self.ip,
                    self.q, self.amount_min is not None, self.amount_max is not None,
                    self.entity, self.category, self.range == 'custom'))

    def describe(self):
        parts = [self.range_label]
        if self.category:
            parts.append(f'category={self.category}')
        if self.user:
            parts.append(f'user={self.user}')
        if self.role:
            parts.append(f'role={self.role}')
        if self.module:
            parts.append(f'module={self.module}')
        if self.result:
            parts.append(f'result={self.result}')
        if self.ip:
            parts.append(f'ip={self.ip}')
        if self.q:
            parts.append(f'“{self.q}”')
        if self.amount_min is not None or self.amount_max is not None:
            lo = '—' if self.amount_min is None else f'{self.amount_min / 100:,.0f}'
            hi = '—' if self.amount_max is None else f'{self.amount_max / 100:,.0f}'
            parts.append(f'amount {lo}…{hi}')
        return ' · '.join(parts)

    def apply(self, query, *, include_dates=True):
        if include_dates:
            if self.start:
                query = query.filter(ActivityEvent.occurred_at >= self.start)
            if self.end:
                query = query.filter(ActivityEvent.occurred_at <= self.end)
        if self.user:
            query = query.filter(ActivityEvent.username == self.user)
        if self.role:
            query = query.filter(ActivityEvent.role == self.role)
        if self.module:
            query = query.filter(ActivityEvent.module == self.module)
        if self.result:
            query = query.filter(ActivityEvent.result == self.result)
        if self.ip:
            query = query.filter(ActivityEvent.ip.ilike(f'%{self.ip}%'))
        if self.entity:
            query = query.filter(
                or_(
                    ActivityEvent.entity_type == self.entity,
                    ActivityEvent.entity_label.ilike(f'%{self.entity}%'),
                )
            )
        if self.amount_min is not None:
            query = query.filter(ActivityEvent.amount_minor >= self.amount_min)
        if self.amount_max is not None:
            query = query.filter(ActivityEvent.amount_minor <= self.amount_max)
        if self.q:
            like = f'%{self.q}%'
            query = query.filter(
                or_(
                    ActivityEvent.summary.ilike(like),
                    ActivityEvent.action.ilike(like),
                    ActivityEvent.entity_label.ilike(like),
                    ActivityEvent.username.ilike(like),
                    ActivityEvent.path.ilike(like),
                    ActivityEvent.ip.ilike(like),
                    ActivityEvent.request_id.ilike(like),
                    ActivityEvent.changed_json.ilike(like),
                )
            )
        return query


def paginate(query, page, per_page=PAGE_SIZE):
    page = max(1, min(int(page or 1), MAX_PAGE))
    total = query.order_by(None).count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)
    return {
        'rows': rows,
        'page': page,
        'pages': pages,
        'total': total,
        'has_prev': page > 1,
        'has_next': page < pages,
        'per_page': per_page,
    }


def _sorted(query, filters):
    """Apply the shared sort choice (newest / oldest / largest amount)."""
    if filters.sort == 'oldest':
        return query.order_by(ActivityEvent.occurred_at.asc(), ActivityEvent.id.asc())
    if filters.sort == 'amount':
        return query.order_by(ActivityEvent.amount_minor.desc().nullslast(), ActivityEvent.occurred_at.desc())
    return query.order_by(ActivityEvent.occurred_at.desc(), ActivityEvent.id.desc())


def tab_query(category, filters, *, include_dates=True):
    query = ActivityEvent.query.filter(ActivityEvent.category == category)
    query = filters.apply(query, include_dates=include_dates)
    return _sorted(query, filters)


def all_query(filters, *, include_dates=True):
    """The 'All logs' stream: every category, one list.

    Same filters as every other tab, plus the tab's own optional category
    filter (``?category=error``), so the proprietor can see everything and then
    narrow it without switching tabs.
    """
    query = ActivityEvent.query
    if filters.category:
        query = query.filter(ActivityEvent.category == filters.category)
    query = filters.apply(query, include_dates=include_dates)
    return _sorted(query, filters)


def tab_counts(filters):
    """Row counts per tab, so the tab labels carry real numbers."""
    counts = {}
    for key, category in CATEGORY_TABS.items():
        query = ActivityEvent.query.filter(ActivityEvent.category == category)
        query = filters.apply(query)
        try:
            counts[key] = query.count()
        except Exception:
            counts[key] = None
    # The All tab is the whole stream, so it is counted on its own (it must not
    # inherit the All tab's category filter in the other tabs' numbers).
    try:
        all_count = ActivityEvent.query
        if filters.category:
            all_count = all_count.filter(ActivityEvent.category == filters.category)
        counts[ALL_TAB] = filters.apply(all_count).count()
    except Exception:
        counts[ALL_TAB] = None
    return counts


def distinct_values(column, limit=400):
    try:
        rows = (
            db.session.query(column)
            .filter(column.isnot(None), column != '')
            .distinct()
            .order_by(column.asc())
            .limit(limit)
            .all()
        )
        return [row[0] for row in rows if row[0]]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Tab-specific helpers
# ---------------------------------------------------------------------------
def decode_changes(row):
    """The field-level diff for one row, ready for the template."""
    if not row.changed_json:
        return []
    try:
        data = json.loads(row.changed_json)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return data


def pretty_json(raw, limit=4000):
    if not raw:
        return ''
    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)[:limit]
    except ValueError:
        return str(raw)[:limit]


def human_age(when, reference=None):
    if not when:
        return ''
    reference = reference or now()
    seconds = int((reference - when).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f'{seconds}s ago'
    if seconds < 3600:
        return f'{seconds // 60}m ago'
    if seconds < 86400:
        return f'{seconds // 3600}h {(seconds % 3600) // 60}m ago'
    return f'{seconds // 86400}d ago'


def coverage():
    """What this log can and cannot show. Displayed on every tab.

    The honesty banner: activity capture starts at the first row, and anything
    that happened before that exists only in the legacy tables.  Saying this
    out loud is better than a page that looks complete but isn't.
    """
    first = None
    last = None
    total = 0
    try:
        first, last, total = db.session.query(
            func.min(ActivityEvent.occurred_at),
            func.max(ActivityEvent.occurred_at),
            func.count(ActivityEvent.id),
        ).one()
    except Exception:
        db.session.rollback()
    return {
        'first_event_at': first,
        'last_event_at': last,
        'total_events': int(total or 0),
        'captures_since': first,
        'has_data': bool(total),
    }


def legacy_counts():
    """Rows still only in the legacy stores (shown in the banner)."""
    out = {'audit_log': 0, 'accounting_audit_log': 0}
    from sqlalchemy import text
    for table in ('audit_log', 'accounting_audit_log'):
        try:
            out[table] = int(db.session.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar() or 0)
        except Exception:
            db.session.rollback()
            out[table] = 0
    return out


def top_pages(filters, limit=12):
    query = tab_query('view', filters)
    rows = (
        query.with_entities(
            ActivityEvent.path,
            ActivityEvent.entity_label,
            func.sum(func.coalesce(ActivityEvent.hits, 1)).label('hits'),
            func.count(ActivityEvent.id).label('buckets'),
        )
        .group_by(ActivityEvent.path, ActivityEvent.entity_label)
        .order_by(func.sum(func.coalesce(ActivityEvent.hits, 1)).desc())
        .limit(limit)
        .all()
    )
    return [
        {'path': r[0], 'label': r[1], 'hits': int(r[2] or 0), 'buckets': int(r[3] or 0)}
        for r in rows
    ]


def per_day(filters, category=None, days=14):
    start = now() - timedelta(days=days)
    query = ActivityEvent.query.filter(ActivityEvent.occurred_at >= start)
    if category:
        query = query.filter(ActivityEvent.category == category)
    query = filters.apply(query, include_dates=False)
    rows = (
        query.with_entities(
            func.date(ActivityEvent.occurred_at).label('day'),
            func.count(ActivityEvent.id).label('count'),
        )
        .group_by(func.date(ActivityEvent.occurred_at))
        .order_by(func.date(ActivityEvent.occurred_at).asc())
        .all()
    )
    return [{'day': r[0], 'count': int(r[1] or 0)} for r in rows]
