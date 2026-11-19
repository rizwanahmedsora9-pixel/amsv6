"""Presence: who is online, who is actually working, who has walked away.

Why this is not "seen in the last 45 minutes"
---------------------------------------------
The old ``Live logins`` page called a session live if its heartbeat was under
45 minutes old, and the heartbeat itself only fired every 5 minutes
(``utils/sessions.TOUCH_INTERVAL_SECONDS = 300``).  The most it could honestly
say was "seen recently".  It could not tell a man at the counter from a PC left
on a page since lunch.

Now the browser sends a small beat every 60 s **while the tab is visible**, and
reports how long since the user last actually touched the keyboard or mouse:

ONLINE — WORKING   beat < 90 s ago  and  real input < 3 min ago
ONLINE — IDLE      beat < 90 s ago  but  no input for 3 min or more
OFFLINE            no beat for > 90 s, or the session was closed / logged out
AWAY (hidden tab)   beat present but the tab is in the background

Ages are always computed from ``last_heartbeat_at``; nothing is inferred from
page views, which can be minutes stale.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from flask import current_app, has_request_context, request, session as flask_session
from sqlalchemy.orm import Session as SASession

from models import db
from models.__base import pk_model_now

LOG = logging.getLogger('activity')

WORKING_INPUT_SECONDS = 180      # no real input for 3 min => idle
ONLINE_HEARTBEAT_SECONDS = 90    # no beat for 90 s => offline
STALE_SESSION_MINUTES = 45       # session row with no beat for 45 min => expired

STATE_WORKING = 'working'
STATE_IDLE = 'idle'
STATE_OFFLINE = 'offline'
STATE_AWAY = 'away'
STATE_EXPIRED = 'expired'


def _now():
    return pk_model_now()


def _client_ip():
    if not has_request_context():
        return None
    forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return forwarded or request.remote_addr


def _ua():
    if not has_request_context():
        return None
    return (request.headers.get('User-Agent') or '')[:300]


def record_heartbeat(user, *, path=None, title=None, visible=True, idle_sec=0):
    """Upsert this browser session's presence row. Best effort, never raises."""
    sid = None
    try:
        sid = flask_session.get('ams_sid')
    except Exception:
        sid = None
    if not sid:
        # A logged-in user without a session row (cookie restored, DB reset):
        # create one so presence is still correct instead of silently missing.
        try:
            from utils.sessions import open_login_session
            sid = open_login_session(user)
        except Exception:
            sid = None
    if not sid:
        return False

    now = _now()
    last_input = now - timedelta(seconds=max(0, int(idle_sec or 0)))
    bind = db.session.get_bind()
    try:
        with SASession(bind) as s:
            if bind.dialect.name == 'sqlite':
                from sqlalchemy import text as _text
                s.execute(_text('PRAGMA busy_timeout = 250'))
            from models import ActivitySession

            row = s.query(ActivitySession).filter_by(activity_sid=sid).first()
            if row is None:
                row = ActivitySession(
                    activity_sid=sid,
                    user_id=getattr(user, 'id', None),
                    username=getattr(user, 'username', None),
                    role=getattr(user, 'role', None),
                    first_seen_at=now,
                    beat_count=0,
                )
                s.add(row)
            row.username = getattr(user, 'username', None) or row.username
            row.role = getattr(user, 'role', None) or row.role
            row.ip = _client_ip() or row.ip
            row.user_agent = _ua() or row.user_agent
            if path:
                row.current_path = str(path)[:300]
            if title:
                row.current_label = str(title)[:200]
            row.last_heartbeat_at = now
            row.last_input_at = last_input
            row.visible = bool(visible)
            row.ended_at = None
            row.beat_count = int(row.beat_count or 0) + 1
            s.commit()
    except Exception:
        LOG.debug('presence: heartbeat write skipped', exc_info=True)
        return False

    # Mirror the presence onto the login-session row so the older Live logins
    # page and any external report show the same truth.
    _mirror_to_login_session(sid, now, last_input, visible)
    return True


def _mirror_to_login_session(sid, now, last_input, visible):
    bind = db.session.get_bind()
    sql = (
        'UPDATE user_login_session SET last_active_at = ?, activity_state = ?, '
        'last_seen_at = CASE WHEN last_seen_at IS NULL OR last_seen_at < ? THEN ? ELSE last_seen_at END '
        'WHERE sid = ? AND ended_at IS NULL'
    )
    state = classify(now, now if visible else None, last_input, visible)[0]
    try:
        with SASession(bind) as s:
            if bind.dialect.name == 'sqlite':
                from sqlalchemy import text as _text
                s.execute(_text('PRAGMA busy_timeout = 250'))
            s.execute(db.text(sql), (now, state, now, now, sid))
            s.commit()
    except Exception:
        LOG.debug('presence: login-session mirror skipped', exc_info=True)


def classify(last_beat, last_beat_ignored=None, last_input=None, visible=True, ended_at=None):
    """Return (state, age_seconds) for one presence row."""
    now = _now()
    if ended_at:
        return (STATE_OFFLINE, _age(now, ended_at))
    if not last_beat:
        return (STATE_OFFLINE, None)
    beat_age = _age(now, last_beat)
    if beat_age is None or beat_age > ONLINE_HEARTBEAT_SECONDS:
        return (STATE_OFFLINE, beat_age)
    if visible is False:
        return (STATE_AWAY, beat_age)
    input_age = _age(now, last_input) if last_input else None
    if input_age is None or input_age > WORKING_INPUT_SECONDS:
        return (STATE_IDLE, beat_age)
    return (STATE_WORKING, beat_age)


def _age(now, then):
    try:
        return max(0, int((now - then).total_seconds()))
    except Exception:
        return None


def close_presence(sid):
    """Mark the session closed on logout."""
    if not sid:
        return
    bind = db.session.get_bind()
    try:
        with SASession(bind) as s:
            s.execute(
                db.text('UPDATE activity_session SET ended_at = ?, visible = 0 WHERE activity_sid = ?'),
                (_now(), sid),
            )
            s.commit()
    except Exception:
        LOG.debug('presence: close skipped', exc_info=True)


def live_sessions():
    """Every presence row that is not closed, newest beat first, with a status."""
    from models import ActivitySession

    rows = (
        ActivitySession.query.filter(ActivitySession.ended_at.is_(None))
        .order_by(ActivitySession.last_heartbeat_at.desc())
        .all()
    )
    out = []
    for row in rows:
        state, beat_age = classify(
            row.last_heartbeat_at, None, row.last_input_at, bool(row.visible), row.ended_at
        )
        input_age = _age(_now(), row.last_input_at) if row.last_input_at else None
        if state == STATE_OFFLINE and beat_age is not None and beat_age > STALE_SESSION_MINUTES * 60:
            state = STATE_EXPIRED
        out.append({
            'row': row,
            'state': state,
            'beat_age': beat_age,
            'input_age': input_age,
            'username': row.username or '—',
            'role': row.role or '',
            'ip': row.ip or '',
            'path': row.current_path or '',
            'label': row.current_label or '',
            'since': row.first_seen_at,
            'last_beat': row.last_heartbeat_at,
        })
    return out


def state_counts(rows=None):
    rows = live_sessions() if rows is None else rows
    counts = {STATE_WORKING: 0, STATE_IDLE: 0, STATE_AWAY: 0, STATE_OFFLINE: 0, STATE_EXPIRED: 0}
    for row in rows:
        counts[row['state']] = counts.get(row['state'], 0) + 1
    return counts


def user_presence_summary():
    """One entry per registered user, with the best current state and session count.

    Used by the Users tab so *every* registered account appears — including
    suspended ones and accounts that have never signed in — instead of only the
    ones with an open session.
    """
    from models import User

    rows = live_sessions()
    by_user = {}
    for row in rows:
        key = (row['username'] or '').strip().lower()
        by_user.setdefault(key, []).append(row)

    rank = {STATE_WORKING: 0, STATE_IDLE: 1, STATE_AWAY: 2, STATE_OFFLINE: 3, STATE_EXPIRED: 4}
    summary = []
    for user in User.query.order_by(User.username.asc()).all():
        key = (user.username or '').strip().lower()
        sessions = by_user.get(key, [])
        best = None
        for entry in sessions:
            if best is None or rank.get(entry['state'], 9) < rank.get(best['state'], 9):
                best = entry
        summary.append({
            'user': user,
            'username': user.username,
            'role': user.role or '',
            'account_status': (user.status or 'active'),
            'access_mode': (user.access_mode or 'read_write'),
            'sessions': sessions,
            'session_count': len(sessions),
            'state': best['state'] if best else STATE_OFFLINE,
            'current_page': (best['label'] or best['path']) if best else None,
            'ip': best['ip'] if best else None,
            'last_beat': best['last_beat'] if best else None,
            'last_input_age': best['input_age'] if best else None,
            'never_seen': best is None,
            'created_at': user.created_at,
        })
    return summary
