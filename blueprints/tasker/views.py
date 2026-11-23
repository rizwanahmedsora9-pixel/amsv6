"""Tasker — page route (the board)."""
from __future__ import annotations

from flask import render_template
from flask_login import login_required

from ._common import is_admin, tasker_bp


@tasker_bp.route('/', strict_slashes=False)
@login_required
def board():
    """The Tasker board: total list + new-task modal + alarm banner."""
    return render_template('tasker.html', tasker_is_admin=is_admin())
