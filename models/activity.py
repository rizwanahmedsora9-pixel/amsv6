"""Activity & presence storage for the redesigned Logs section.

Two new tables, deliberately separate from the legacy ``audit_log`` table
(which stays untouched, for compatibility and for the rows already written):

``activity_event``
    One row per *committed* change (create / update / delete), per login
    outcome, per import/export job, and per page-view bucket.  Written by the
    ORM capture engine in ``app/services/activity_capture.py``.

``activity_session``
    Per-browser presence: is the tab visible, when did the user last touch the
    keyboard/mouse, what page are they on.  Backs the ONLINE-WORKING / ONLINE-IDLE
    / OFFLINE states in the Users tab.

Why not reuse ``audit_log``?  Because it has no room for before/after JSON, no
request identifier, no duration, no session link and no result marker, and its
details string is a free-text 1000-char field.  Widening a table that 65 call
sites already write to, and that a live shop depends on, is riskier than adding
two new ones.
"""
from __future__ import annotations

from .__base import db, pk_model_now


class ActivityEvent(db.Model):
    """One audited thing that happened. Append-only."""

    __tablename__ = 'activity_event'

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, default=pk_model_now, index=True, nullable=False)

    # login | create | update | delete | view | import_export | error | server
    category = db.Column(db.String(20), index=True, nullable=False)
    # create/update/delete/view for CRUD rows; the outcome for logins:
    # success | failed | blocked | logout | session_end
    result = db.Column(db.String(20), default='success', index=True, nullable=False)

    module = db.Column(db.String(60), index=True)          # sales | masters | ops | accounts | system | auth
    action = db.Column(db.String(120), index=True, nullable=False)
    entity_type = db.Column(db.String(60), index=True)
    entity_id = db.Column(db.Integer, index=True)
    entity_label = db.Column(db.String(200))                # bill no / client name — what a human recognises
    amount_minor = db.Column(db.BigInteger)                 # money in minor units; never a float copy

    summary = db.Column(db.String(500))
    before_json = db.Column(db.Text)
    after_json = db.Column(db.Text)
    changed_json = db.Column(db.Text)                       # [{"field","before","after"}]

    user_id = db.Column(db.Integer, index=True)
    username = db.Column(db.String(80), index=True)
    role = db.Column(db.String(20))
    ip = db.Column(db.String(80), index=True)
    session_sid = db.Column(db.String(40), index=True)      # == user_login_session.sid
    request_id = db.Column(db.String(40), index=True)

    method = db.Column(db.String(10))
    path = db.Column(db.String(300))
    status_code = db.Column(db.Integer)
    duration_ms = db.Column(db.Integer)

    # Page-view aggregation: one row per (user, path, minute), hit count instead
    # of one row per page load.  Keeps the write rate off the SQLite lock.
    bucket_minute = db.Column(db.String(12), index=True)    # 'YYYYMMDDHHMM'
    hits = db.Column(db.Integer, default=1)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f'<ActivityEvent {self.category}:{self.action} {self.occurred_at}>'


class ActivitySession(db.Model):
    """Presence per browser tab session.

    ``activity_sid`` mirrors ``session['ams_sid']`` so this joins to
    ``user_login_session`` on a real key (the legacy accounting audit stored an
    unrelated Werkzeug id — see LOGS_REDESIGN_PLAN.md §1).
    """

    __tablename__ = 'activity_session'

    id = db.Column(db.Integer, primary_key=True)
    activity_sid = db.Column(db.String(40), unique=True, index=True, nullable=False)
    user_id = db.Column(db.Integer, index=True, nullable=False)
    username = db.Column(db.String(80), index=True)
    role = db.Column(db.String(20))

    ip = db.Column(db.String(80))
    user_agent = db.Column(db.String(300))
    current_path = db.Column(db.String(300))
    current_label = db.Column(db.String(200))               # human page name

    first_seen_at = db.Column(db.DateTime, default=pk_model_now, nullable=False)
    last_heartbeat_at = db.Column(db.DateTime, default=pk_model_now, index=True)
    last_input_at = db.Column(db.DateTime)                  # real click/key/scroll
    visible = db.Column(db.Boolean, default=True)
    ended_at = db.Column(db.DateTime, index=True)
    beat_count = db.Column(db.Integer, default=0)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f'<ActivitySession {self.username} {self.current_path}>'
