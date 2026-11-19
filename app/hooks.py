"""Legacy request hooks. Bound in create_app via register_hooks()."""
from __future__ import annotations
import os, time, logging, secrets
from flask import request
from werkzeug.exceptions import RequestEntityTooLarge

# Internal-detail signatures that must never reach the user.  Flask messages
# are sanitised centrally (see ``_stamp_actor_on_crud``) so a single legacy
# route cannot leak SQL, parameter bindings or driver URLs into the UI.
_ERROR_LEAK_SIGNATURES = (
    "sqlite3.integrityerror",
    "sqlalchemy.exc",
    "sqlalchemy",
    "integrityerror",
    "operationalerror",
    "programmingerror",
    "statementerror",
    " [sql: ",
    "sqlalche.me",
    "unique constraint failed",
    "foreign key constraint failed",
    "no such table",
    "(background on this error",
    "traceback (most recent call last)",
    " [parameters:",
    "database is locked",
    "not null constraint failed",
    "check constraint failed",
)


def _safe_flash_text(text):
    """Replace an exception/SQL leak with a clean, actionable message."""
    raw = str(text or "")
    lowered = raw.lower()
    if any(sig in lowered for sig in _ERROR_LEAK_SIGNATURES):
        return (
            "The operation could not be completed due to a data error. "
            "No changes were saved — please retry, and contact support if "
            "the problem persists."
        )
    return raw


#: Modules a permission-only account can be pointed at, in the order the
#: no-access page offers them.  (permission, label, endpoint)
_ACCESS_LINKS = (
    ('can_view_dashboard', 'Dashboard', 'index'),
    ('can_manage_sales', 'Sales & Billing', 'direct_sales'),
    ('can_manage_bookings', 'Bookings', 'bookings'),
    ('can_manage_clients', 'Clients', 'clients'),
    ('can_manage_suppliers', 'Suppliers', 'suppliers'),
    ('can_manage_purchases', 'Purchases (GRN)', 'grn'),
    ('can_view_stock', 'Stock', 'inventory.stock_summary'),
    ('can_manage_pending_bills', 'Unpaid Bills', 'unpaid_transactions_page'),
    ('can_manage_returns', 'Material Returns', 'material_returns_page'),
    ('can_view_reports', 'Reports', 'profit_reports'),
    ('can_view_accounts', 'Accounts', 'accounts.index'),
    ('can_access_settings', 'Settings', 'settings'),
)


def _no_access_response(needed):
    """Terminal "this account has no access yet" page (breaks the redirect loop).

    A user whose permissions have not been granted yet used to bounce between
    ``/`` and ``/`` for ever.  This page says what is missing and links the
    modules the account *can* open, so the owner can either tick a permission or
    sign out — and the browser can never loop.
    """
    from flask import render_template_string, url_for as _url_for
    from flask_login import current_user
    from app.services.permissions import _user_can

    links = []
    for permission, label, endpoint in _ACCESS_LINKS:
        try:
            if not _user_can(permission):
                continue
            links.append({'label': label, 'url': _url_for(endpoint)})
        except Exception:
            continue

    return render_template_string(
        """
        <!doctype html><html lang="en"><head><meta charset="utf-8">
        <title>No access yet</title>
        <style>
          body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f6f7fb;
               color:#1f2933;margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
          .card{background:#fff;border:1px solid #e3e6ef;border-radius:14px;max-width:640px;
                padding:32px 36px;box-shadow:0 10px 30px rgba(15,23,42,.06)}
          h1{font-size:22px;margin:0 0 8px}
          p{margin:0 0 12px;line-height:1.55;color:#475467}
          code{background:#f1f3f9;padding:2px 6px;border-radius:6px;font-size:13px}
          .links{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 6px}
          .links a{background:#1f4e78;color:#fff;text-decoration:none;padding:9px 14px;border-radius:9px;font-size:14px}
          .out{display:inline-block;margin-top:18px;color:#b42318;text-decoration:none;font-weight:600}
        </style></head><body><div class="card">
        <h1>This account has no access yet</h1>
        <p>You are signed in as <strong>{{ user }}</strong>, but the permission this page needs
        (<code>{{ needed }}</code>) has not been granted. Nothing is broken and no data was changed.</p>
        {% if links %}
        <p>You can still open:</p>
        <div class="links">{% for l in links %}<a href="{{ l.url }}">{{ l.label }}</a>{% endfor %}</div>
        {% else %}
        <p>Ask the administrator (admin &rarr; Settings &rarr; Users &amp; permissions) to tick the boxes
        for this account. Until then every screen is closed.</p>
        {% endif %}
        <a class="out" href="{{ logout_url }}">Sign out</a>
        </div></body></html>
        """,
        user=getattr(current_user, 'username', '') or 'this account',
        needed=needed if isinstance(needed, str) else ', '.join(sorted(needed)),
        links=links,
        logout_url=_url_for('logout'),
    ), 403


# Endpoints any authenticated account may use regardless of permissions:
# switching one's own UI theme, and the presence heartbeat (which only writes
# the caller's own row).  Keep this list tiny — it is the allow-list that
# bypasses the permission map.
_PERMISSION_EXEMPT_ENDPOINTS = frozenset({
    'ui_theme_preference_api', 'api_ui_theme', 'ui_theme',
    'activity_heartbeat', 'system.activity_heartbeat',
})

def register_hooks(app):
    from flask import jsonify, flash, redirect, url_for, session
    from flask_login import current_user
    from models import Client, Material, Service, DeliveryPerson, Settings
    from app.services.backup import _start_hourly_backup_worker, _start_reconcile_worker
    from app.services.terms import get_active_terms
    from app.services.permissions import _user_can
    from app.services.constants import (
        _AUTO_BACKUP_ENABLED,
        _ENABLE_BACKGROUND_WORKERS,
        BLUEPRINT_PERMISSION_PREFIXES,
        ENDPOINT_PERMISSION_MAP,
    )
    @app.after_request
    def _cache_control_headers(response):
        # On-demand edit/view modal fragments must never be served stale —
        # a stale fragment is the classic "click edit -> stuck overlay".
        endpoint = request.endpoint or ''
        short = endpoint.rsplit('.', 1)[-1] if endpoint else ''
        if short in ('public_client_ledger', 'client_ledger_qr', 'client_ledger_qr_dialog'):
            response.headers['Cache-Control'] = 'private, no-store, max-age=0'
            return response
        if short in (
            'direct_sale_edit_modal',
            'booking_edit_modal',
            'pending_bill_modals',
            'client_modals',
        ):
            response.headers['Cache-Control'] = 'no-store'
            return response
        # HTML pages: revalidate every time.  Yard PCs share this server over
        # LAN and code updates deploy often; without this, a cached page can
        # run OLD page scripts against NEW routes and hang (edit modal stuck
        # forever).  Static assets keep their own caching rules.
        if (response.headers.get('Content-Type') or '').startswith('text/html'):
            response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.after_request
    def allow_iframe_and_cors(response):
        # Views that emit their own framing policy (e.g. the public QR ledger
        # page: X-Frame-Options DENY + strict CSP) keep it. Everything else
        # is same-origin framed unless the operator explicitly opts back in
        # (e.g. embedding in a cross-origin dashboard, or the e2b/Arena
        # preview) via AMS_ALLOW_IFRAME=1.
        allow_open = os.environ.get('ALLOW_OPEN_CORS', '').lower() in ('1', 'true', 'yes')
        allow_embed = (os.environ.get('AMS_ALLOW_IFRAME') or '').strip().lower() in ('1', 'true', 'yes')
        view_set_frame = bool(response.headers.get('X-Frame-Options')) or 'frame-ancestors' in (
            response.headers.get('Content-Security-Policy') or ''
        )

        if allow_open:
            response.headers["X-Frame-Options"] = "ALLOWALL"
            response.headers["Content-Security-Policy"] = "frame-ancestors *"
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
            response.headers["Access-Control-Allow-Credentials"] = "false"
        elif allow_embed:
            response.headers["X-Frame-Options"] = "ALLOWALL"
            response.headers["Content-Security-Policy"] = "frame-ancestors *"
        elif not view_set_frame:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if (request.headers.get('X-Forwarded-Proto') or request.scheme or '').split(',')[0].strip().lower() == 'https' or request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.before_request
    def _request_log_start():
        request._log_started_at = time.time()
        from flask import g as _g
        _g.ams_request_started_at = request._log_started_at

    @app.after_request
    def _stamp_response_status(response):
        """Record the status so activity rows written later can carry it."""
        from flask import g as _g
        try:
            _g.ams_response_status = int(response.status_code or 0)
        except Exception:
            pass
        return response

    @app.after_request
    def _capture_page_view(response):
        """Log an (aggregated) page view for authenticated HTML GETs.

        Buckets are one row per (user, path, minute) with a hit count, and the
        client-side write is throttled in ``activity_capture.log_page_view``:
        a row per page load would make this the busiest writer in a database
        that already suffers from SQLite write-lock contention.
        """
        try:
            if (request.method or '').upper() != 'GET':
                return response
            if not (response.headers.get('Content-Type') or '').startswith('text/html'):
                return response
            if not (current_user and current_user.is_authenticated):
                return response
            endpoint = request.endpoint or ''
            if endpoint in ('login', 'logout', 'static') or endpoint.startswith('static.'):
                return response
            path = request.path or ''
            if path.startswith('/static') or path.startswith('/api/') or path.startswith('/c/'):
                return response
            # Only real navigations: an XHR fragment fetch is not a page view.
            if (request.headers.get('X-Requested-With') or '').lower() == 'xmlhttprequest':
                return response
            from app.services.activity_capture import log_page_view
            log_page_view(
                path=path[:300],
                label=(request.endpoint or path)[:200],
                user_id=getattr(current_user, 'id', None),
                username=(getattr(current_user, 'username', None) or '')[:80] or None,
            )
        except Exception:
            pass
        return response

    # Activity capture: ORM before_flush / after_commit listeners that record a
    # real field-level diff for every audited model.  This is what makes
    # "entries added / edited / deleted" possible for sales and ops, which had
    # no audit calls at all.  A failed save writes nothing (see
    # app/services/activity_capture.py).
    try:
        from app.services.activity_capture import install_listeners
        install_listeners()
    except Exception:
        logging.getLogger('app').exception('Activity capture listeners could not be installed')

    @app.before_request
    def _touch_concurrent_login_session():
        try:
            if current_user.is_authenticated:
                from utils.sessions import touch_login_session
                touch_login_session(current_user)
        except Exception:
            pass

    @app.after_request
    def _stamp_actor_on_crud(response):
        """Every mutating request: flash who did it + write audit_log."""
        try:
            method = (request.method or '').upper()
            if method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
                return response
            path = request.path or ''
            if path.startswith('/static') or path.startswith('/import_export/jobs/'):
                return response
            endpoint = request.endpoint or ''
            if endpoint in ('login', 'logout', 'static'):
                return response
            who = None
            try:
                if current_user and current_user.is_authenticated:
                    who = (current_user.username or '').strip() or None
            except Exception:
                who = None
            if not who:
                return response
            flashes = session.get('_flashes')
            if flashes:
                tagged = []
                marker = f'— by {who}'
                for cat, msg in flashes:
                    text = _safe_flash_text(msg)
                    if cat in ('success', 'warning', 'info', 'danger') and marker not in text:
                        text = f'{text} {marker}'
                    tagged.append((cat, text))
                session['_flashes'] = tagged
            if endpoint in ('logout', 'auth.logout'):
                try:
                    from app.services.presence import close_presence
                    close_presence(session.pop('ams_sid', None))
                    from app.services.activity_capture import log_event
                    log_event(
                        'login', 'logout', result='logout', module='auth',
                        summary=f'{who} signed out',
                    )
                except Exception:
                    pass
            if 200 <= int(response.status_code or 0) < 400:
                from utils.audit import audit_log
                audit_log(
                    current_user,
                    f'http.{method.lower()}.{endpoint or "unknown"}',
                    f'path={path} status={response.status_code}',
                )
        except Exception:
            pass
        return response

    @app.after_request
    def _log_request_summary(response):
        started = getattr(request, '_log_started_at', None)
        elapsed_ms = ((time.time() - started) * 1000.0) if started else 0.0
        # QR links are bearer credentials. Never write a client's raw token
        # into application request logs.
        logged_path = '/c/[client-ledger-token]' if request.path.startswith('/c/') else (
            request.full_path.rstrip('?') if request.query_string else request.path
        )
        logging.getLogger('request').info(
            '%s - "%s %s" %s %.1fms',
            request.headers.get('X-Forwarded-For', request.remote_addr or '-'),
            request.method,
            logged_path,
            response.status_code,
            elapsed_ms
        )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def _handle_request_too_large(_err):
        max_mb = max(1, int((app.config.get('MAX_CONTENT_LENGTH') or 0) / (1024 * 1024)))
        msg = f"Uploaded file is too large. Max allowed size is {max_mb} MB."
        if request.path.startswith('/import_export/master/import/start'):
            return jsonify({'error': msg}), 413
        flash(msg, 'danger')
        return redirect(request.referrer or url_for('index'))

    @app.before_request
    def _protect_against_csrf():
        """Session-bound CSRF protection for every mutating endpoint.

        The layout template attaches the session token to every mutating
        form (and patches fetch/XHR to send the X-CSRF-Token header), so
        the gate is transparent to the UI.  Enforcement is skipped only in
        explicit test-mode opt-outs; the ``login`` endpoint is protected as
        well (its template carries the token).
        """
        token = session.get('_csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['_csrf_token'] = token
        if app.config.get('TESTING') or app.config.get('WTF_CSRF_ENABLED') is False:
            if not app.config.get('AMS_CSRF_ALWAYS'):
                return None
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        endpoint = request.endpoint or ''
        if endpoint in ('static',) or endpoint.startswith('static.'):
            return None
        supplied = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token') or request.headers.get('X-CSRFToken')
        if supplied and secrets.compare_digest(str(supplied), str(token)):
            return None
        logging.getLogger('security').warning('CSRF rejected: endpoint=%s ip=%s', endpoint, request.remote_addr)
        return jsonify({'error': 'Invalid or expired form token. Reload the page and try again.'}), 400

    @app.context_processor
    def _inject_csrf_token():
        token = session.get('_csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['_csrf_token'] = token
        return {'csrf_token': token}

    # R8/AUDIT-112: the old hook silently stripped the Secure flag from
    # cookies whenever the *client-controllable* X-Forwarded-Proto said http.
    # Secure cookies are now governed solely by SESSION_COOKIE_SECURE/AMS_HTTPS=1
    # (PA serves HTTPS for free on <user>.pythonanywhere.com). To embed or
    # access over plain HTTP on a LAN, run the site as-is (cookie Secure will
    # simply not be set) — never by rewriting cookies based on a header.

    @app.before_request
    def _ensure_background_workers_started():
        # A web/WSGI worker must never start scheduled work: the database copy,
        # the reconcile pass and the backup poll all compete with the shop for
        # the SQLite write lock, and the reconcile pass rewrites balances.  These
        # threads belong to an explicit background process (cron -> tools/
        # maintenance.py) or a local dev server started with
        # AMS_ENABLE_BACKGROUND_WORKERS=1.  ``wsgi.py`` never sets that flag, so
        # this hook is a no-op on the deployed site.
        if not _ENABLE_BACKGROUND_WORKERS:
            return
        if _AUTO_BACKUP_ENABLED:
            _start_hourly_backup_worker()
        _start_reconcile_worker()
        from app.services.daily_backup import ensure_worker_started
        ensure_worker_started(app)

    @app.before_request
    def _enforce_user_permissions():
        if not current_user.is_authenticated:
            return None
        if current_user.role in ('admin', 'root'):
            return None
        endpoint = request.endpoint or ''
        short = endpoint.rsplit('.', 1)[-1]
        # Personal-preference endpoints every signed-in account owns: the theme
        # switch and its own presence heartbeat.  Nothing else is exempt.
        if short in _PERMISSION_EXEMPT_ENDPOINTS:
            return None
        # Core blueprints are registered before the legacy short aliases, so
        # Flask normally reports endpoints such as ``sales.add_booking`` while
        # the permission table historically used the short name.  Falling
        # back to the final component keeps both route forms protected.
        needed = ENDPOINT_PERMISSION_MAP.get(endpoint)
        if not needed and '.' in endpoint:
            needed = ENDPOINT_PERMISSION_MAP.get(endpoint.rsplit('.', 1)[-1])
        # Whole-feature-pack fallback (e.g. every /accounts route).
        if not needed and '.' in endpoint:
            needed = BLUEPRINT_PERMISSION_PREFIXES.get(endpoint.split('.', 1)[0])
        if not needed:
            return None
        # A tuple means "any of these permissions" (OR semantics).
        if isinstance(needed, (tuple, list, set)):
            allowed = any(_user_can(p) for p in needed)
        else:
            allowed = _user_can(needed)
        if allowed:
            return None
        # The dashboard itself is permission-gated, so redirecting a user who
        # lacks ``can_view_dashboard`` to ``index`` sent them straight back to
        # the guard that had just refused them: 302 -> / -> 302 for ever
        # (ERR_TOO_MANY_REDIRECTS) on every freshly created account, because
        # /add_user stores every can_* flag as 0.  Show a terminal page instead.
        if short in ('index', 'dashboard', 'home'):
            return _no_access_response(needed)
        # A JSON caller gets JSON, not a redirect to the login-less dashboard
        # (AMS-2026-1119-02: the /api/ surface is permission-gated now).
        if (request.path or '').startswith('/api/'):
            from flask import jsonify as _jsonify
            return _jsonify({'success': False,
                             'error': 'Permission denied for this module.',
                             'required_permission': needed if isinstance(needed, str) else list(needed)}), 403
        flash('Permission denied for this module.', 'danger')
        return redirect(url_for('index'))

    @app.before_request
    def _enforce_read_only_access_mode():
        """Read-Only accounts: block every mutating request, keep all views.

        Applies only to non-admin/non-root users whose ``access_mode`` is
        'read_only'.  Existing behaviour for everyone else is untouched.
        Personal preference endpoints (own UI theme) stay usable.
        """
        from flask import request as _req
        if _req.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        if not current_user.is_authenticated:
            return None
        if current_user.role in ('admin', 'root'):
            return None
        mode = (getattr(current_user, 'access_mode', None) or 'read_write').strip().lower()
        if mode != 'read_only':
            return None
        endpoint = _req.endpoint or ''
        short = endpoint.rsplit('.', 1)[-1] if endpoint else ''
        # Self-service endpoints a read-only user may still hit.
        if short in ('api_ui_theme', 'ui_theme', 'ui_theme_preference_api'):
            return None
        if (_req.path or '').rstrip('/') in ('/api/ui/theme',):
            return None
        if (_req.path or '').startswith('/static'):
            return None
        # AJAX calls expect JSON, not a redirect.
        if (_req.path or '').startswith('/api/'):
            from flask import jsonify as _jsonify
            return _jsonify({'success': False, 'error': 'Your account is READ-ONLY. Changes are not permitted.'}), 403
        flash('Your account is READ-ONLY — viewing is allowed, saving/changing/deleting is blocked.', 'danger')
        referrer = _req.referrer
        if referrer:
            return redirect(referrer)
        return redirect(url_for('index'))

    @app.context_processor
    def inject_dropdown_data():
        if current_user.is_authenticated:
            now_ts = time.time()
            cache_ttl = 20
            cache_obj = getattr(app, '_dropdown_cache', None)
            if (
                not isinstance(cache_obj, dict)
                or (now_ts - float(cache_obj.get('ts', 0) or 0)) > cache_ttl
            ):
                # This context processor feeds the shared layout, so it runs for
                # EVERY authenticated page.  An unhandled DB error here turned
                # every page after login into an HTTP 500 (the classic
                # "login works, then 500" report).  Degrade to empty dropdowns
                # instead and log the cause.
                try:
                    cache_obj = {
                        'ts': now_ts,
                        'clients': Client.query.filter_by(is_active=True).order_by(Client.name.asc()).all(),
                        'materials': Material.query.order_by(Material.name.asc()).all(),
                        'services': Service.query.filter_by(is_active=True).order_by(Service.name.asc()).all(),
                        'delivery_persons': DeliveryPerson.query.filter_by(is_active=True).order_by(DeliveryPerson.name.asc()).all(),
                        'settings': Settings.query.first(),
                    }
                    app._dropdown_cache = cache_obj
                except Exception:
                    try:
                        from models import db as _db
                        _db.session.rollback()
                    except Exception:
                        pass
                    logging.getLogger('app').exception(
                        "Could not load shared dropdown data; rendering the page "
                        "with empty dropdowns."
                    )
                    cache_obj = {'ts': 0, 'clients': [], 'materials': [],
                                 'services': [], 'delivery_persons': [],
                                 'settings': None}
            # Sidebar layout edited in Settings → Sidebar Settings.  Building
            # it must never break a page: a broken layout falls back to the
            # shipped defaults inside the service, and a hard error still
            # renders an (empty) sidebar rather than a 500.
            try:
                from app.services.sidebar_nav import build_nav
                sidebar_nav = build_nav(current_user)
            except Exception:
                logging.getLogger('app').exception(
                    "Could not build the custom sidebar navigation; rendering an empty sidebar."
                )
                sidebar_nav = {'root_items': [], 'categories': []}
            return dict(
                clients=cache_obj.get('clients') or [],
                materials=cache_obj.get('materials') or [],
                services=cache_obj.get('services') or [],
                delivery_persons=cache_obj.get('delivery_persons') or [],
                settings=cache_obj.get('settings'),
                # Terms & Conditions feed the Urdu print/download dialog on the
                # bill views. Fetch them fresh so settings edits appear immediately.
                terms=get_active_terms(),
                user_can=_user_can,
                sidebar_nav=sidebar_nav,
                ui_theme_preference=session.get('ui_theme')
            )
        return dict(ui_theme_preference=session.get('ui_theme'))

