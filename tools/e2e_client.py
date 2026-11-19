"""ERP end-to-end transaction harness — drives the REAL app forms over HTTP.

* Every POST carries the session CSRF token scraped from a rendered page,
  exactly as the browser's `window.fetch` patch and hidden form input do.
* GETs follow redirects (trailing-slash 308 etc.); POSTs are captured
  un-followed so the `Location` header and flash can both be read.
* Every response's *server* flash alert is parsed from the layout's
  `<div class="alert alert-X alert-dismissible ...">` block.

Nothing is written to the database directly. All data entry goes through the
application's own routes.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('E2E_BASE', 'http://127.0.0.1:5001')

# Flash block exactly as templates/layout.html (line ~639) renders it:
#   <div class="alert alert-success alert-dismissible fade show fw-bold mb-4" role="alert">
# Requiring `alert-dismissible` keeps static in-modal warnings
# (e.g. materials.html "This action cannot be undone") out of the result.
FLASH_RE = re.compile(
    r'<div class="alert alert-(success|danger|warning|info)\s+alert-dismissible[^"]*"[^>]*>'
    r'\s*(.*?)\s*<button',
    re.S | re.I,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_flashes(html: str):
    """Only real server flashes (layout alert block), never script literals."""
    out = []
    for match in FLASH_RE.finditer(html):
        text = re.sub(r'<[^>]+>', ' ', match.group(2))
        text = re.sub(r'\s+', ' ', text).strip()
        if text:
            out.append((match.group(1).lower(), text))
    return out


class Client:
    def __init__(self, base=BASE):
        self.base = base.rstrip('/')
        self.jar = http.cookiejar.CookieJar()
        self.follow = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.keep = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect)
        self.csrf = None
        self.log = []

    # ---------------------------------------------------------------- tokens
    def _absorb_csrf(self, body: str):
        for pattern in (
            r'name="_csrf_token"\s+value="([^"]+)"',
            r'const csrfToken = "([^"]+)"',
        ):
            match = re.search(pattern, body)
            if match:
                self.csrf = match.group(1)
                return

    # ------------------------------------------------------------------- GET
    def get(self, path, absorb=True):
        req = urllib.request.Request(self.base + path, headers={'Accept': 'text/html'})
        try:
            resp = self.follow.open(req, timeout=180)
            body, status = resp.read().decode('utf-8', 'replace'), resp.status
        except urllib.error.HTTPError as exc:
            body, status = exc.read().decode('utf-8', 'replace'), exc.code
        if absorb:
            self._absorb_csrf(body)
        return status, body

    # ------------------------------------------------------------------ POST
    def post(self, path, data, follow_flash=True):
        """POST urlencoded -> dict(status, location, flashes, body, csrf_ok)."""
        if not self.csrf:
            self.get('/settings')
        payload = [(k, '' if v is None else str(v)) for k, v in data]
        # app/hooks.py reads the token from request.form, so it must be in the
        # body exactly as the layout's hidden input puts it.
        if not any(k == '_csrf_token' for k, _ in payload):
            payload.append(('_csrf_token', self.csrf or ''))
        body = urllib.parse.urlencode(payload)
        req = urllib.request.Request(
            self.base + path, data=body.encode(), method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRF-Token': self.csrf or '',
                'Accept': 'text/html',
                '_csrf_token': self.csrf or '',
            },
        )
        try:
            resp = self.keep.open(req, timeout=300)
            status, location = resp.status, resp.headers.get('Location', '')
            text = resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            status, location = exc.code, exc.headers.get('Location', '')
            text = exc.read().decode('utf-8', 'replace')

        flashes = parse_flashes(text)
        if not flashes and follow_flash and status in (301, 302, 303, 307, 308):
            target = urllib.parse.urlparse(location).path or '/'
            query = urllib.parse.urlparse(location).query
            get_path = target + (('?' + query) if query else '')
            status2, body2 = self.get(get_path)
            flashes = parse_flashes(body2)
        self._absorb_csrf(text)
        result = {
            'path': path, 'status': status, 'location': location,
            'flashes': flashes,
            'ok': any(k == 'success' for k, _ in flashes),
            'error': next((t for k, t in flashes if k in ('danger', 'warning')), None),
        }
        self.log.append(result)
        return result


def db_rows(sql, params=()):
    """READ-ONLY cross-check query against the live DB (never a write path)."""
    import sqlite3
    con = sqlite3.connect('/home/user/AMS/instance/ahmed_cement_v44_fresh.db')
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()
