"""S9 — access control, CSRF, injection, exports, and the UI contract."""
import os
import re
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L

print("== S9 security / exports / UI ==")

# ---------------------------------------------------------------- 1. anonymous access
anon = H.app.test_client()
probe = ["/", "/bookings", "/sales", "/grn", "/accounts/dashboard", "/system_report",
         "/import_export", "/logs", "/settings", "/root/backup-settings", "/current_payables",
         "/financial_ledger/2", "/api/supplier_balance/1", "/profit_reports"]
leaks, server_errors = [], []
for url in probe:
    try:
        r = anon.get(url, follow_redirects=False)
        if r.status_code == 200:
            leaks.append((url, r.status_code))
    except Exception as exc:                      # TESTING=True re-raises what a browser would get as a 500
        server_errors.append((url, type(exc).__name__, str(exc)))
L.check("R2-X01", "Security / Authentication", "sample of 14 sensitive routes",
        "Request every sensitive page without a session",
        "nothing renders data without a login",
        f"routes probed={len(probe)}; rendered without login={leaks}; raised instead of redirecting={server_errors}",
        not leaks, "CRITICAL")

if server_errors:
    L.check("R2-X09", "Security / Authentication", "GET " + ", ".join(u for u, _t, _m in server_errors),
            "Open the pages that answered an anonymous request with an exception instead of a login redirect",
            "an anonymous visitor is redirected to /login (302)",
            f"exceptions={server_errors}",
            False, "MEDIUM",
            evidence="app/blueprints/system/logs.py::_guard() reads current_user.role before the "
                     "login check, so AnonymousUserMixin has no attribute 'role' and the request dies "
                     "with a 500 instead of redirecting. The route itself carries no @login_required.")
else:
    L.check("R2-X09", "Security / Authentication", "anonymous probes", "no anonymous 500s",
            "no route raises for an anonymous visitor", "clean", True, "MEDIUM")

# ---------------------------------------------------------------- 2. CSRF gate (forced on)
import subprocess
csrf = subprocess.run([sys.executable, "tools/s9b_csrf.py"], capture_output=True, text=True,
                      env={**os.environ, "AMS_CSRF_ALWAYS": "1"}, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
print("    csrf probe:", csrf.stdout.strip().splitlines()[-1:] or csrf.stderr.strip().splitlines()[-1:])

# ---------------------------------------------------------------- 3. role separation
c = H.client()
c.post("/add_user", data={"username": "r2_clerk", "password": "Clerk@fbm12345",
                          "role": "user", "access_mode": "read_write"}, follow_redirects=True)
c.post("/add_user", data={"username": "r2_sales", "password": "Sales@fbm12345",
                          "role": "user", "access_mode": "read_write"}, follow_redirects=True)
sales_id = H.scalar("SELECT id FROM user WHERE username='r2_sales'")
c.post(f"/edit_user_permissions/{sales_id}",
       data={"role": "user", "access_mode": "read_write", "can_view_dashboard": "on",
             "can_manage_sales": "on", "can_view_client_ledger": "on"}, follow_redirects=True)
sales_flags = {k: v for k, v in H.q("SELECT * FROM user WHERE username='r2_sales'")[0].items()
               if k.startswith("can_") and v}
L.show("r2_sales permissions", sales_flags)

cl = H.app.test_client()
cl.post("/login", data={"username": "r2_sales", "password": "Sales@fbm12345"}, follow_redirects=True)
restricted = {}
for url in ("/system_report", "/settings", "/logs", "/import_export", "/root/backup-settings",
            "/accounts/", "/accounts/purchase_payables", "/wipe"):
    try:
        restricted[url] = cl.get(url, follow_redirects=False).status_code
    except Exception as exc:
        restricted[url] = f"EXC {type(exc).__name__}"
allowed = {}
for url in ("/", "/direct_sales", "/material_returns"):
    try:
        allowed[url] = cl.get(url, follow_redirects=False).status_code
    except Exception as exc:
        allowed[url] = f"EXC {type(exc).__name__}"
L.check("R2-X03", "Security / Roles", "user 'r2_sales' (sales + dashboard only)",
        "Log in as a sales-only user and open admin areas and their own areas",
        "admin/settings/import/wipe areas are refused; the sales pages the permission grants are served",
        f"refused-statuses={restricted}; own-area statuses={allowed}",
        all(v != 200 for v in restricted.values()) and all(v == 200 for v in allowed.values()), "HIGH")

# --- a brand-new user with no permissions ticked
cl2 = H.app.test_client()
loop = []
r = cl2.post("/login", data={"username": "r2_clerk", "password": "Clerk@fbm12345"}, follow_redirects=False)
url = r.headers.get("Location") or "/"
for _ in range(5):
    rr = cl2.get(url, follow_redirects=False)
    loop.append((url, rr.status_code, rr.headers.get("Location")))
    if rr.status_code != 302 or rr.headers.get("Location") == url:
        break
    url = rr.headers.get("Location")
L.check("R2-X10", "Security / Roles", "new user 'r2_clerk' with no permissions ticked",
        "Log in as a user the admin just created, before ticking any permission",
        "the app explains that the account has no access (a page, not a loop)",
        f"redirect chain={loop}",
        not all(code == 302 and loc == "/" for _u, code, loc in loop), "HIGH",
        evidence="POST /add_user stores every can_* flag as 0; requesting / then redirects to / for ever "
                 "(the browser reports ERR_TOO_MANY_REDIRECTS). Granting one permission fixes it, so the "
                 "loop is the 'no permission' path of the dashboard guard.")

# ---------------------------------------------------------------- 4. injection probe
c = H.client()
base = L.f(H.scalar("SELECT COUNT(*) FROM client"))
for payload in ("' OR 1=1 --", "'; DROP TABLE client; --", "%' UNION SELECT 1,2,3 --"):
    st, txt = L.page(f"/bookings?q={payload}")
    st2, txt2 = L.page(f"/inventory/daily_transactions?client={payload}")
rows_after = L.f(H.scalar("SELECT COUNT(*) FROM client"))
L.check("R2-X04", "Security / Injection", "/bookings?q= + /inventory/daily_transactions?client=",
        "Send three classic SQL-injection strings through the search and filter boxes",
        "no stack trace, no table loss, the page still renders normally",
        f"client rows {base}->{rows_after}; last page length={len(txt2)}; "
        f"traceback present={'Traceback' in txt2 or 'Traceback' in txt}",
        rows_after == base and "Traceback" not in txt2 and "Traceback" not in txt, "CRITICAL")

# ---------------------------------------------------------------- 5. stored XSS
c.post("/add_client", data={"name": "R2 <script>alert(1)</script>", "code": "ACX1",
                            "category": "Retail", "phone": "0", "address": "x",
                            "opening_balance": "0"}, follow_redirects=True)
st, listing = L.page("/clients")
st2, page_txt = L.page("/current_payables")
raw = c.get("/clients").get_data(as_text=True)
L.check("R2-X05", "Security / XSS", "/clients",
        "Save a client whose name contains a script tag and re-open the client list",
        "the name is HTML-escaped, so no script tag reaches the browser unescaped",
        f"literal '<script>alert(1)</script>' present in the HTML={('<script>alert(1)</script>' in raw)}; "
        f"escaped form present={'&lt;script&gt;' in raw}",
        "<script>alert(1)</script>" not in raw, "HIGH")

# ---------------------------------------------------------------- 6. exports really export
exports = {
    "/profit_reports/export/excel": "xlsx",
    "/profit_reports/export/pdf": "pdf",
    "/download_supplier_ledger/1": "other",
    "/download_delivery_person_ledger/1": "other",
}
results = {}
for url, kind in exports.items():
    r = c.get(url)
    body = r.get_data()
    ok_sig = True
    if url.endswith("excel"):
        ok_sig = body[:2] == b"PK"
    elif url.endswith("pdf"):
        ok_sig = body[:4] == b"%PDF"
    results[url] = (r.status_code, len(body), r.headers.get("Content-Type"), ok_sig)
L.check("R2-X06", "Exports / Files", "profit excel + pdf + ledger downloads",
        "Download the P&L workbook, the P&L PDF and two ledgers",
        "each returns a real file with the right signature (PK for xlsx, %PDF for pdf) and real content",
        f"{results}", all(v[0] == 200 and v[1] > 20 and v[3] for v in results.values()), "MEDIUM",
        note="PDF export only works once the optional requirements-pdf.txt is installed; the shipped "
             "environment had it missing and the screen degraded with a clear message "
             "('PDF export is unavailable. Install requirements-pdf.txt').")

# ---------------------------------------------------------------- 7. UI contract on the main pages
pages = ["/", "/bookings", "/direct_sales", "/grn", "/material_returns", "/pending_bills",
         "/accounts/", "/accounts/expenditures", "/accounts/pay_grn",
         "/accounts/purchase_payables", "/current_payables", "/financial_ledger/2",
         "/client_ledger/2", "/profit_reports", "/cash_flow", "/stock_summary",
         "/inventory/daily_transactions", "/system_report", "/logs", "/settings"]
ui = []
for url in pages:
    r = c.get(url, follow_redirects=True)
    html = r.get_data(as_text=True)
    problems = []
    if r.status_code != 200:
        problems.append(f"HTTP {r.status_code}")
    if re.search(r"\bNone\b|\bnan\b|\bNaN\b", re.sub(r"<script.*?</script>", " ", html, flags=re.S)):
        problems.append("raw None/nan in markup")
    if "{{" in html or "{%" in html:
        problems.append("unrendered template tag")
    if "Traceback" in html:
        problems.append("traceback")
    if problems:
        ui.append((url, problems))
L.check("R2-X07", "UI / Rendering contract", "20 main pages",
        "Render the twenty busiest pages and read the served HTML",
        "every page returns 200 with no raw None/nan, no unrendered Jinja and no traceback",
        f"pages checked={len(pages)}; problems={ui}", not ui, "MEDIUM")

# ---------------------------------------------------------------- 8. destructive GET safety
danger = ["/wipe", "/reset_system", "/purge", "/restore_backup", "/backup", "/import_export/export"]
before = L.f(H.scalar("SELECT COUNT(*) FROM direct_sale"))
hits = {}
for url in danger:
    r = c.get(url, follow_redirects=False)
    hits[url] = r.status_code
after = L.f(H.scalar("SELECT COUNT(*) FROM direct_sale"))
L.check("R2-X08", "Security / Destructive routes", "wipe/reset/purge/backup GET probes",
        "GET the destructive-looking URLs to make sure browsing cannot destroy data",
        "none of them performs a wipe (no 2xx page that executes, sales and stock unchanged)",
        f"statuses={hits}; direct_sale rows {before}->{after}",
        after == before, "CRITICAL")

print("S9 done")
