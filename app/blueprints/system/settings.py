"""settings — split from system.py."""
from ._common import *  # noqa
from urllib.parse import quote

@bp.route('/debug/db')
@login_required
def debug_db():
    if getattr(current_user, 'role', None) != 'admin':
        abort(403)
    try:
        st = os.stat(db_path) if os.path.exists(db_path) else None
        info = {
            'db_path': db_path,
            'db_exists': os.path.exists(db_path),
            'db_size_bytes': (st.st_size if st else None),
            'db_mtime': (datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S') if st else None),
            'counts': _db_debug_counts(),
        }
    except Exception as exc:
        info = {'error': str(exc)}
    return jsonify(info)


@bp.route('/settings')
@login_required
def settings():
    if current_user.role != 'admin' and not _user_can('can_access_settings'):
        flash('Unauthorized: Admin access required.', 'danger')
        return redirect(url_for('index'))
    settings_obj = Settings.query.first()
    if not settings_obj:
        settings_obj = Settings()
    categories = MaterialCategory.query.order_by(MaterialCategory.name.asc()).all()
    recon_report = session.pop('recon_report', None)
    recent_audit = []
    try:
        recent_audit = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(20).all()
    except Exception:
        recent_audit = []
    # Single source of truth for the user-role UI: feature groups + the
    # permission value of every user, so the checkboxes and the access
    # matrix always show exactly what exists and who has it.
    from app.services.constants import (
        EDITABLE_USER_PERMISSION_FIELDS,
        PERMISSION_GROUPS,
        USER_PERMISSION_DEFAULTS,
    )
    users_all = User.query.all()
    user_perm_values = {
        u.id: {field: bool(getattr(u, field, None)) for field in EDITABLE_USER_PERMISSION_FIELDS}
        for u in users_all
    }
    default_permissions = {
        field: bool(USER_PERMISSION_DEFAULTS.get(field, False))
        for field in EDITABLE_USER_PERMISSION_FIELDS
    }
    return render_template(
        'settings.html',
        users=users_all,
        settings=settings_obj,
        categories=categories,
        recon_report=recon_report,
        recent_audit=recent_audit,
        permission_groups=PERMISSION_GROUPS,
        permission_fields=EDITABLE_USER_PERMISSION_FIELDS,
        user_perm_values=user_perm_values,
        default_permissions=default_permissions,
    )


@bp.route('/settings/activity')
@login_required
def activity_log_page():
    """Legacy URL — the redesigned Logs section replaces this page.

    Kept as a redirect (not deleted) because the old link is printed in
    Settings, stored in browser bookmarks on the yard PCs, and referenced in
    older release notes. ``?q=`` is carried over as the new free-text filter.
    """
    if current_user.role != 'admin' and not _user_can('can_access_settings'):
        flash('Unauthorized: Admin access required.', 'danger')
        return redirect(url_for('index'))
    term = (request.args.get('q') or '').strip()
    target = url_for('system.logs_page', tab='added')
    if term:
        target = f'{target}?q={quote(term)}&range=all'
    return redirect(target)


@bp.route('/settings/sessions')
@login_required
def login_sessions_page():
    """Legacy URL — now the Logins tab of the Logs section."""
    if current_user.role != 'admin' and not _user_can('can_access_settings'):
        flash('Unauthorized: Admin access required.', 'danger')
        return redirect(url_for('index'))
    return redirect(url_for('system.logs_page', tab='users'))
