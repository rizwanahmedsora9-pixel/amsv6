"""Tasker — isolated delivery tracking board.

These four tables are a fully self-contained world.  They have **no foreign
keys** to sales, delivery, billing, stock, cash or any other AMS domain: the
client and material picked in the combo boxes are snapshotted into plain text
columns at creation time.  Tasker only ever READS the master tables (for the
type-to-search lookups) and never writes to them — and nothing outside
``tasker_*`` is ever written by the Tasker module.

Workflow:  NEW → NOTED → LOADED → DISPATCHED → DELIVERED  (+ CANCELLED).

Two admin-set deadlines per task (owner decision 2026-11-22):

* ``note_deadline_at``     — by when some user must mark it NOTED
* ``delivery_deadline_at`` — by when it must be DELIVERED ("time allowed")

Missing either turns the task red and raises the alarm until an admin resets
that deadline manually.
"""
from .__base import *  # noqa


class TaskerTask(db.Model):
    __tablename__ = 'tasker_task'

    id = db.Column(db.Integer, primary_key=True)
    # Permanent human-facing number, assigned at insert and never changed.
    task_no = db.Column(db.String(20), nullable=False, unique=True, index=True)

    # --- client snapshot (plain text; never follows master data) ----------
    # client_ref_id is an optional integer hint to the master row (read-only
    # lookups only).  It is deliberately NOT a foreign key.
    client_ref_id = db.Column(db.Integer, nullable=True)
    client_code = db.Column(db.String(50), default='', nullable=True)
    client_name = db.Column(db.String(160), nullable=False)

    # NEW -> NOTED -> LOADED -> DISPATCHED -> DELIVERED ; CANCELLED is terminal
    status = db.Column(db.String(20), default='NEW', nullable=False, index=True)
    status_before_cancel = db.Column(db.String(20), nullable=True)

    # --- deadlines (admin-set; naive Asia/Karachi like every AMS timestamp) --
    note_deadline_at = db.Column(db.DateTime, nullable=True, index=True)
    delivery_deadline_at = db.Column(db.DateTime, nullable=True, index=True)

    # --- stage stamps (username snapshots — no FK to User) -----------------
    noted_by = db.Column(db.String(80), nullable=True)
    noted_at = db.Column(db.DateTime, nullable=True)
    loaded_by = db.Column(db.String(80), nullable=True)
    loaded_at = db.Column(db.DateTime, nullable=True)
    dispatched_by = db.Column(db.String(80), nullable=True)
    dispatched_at = db.Column(db.DateTime, nullable=True)
    delivered_by = db.Column(db.String(80), nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    delivered_note = db.Column(db.String(300), default='', nullable=True)

    # --- alarm state -------------------------------------------------------
    # Computed flags (kept in sync on board reads / actions) so the global
    # alarm widget and the list can show red without recomputing history.
    note_alarm_on = db.Column(db.Boolean, default=False, nullable=False)
    delivery_alarm_on = db.Column(db.Boolean, default=False, nullable=False)
    note_overdue_since = db.Column(db.DateTime, nullable=True)
    delivery_overdue_since = db.Column(db.DateTime, nullable=True)
    deadline_reset_count = db.Column(db.Integer, default=0, nullable=False)

    task_note = db.Column(db.String(500), default='', nullable=True)

    created_by = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=pk_model_now, index=True)
    updated_at = db.Column(db.DateTime, default=pk_model_now, onupdate=pk_model_now)

    cancelled_by = db.Column(db.String(80), nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancel_reason = db.Column(db.String(300), default='', nullable=True)

    items = db.relationship(
        'TaskerItem', backref='task', lazy='select',
        order_by='TaskerItem.line_no', cascade='all, delete-orphan',
    )
    events = db.relationship(
        'TaskerEvent', backref='task', lazy='select',
        order_by='TaskerEvent.id', cascade='all, delete-orphan',
    )
    deadline_resets = db.relationship(
        'TaskerDeadlineReset', backref='task', lazy='select',
        order_by='TaskerDeadlineReset.id', cascade='all, delete-orphan',
    )

    @property
    def is_overdue(self):
        return bool(self.note_alarm_on or self.delivery_alarm_on)


class TaskerItem(db.Model):
    """One material line on a task — snapshot text, no master dependency."""

    __tablename__ = 'tasker_item'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasker_task.id'), nullable=False, index=True)
    line_no = db.Column(db.Integer, default=1, nullable=False)

    # Optional read-only hint to the Material master row (NOT a foreign key).
    material_ref_id = db.Column(db.Integer, nullable=True)
    material_code = db.Column(db.String(50), default='', nullable=True)
    material_name = db.Column(db.String(160), nullable=False)

    qty = db.Column(db.Float, default=0, nullable=False)
    unit = db.Column(db.String(20), default='', nullable=True)
    warranty = db.Column(db.String(80), default='', nullable=True)  # "wty"
    note = db.Column(db.String(300), default='', nullable=True)


class TaskerEvent(db.Model):
    """Self-contained audit trail of one task.  Never mixed into AMS logs."""

    __tablename__ = 'tasker_event'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasker_task.id'), nullable=False, index=True)
    # CREATED / NOTED / LOADED / DISPATCHED / DELIVERED / CANCELLED / REOPENED
    # / ALARM_ON / ALARM_OFF / DEADLINE_RESET / DEADLINE_SET
    event = db.Column(db.String(30), nullable=False)
    by_user = db.Column(db.String(80), nullable=True)
    at = db.Column(db.DateTime, default=pk_model_now, index=True)
    detail = db.Column(db.String(300), default='', nullable=True)


class TaskerDeadlineReset(db.Model):
    """Every admin time change — the 'admin saw it and reset the time' proof."""

    __tablename__ = 'tasker_deadline_reset'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasker_task.id'), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)  # NOTE | DELIVERY
    old_deadline = db.Column(db.DateTime, nullable=True)
    new_deadline = db.Column(db.DateTime, nullable=True)
    by_user = db.Column(db.String(80), nullable=True)
    at = db.Column(db.DateTime, default=pk_model_now)
    reason = db.Column(db.String(200), default='', nullable=True)
