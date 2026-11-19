"""Shared helpers for the round-2 audit scenarios (read-only DB access)."""
import json
import os
import re

import harness as H
import recorder as R

#: The business date every scenario posts on.  The application decides "today"
#: in Asia/Karachi (``pk_now()``), so the harness must use exactly the same
#: clock: a hard-coded date made the whole suite drift out of step with the
#: dashboard's "today" tiles the moment the wall clock rolled past midnight.
def _business_today():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Karachi")).date().isoformat()
    except Exception:  # pragma: no cover - tzdata missing
        from datetime import date
        return date.today().isoformat()


TODAY = os.environ.get("AMS_AUDIT_DATE") or _business_today()


# ---- money / stock readers ------------------------------------------------
def f(x):
    return float(x or 0)


def stock(name):
    row = H.one("SELECT total FROM material WHERE name = ?", (name,))
    return f(row["total"]) if row else None


def qty_from_entries(name):
    """Independent stock figure: opening + IN - OUT (voided rows excluded)."""
    ins = H.scalar("SELECT SUM(qty) FROM entry WHERE material = ? AND type='IN' AND COALESCE(is_void,0)=0", (name,))
    outs = H.scalar("SELECT SUM(qty) FROM entry WHERE material = ? AND type='OUT' AND COALESCE(is_void,0)=0", (name,))
    return f(ins) - f(outs)


def cash(account_id=1):
    return f(H.scalar("SELECT balance FROM account WHERE id = ?", (account_id,)))


def open_receivable():
    return f(H.scalar(
        "SELECT SUM(amount) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND COALESCE(is_paid,0)=0"))


def open_payable_supplier(name=None):
    sql = ("SELECT SUM(amount) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND COALESCE(is_paid,0)=0"
           " AND source_table IN ('grn','supplier')")
    if name:
        return f(H.scalar(sql + " AND client_name = ?", (name,)))
    return f(H.scalar(sql))


def page(url):
    """Rendered text of a page with tags and scripts stripped."""
    c = H.client()
    r = c.get(url, follow_redirects=True)
    body = r.get_data(as_text=True)
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", body)
    return r.status_code, re.sub(r"\s+", " ", txt)


def money_in(text, label):
    """First Rs amount following `label` on a page."""
    m = re.search(re.escape(label) + r"[^0-9\-]{0,60}(-?\d[\d,]*(?:\.\d+)?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return None


def post_json(c, url, payload):
    r = c.post(url, data=json.dumps(payload), content_type="application/json")
    try:
        return r.status_code, r.get_json()
    except Exception:
        return r.status_code, None


def check(test_id, module, page_url, scenario, expected, actual, ok, severity="MEDIUM",
          evidence="", note=""):
    return R.record(test_id, module, page_url, scenario, expected, actual,
                    "PASS" if ok else "FAIL",
                    severity=None if ok else severity, evidence=evidence, note=note)


def show(label, obj):
    print(f"    {label}: {obj}")
    return obj
