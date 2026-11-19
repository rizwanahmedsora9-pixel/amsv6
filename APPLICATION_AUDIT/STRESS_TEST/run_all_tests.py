#!/usr/bin/env python3
"""AMS regression suite — every test from the audit, in one run.

WHY THIS EXISTS
---------------
The original stress test took about 3 hours because it was exploratory: each
question was answered by writing a new probe script, seeding a fresh 2,000-row
database, and re-deriving the expected figures by hand. Almost all of that time
was spent on work that only had to be done once.

This file is the result of that work, frozen. It runs the same checks in a fixed
order against a database it builds itself, so it can be re-run after any change
in about two minutes instead of three hours.

WHAT IT DOES NOT DO
-------------------
It does not prove the app is correct in general — it proves the specific defects
found in this audit stay fixed and the specific invariants still hold. A green
run means "nothing that was ever broken here has broken again".

USAGE
-----
    python3 run_all_tests.py                 # full run (~2 min)
    python3 run_all_tests.py --quick          # skip the volume seed (~40 s)
    python3 run_all_tests.py --app /path/to/AMS
    python3 run_all_tests.py --keep           # keep the scratch database

Exit code is 0 only when every check passes, so it can gate a deploy.

DESIGN RULES BAKED IN (each one was learned the hard way)
---------------------------------------------------------
1.  Every assertion reads through a SEPARATE READ-ONLY SQLite connection.
    Reading through the ORM session under test reports uncommitted state as if
    it were saved. That single mistake produced a withdrawn CRITICAL finding.
2.  Never detect success from page text. The dashboard JavaScript contains the
    word "successfully"; success means a real `alert-success` element.
3.  Never accept a 302 as success. A handler that swallows a lock error still
    redirects.
4.  One unique database per test, deleted afterwards. Two app instances on one
    SQLite file produce `database is locked`, which looks like a defect.
5.  Never compare "money still owed" with "all money ever received". Those are
    different quantities; the difference is not an error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback

RESULTS: list[tuple[str, str, str]] = []   # (section, status, detail)
T0 = time.time()


# ---------------------------------------------------------------- reporting
def record(section: str, status: str, detail: str = "") -> None:
    RESULTS.append((section, status, detail))
    icon = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP", "INFO": "  . "}[status]
    print(f"  [{icon}] {section}" + (f" — {detail}" if detail else ""))


def head(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------- app harness
def prepare_env(app_root: str, db_path: str, extra_env: dict | None = None) -> None:
    """Environment the app needs when driven from a scratch database."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)
    env = {
        "APP_DB_PATH": db_path,
        "AMS_SCHEMA_VERSION": "v44",
        "AMS_ENABLE_BACKGROUND_WORKERS": "0",
        "ALLOW_EMPTY_DB": "1",
        "WTF_CSRF_ENABLED": "0",
        "ALLOW_DB_DROP": "1",
    }
    env.update(extra_env or {})
    os.environ.update(env)
    if app_root not in sys.path:
        sys.path.insert(0, app_root)


def make_app_and_client(app_root: str, db_path: str):
    prepare_env(app_root, db_path)
    from app import create_app  # noqa: E402

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    client.post(
        "/login",
        data={"username": "Admin", "password": "Admin@fbm12345"},
        follow_redirects=True,
    )
    return app, client


def flashes(body: str) -> list[tuple[str, str]]:
    """Real flash messages only — never a bare substring search."""
    out = []
    for cat, msg in re.findall(
        r'<div class="alert alert-(\w+)[^"]*"[^>]*>(.*?)</div>', body, re.S
    ):
        out.append((cat, re.sub(r"<[^>]+>", "", msg).strip()[:120]))
    return out


def ro(db_path: str):
    """Read-only connection — the only legitimate source of truth."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def scalar(db_path: str, sql: str, params=()):
    con = ro(db_path)
    try:
        row = con.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        con.close()


# ---------------------------------------------------------------- fixture
def seed_base(db_path: str, app_obj) -> dict:
    """One client, one material, one supplier, one account, 1,000 in stock."""
    with app_obj.app_context():
        from models import (  # noqa: E402
            Account, Client, DeliveryPerson, Material, Supplier, db,
        )

        db.session.add(Client(code="C1", name="Test Traders", is_active=True))
        db.session.add(
            Material(
                code="M1", name="OPC Cement", unit="Bags",
                unit_price=500.0, total=0.0, is_active=True,
            )
        )
        db.session.add(Supplier(name="Supplier A"))
        db.session.add(DeliveryPerson(name="Driver 1"))
        db.session.add(
            Account(
                name="Cash", account_type="Cash", type="Asset", category="Cash",
                balance=0.0, balance_minor=0, opening_balance=5000000.0,
                opening_balance_minor=500000000, is_active=True,
            )
        )
        db.session.commit()
        return {
            "material_id": str(Material.query.first().id),
            "supplier_id": str(Supplier.query.first().id),
            "account_id": str(Account.query.first().id),
        }


def receive_stock(client, ids: dict, qty: int = 1000, price: int = 500) -> None:
    r = client.post(
        "/grn",
        data={
            "action": "add", "supplier": "Supplier A",
            "supplier_id": ids["supplier_id"], "date": "2026-11-01",
            "time": "09:00", "manual_bill_no": "G-BASE",
            "mat_name[]": ["OPC Cement"], "qty[]": [str(qty)],
            "price[]": [str(price)], "tax_type": "none",
        },
        follow_redirects=True,
    )
    if not any(c == "success" for c, _ in flashes(r.get_data(as_text=True))):
        raise SystemExit("fixture: GRN rejected; cannot continue")


def last_id(db_path: str, table: str) -> int | None:
    return scalar(db_path, f"SELECT MAX(id) FROM {table}")


# ---------------------------------------------------------------- sections
def section_integrity(app_root: str, workdir: str) -> str | None:
    """Schema, foreign keys, and the destructive-button regression."""
    head("1. BOOT + SCHEMA INTEGRITY")
    db_path = os.path.join(workdir, "integrity.db")
    try:
        app, client = make_app_and_client(app_root, db_path)
    except Exception:
        record("app boots from a scratch database", "FAIL", traceback.format_exc(limit=3))
        return None

    routes = len(list(app.url_map.iter_rules()))
    record("app boots", "PASS", f"{routes} routes registered")

    fk = scalar(db_path, "PRAGMA foreign_key_check")
    record("PRAGMA foreign_key_check empty", "PASS" if fk is None else "FAIL", str(fk))
    ok = scalar(db_path, "PRAGMA integrity_check")
    record("PRAGMA integrity_check", "PASS" if ok == "ok" else "FAIL", str(ok))

    tables = scalar(
        db_path, "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    )
    record("tables present", "INFO", str(tables))

    # The four fixes must still be in the source.
    checks = [
        ("app/services/void_rebuild.py", "_reopen_bills_settled_by_payment",
         "delete-payment reopens settled bills"),
        ("app/services/schema.py", "ix_pending_bill_client_name_norm",
         "client-name expression index"),
        ("app/blueprints/misc/extra.py", "Diagnosis complete",
         "Auto-Fix is read-only"),
        ("app/blueprints/reports/_profit_profit_reports.py",
         "No sales or purchases were recorded", "empty-period notice"),
    ]
    for rel, needle, label in checks:
        path = os.path.join(app_root, rel)
        try:
            found = needle in open(path, encoding="utf-8").read()
        except OSError:
            found = False
        record(label, "PASS" if found else "FAIL", rel)

    tpl = os.path.join(app_root, "templates/system_report.html")
    try:
        still = "fix_system_issues" in open(tpl, encoding="utf-8").read()
    except OSError:
        still = False
    record(
        "Auto-Fix button removed from the page",
        "PASS" if not still else "FAIL",
        "link still present" if still else "no link",
    )
    return db_path


def section_delete(app_root: str, workdir: str) -> None:
    """Owner's requirement: a deliberate delete restores the exact prior state."""
    head("2. DELETE CLEANLINESS — every kind must return to the exact prior state")
    db_path = os.path.join(workdir, "delete.db")
    app, client = make_app_and_client(app_root, db_path)
    ids = seed_base(db_path, app)
    receive_stock(client, ids)

    TABLES = [
        "direct_sale", "direct_sale_item", "booking", "booking_item",
        "pending_bill", "entry", "account_transaction", "payment", "invoice",
        "delivery_rent", "sale_delivery_persons", "booking_allocation",
        "grn", "grn_item", "supplier_payment", "material_return",
        "material_return_item", "waive_off",
    ]

    def snapshot() -> dict:
        con = ro(db_path)
        try:
            d = {}
            for t in TABLES:
                try:
                    d[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except sqlite3.Error:
                    pass
            d["cash"] = round(
                con.execute("SELECT COALESCE(balance,0) FROM account LIMIT 1").fetchone()[0], 2
            )
            d["stock"] = con.execute("SELECT total FROM material LIMIT 1").fetchone()[0]
            d["open"] = con.execute(
                "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill "
                "WHERE COALESCE(is_void,0)=0 AND is_paid=0"
            ).fetchone()[0]
            return d
        finally:
            con.close()

    def sale(qty: str, paid: str, ref: str, category: str = "Credit Customer"):
        return client.post(
            "/add_direct_sale",
            data={
                "sale_date": "2026-11-02T10:00", "client_name": "Test Traders",
                "client_code": "C1", "category": category, "driver_name": "Driver 1",
                "product_name[]": ["OPC Cement"], "material_id[]": [ids["material_id"]],
                "qty[]": [qty], "unit_rate[]": ["500"],
                "alternate_material[]": [""], "alternate_material_id[]": [""],
                "grn_item_id[]": [""], "ignore_booking_item[]": ["1"],
                "paid_amount": paid,
                "payment_method": "Cash" if paid != "0" else "",
                "payment_account_id": ids["account_id"] if paid != "0" else "",
                "manual_bill_no": ref, "idempotency_key": ref.lower(),
            },
            follow_redirects=True,
        )

    def booking(amount: str, paid: str, ref: str):
        return client.post(
            "/add_booking",
            data={
                "client_code": "C1", "client_name": "Test Traders",
                "date": "2026-11-03", "material_name[]": ["OPC Cement"],
                "material_id[]": [ids["material_id"]], "qty[]": ["6"],
                "unit_rate[]": ["500"], "amount": amount, "paid_amount": paid,
                "payment_method": "Cash" if paid != "0" else "",
                "receive_in_account_id": ids["account_id"] if paid != "0" else "",
                "manual_bill_no": ref,
            },
            follow_redirects=True,
        )

    def run_case(label, create, kind, table):
        base = snapshot()
        create()
        rid = last_id(db_path, table)
        if rid is None:
            record(label, "FAIL", "entry was not created at all")
            return
        client.post(f"/delete_transaction/{kind}/{rid}", data={"reason": "test"},
                    follow_redirects=True)
        after = snapshot()
        drift = {k: (base.get(k), after.get(k)) for k in base if base.get(k) != after.get(k)}
        residue = {k: v for k, v in drift.items() if k != "audit_log"}
        record(
            label,
            "PASS" if not residue else "FAIL",
            "clean" if not residue else f"residue {residue}",
        )

    run_case("cash sale, fully paid → delete",
             lambda: sale("2", "1000", "D-CASH", "Cash"), "DirectSale", "direct_sale")
    run_case("credit sale, nothing paid → delete",
             lambda: sale("3", "0", "D-CREDIT"), "DirectSale", "direct_sale")
    run_case("credit sale, half paid at counter → delete",
             lambda: sale("4", "1000", "D-HALF"), "DirectSale", "direct_sale")
    run_case("booking, nothing paid → delete",
             lambda: booking("3000", "0", "D-BK0"), "Booking", "booking")
    run_case("booking, part paid at booking → delete",
             lambda: booking("3000", "1500", "D-BK1"), "Booking", "booking")

    base = snapshot()
    client.post(
        "/add_payment",
        data={
            "client_name": "Test Traders", "client_code": "C1", "amount": "750",
            "method": "Cash", "payment_account_id": ids["account_id"],
            "date": "2026-11-04", "manual_bill_no": "D-PAY0", "note": "",
            "idempotency_key": "d-pay0",
        },
        follow_redirects=True,
    )
    pid = last_id(db_path, "payment")
    if pid is None:
        record("standalone payment → delete", "FAIL", "payment was not created")
    else:
        client.post(f"/delete_transaction/Payment/{pid}", data={}, follow_redirects=True)
        after = snapshot()
        drift = {k: (base.get(k), after.get(k)) for k in base if base.get(k) != after.get(k)}
        residue = {k: v for k, v in drift.items() if k != "audit_log"}
        record("standalone payment → delete", "PASS" if not residue else "FAIL",
               "clean" if not residue else f"residue {residue}")


def section_reopen(app_root: str, workdir: str) -> None:
    """The fixed defect: deleting a payment must reopen the bill it settled."""
    head("3. DELETING A PAYMENT REOPENS THE BILL IT SETTLED  (AUDIT-041)")
    db_path = os.path.join(workdir, "reopen.db")
    app, client = make_app_and_client(app_root, db_path)
    ids = seed_base(db_path, app)
    receive_stock(client, ids)

    client.post(
        "/add_direct_sale",
        data={
            "sale_date": "2026-11-02T10:00", "client_name": "Test Traders",
            "client_code": "C1", "category": "Credit Customer",
            "driver_name": "Driver 1", "product_name[]": ["OPC Cement"],
            "material_id[]": [ids["material_id"]], "qty[]": ["10"],
            "unit_rate[]": ["500"], "alternate_material[]": [""],
            "alternate_material_id[]": [""], "grn_item_id[]": [""],
            "ignore_booking_item[]": ["1"], "paid_amount": "0",
            "payment_method": "", "manual_bill_no": "RO-1",
            "idempotency_key": "ro-1",
        },
        follow_redirects=True,
    )

    def open_amount() -> float:
        return scalar(
            db_path,
            "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill "
            "WHERE COALESCE(is_void,0)=0 AND is_paid=0",
        )

    after_sale = open_amount()
    record("credit sale 5,000 shows 5,000 owed", "PASS" if abs(after_sale - 5000) < 0.01 else "FAIL",
           f"{after_sale}")

    client.post(
        "/add_payment",
        data={
            "client_name": "Test Traders", "client_code": "C1", "amount": "5000",
            "method": "Cash", "payment_account_id": ids["account_id"],
            "date": "2026-11-03", "manual_bill_no": "RO-PAY", "note": "",
            "idempotency_key": "ro-pay",
        },
        follow_redirects=True,
    )
    after_pay = open_amount()
    record("payment 5,000 clears the debt", "PASS" if abs(after_pay) < 0.01 else "FAIL",
           f"{after_pay}")

    pid = last_id(db_path, "payment")
    client.post(f"/delete_transaction/Payment/{pid}", data={}, follow_redirects=True)
    after_delete = open_amount()
    record(
        "deleting the payment REOPENS 5,000 of debt",
        "PASS" if abs(after_delete - 5000) < 0.01 else "FAIL",
        f"{after_delete} (expected 5000.00)",
    )

    cash = scalar(db_path, "SELECT COALESCE(balance,0) FROM account LIMIT 1")
    record("cash returns to 0", "PASS" if abs(cash) < 0.01 else "FAIL", f"{round(cash,2)}")

    bill = ro(db_path).execute(
        "SELECT amount, is_paid FROM pending_bill WHERE COALESCE(is_void,0)=0"
    ).fetchone()
    record(
        "bill reopened at its true value, not paid",
        "PASS" if bill and abs(bill[0] - 5000) < 0.01 and not bill[1] else "FAIL",
        f"amount={bill[0]}, is_paid={bill[1]}" if bill else "no bill",
    )


def section_advance(app_root: str, workdir: str) -> None:
    """The owner's own flow: advance first (green), book, then delete the booking."""
    head("4. ADVANCE (GREEN BALANCE) → BOOKING → DELETE THE BOOKING")
    db_path = os.path.join(workdir, "advance.db")
    app, client = make_app_and_client(app_root, db_path)
    ids = seed_base(db_path, app)
    receive_stock(client, ids)

    def state():
        con = ro(db_path)
        try:
            paid = con.execute(
                "SELECT ROUND(COALESCE(SUM(COALESCE(amount,0)+COALESCE(discount,0)),0),2) "
                "FROM payment WHERE COALESCE(is_void,0)=0"
            ).fetchone()[0]
            owed = con.execute(
                "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill "
                "WHERE COALESCE(is_void,0)=0 AND is_paid=0"
            ).fetchone()[0]
            cash = round(con.execute(
                "SELECT COALESCE(balance,0) FROM account LIMIT 1").fetchone()[0], 2)
            return {"held": round(paid - owed, 2), "owed": owed, "cash": cash}
        finally:
            con.close()

    client.post(
        "/add_payment",
        data={
            "client_name": "Test Traders", "client_code": "C1", "amount": "10000",
            "method": "Cash", "payment_account_id": ids["account_id"],
            "date": "2026-11-02", "manual_bill_no": "ADV-1", "note": "",
            "idempotency_key": "adv-1",
        },
        follow_redirects=True,
    )
    s1 = state()
    record("10,000 advance shows as 10,000 held (green)",
           "PASS" if abs(s1["held"] - 10000) < 0.01 else "FAIL", str(s1))

    client.post(
        "/add_booking",
        data={
            "client_code": "C1", "client_name": "Test Traders",
            "date": "2026-11-03", "material_name[]": ["OPC Cement"],
            "material_id[]": [ids["material_id"]], "qty[]": ["12"],
            "unit_rate[]": ["500"], "amount": "6000", "paid_amount": "0",
            "manual_bill_no": "ADV-BK",
        },
        follow_redirects=True,
    )
    s2 = state()
    record("booking 6,000 auto-settles against the advance",
           "PASS" if abs(s2["held"] - 4000) < 0.01 and abs(s2["owed"] - 6000) < 0.01 else "FAIL",
           str(s2))

    bid = last_id(db_path, "booking")
    client.post(f"/delete_transaction/Booking/{bid}", data={}, follow_redirects=True)
    s3 = state()
    record("deleting the booking restores 10,000 held, 0 owed",
           "PASS" if abs(s3["held"] - 10000) < 0.01 and abs(s3["owed"]) < 0.01 else "FAIL",
           str(s3))
    record("the advance payment itself survives",
           "PASS" if scalar(db_path, "SELECT COUNT(*) FROM payment") == 1 else "FAIL",
           f"{scalar(db_path, 'SELECT COUNT(*) FROM payment')} payment row(s)")


def section_autofix(app_root: str, workdir: str) -> None:
    """AUDIT-039: the Auto-Fix route must not touch a single row."""
    head("5. AUTO-FIX ROUTE IS READ-ONLY  (AUDIT-039)")
    db_path = os.path.join(workdir, "autofix.db")
    app, client = make_app_and_client(app_root, db_path)
    ids = seed_base(db_path, app)
    receive_stock(client, ids)

    client.post(
        "/add_direct_sale",
        data={
            "sale_date": "2026-11-02T10:00", "client_name": "Test Traders",
            "client_code": "C1", "category": "Cash", "driver_name": "Driver 1",
            "product_name[]": ["OPC Cement"], "material_id[]": [ids["material_id"]],
            "qty[]": ["1"], "unit_rate[]": ["1000"], "alternate_material[]": [""],
            "alternate_material_id[]": [""], "grn_item_id[]": [""],
            "ignore_booking_item[]": ["1"], "paid_amount": "1000",
            "payment_method": "Cash", "payment_account_id": ids["account_id"],
            "manual_bill_no": "AF-1", "idempotency_key": "af-1",
        },
        follow_redirects=True,
    )

    def sig():
        con = ro(db_path)
        try:
            return (
                con.execute("SELECT COUNT(*) FROM pending_bill").fetchone()[0],
                con.execute(
                    "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill "
                    "WHERE COALESCE(is_void,0)=0 AND is_paid=0").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM entry").fetchone()[0],
                con.execute("SELECT ROUND(COALESCE(SUM(total),0),2) FROM material").fetchone()[0],
                round(con.execute("SELECT COALESCE(balance,0) FROM account LIMIT 1").fetchone()[0], 2),
            )
        finally:
            con.close()

    before = sig()
    for _ in range(4):
        client.get("/fix_system_issues", follow_redirects=True)
    after = sig()
    record("4 clicks create no rows and change no figure",
           "PASS" if before == after else "FAIL",
           f"{before} -> {after}")
    record("a fully paid sale still owes nothing",
           "PASS" if abs(after[1]) < 0.01 else "FAIL", f"open={after[1]}")


def section_accounting(app_root: str, workdir: str) -> None:
    """The maths that has to hold on any dataset."""
    head("6. ACCOUNTING INVARIANTS")
    db_path = os.path.join(workdir, "accounting.db")
    app, client = make_app_and_client(app_root, db_path)
    ids = seed_base(db_path, app)
    receive_stock(client, ids)

    client.post(
        "/add_direct_sale",
        data={
            "sale_date": "2026-11-02T10:00", "client_name": "Test Traders",
            "client_code": "C1", "category": "Credit Customer",
            "driver_name": "Driver 1", "product_name[]": ["OPC Cement"],
            "material_id[]": [ids["material_id"]], "qty[]": ["4"],
            "unit_rate[]": ["500"], "alternate_material[]": [""],
            "alternate_material_id[]": [""], "grn_item_id[]": [""],
            "ignore_booking_item[]": ["1"], "paid_amount": "0",
            "payment_method": "", "manual_bill_no": "AC-1", "idempotency_key": "ac-1",
        },
        follow_redirects=True,
    )
    client.post(
        "/add_payment",
        data={
            "client_name": "Test Traders", "client_code": "C1", "amount": "1200",
            "method": "Cash", "payment_account_id": ids["account_id"],
            "date": "2026-11-03", "manual_bill_no": "AC-PAY", "note": "",
            "idempotency_key": "ac-pay",
        },
        follow_redirects=True,
    )

    stock = scalar(db_path, "SELECT total FROM material LIMIT 1")
    in_qty = scalar(db_path, "SELECT COALESCE(SUM(qty),0) FROM entry WHERE type='IN' AND COALESCE(is_void,0)=0")
    out_qty = scalar(db_path, "SELECT COALESCE(SUM(qty),0) FROM entry WHERE type='OUT' AND COALESCE(is_void,0)=0")
    record("stock total == IN - OUT", "PASS" if abs(stock - (in_qty - out_qty)) < 0.001 else "FAIL",
           f"{stock} vs {in_qty}-{out_qty}")

    hdr = scalar(db_path, "SELECT COALESCE(SUM(amount),0) FROM direct_sale WHERE COALESCE(is_void,0)=0")
    lines = scalar(
        db_path,
        "SELECT COALESCE(SUM(i.qty * i.price_at_time),0) FROM direct_sale_item i "
        "JOIN direct_sale s ON s.id=i.sale_id WHERE COALESCE(s.is_void,0)=0",
    )
    record("sale header == sum of its lines", "PASS" if abs(hdr - lines) < 0.01 else "FAIL",
           f"{hdr} vs {lines}")

    cash = round(float(scalar(
        db_path, "SELECT COALESCE(balance,0) FROM account LIMIT 1") or 0), 2)
    received = round(float(scalar(
        db_path,
        "SELECT COALESCE(SUM(amount),0) FROM payment WHERE COALESCE(is_void,0)=0") or 0), 2)
    record("cash book reflects the receipt", "PASS" if abs(cash - received) < 0.01 else "FAIL",
           f"cash={cash} vs payments={received}")

    paid = scalar(
        db_path,
        "SELECT ROUND(COALESCE(SUM(COALESCE(amount,0)+COALESCE(discount,0)),0),2) "
        "FROM payment WHERE COALESCE(is_void,0)=0")
    owed = scalar(
        db_path,
        "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill "
        "WHERE COALESCE(is_void,0)=0 AND is_paid=0")
    sold = scalar(db_path, "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM direct_sale WHERE COALESCE(is_void,0)=0")
    booked = scalar(db_path, "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM booking WHERE COALESCE(is_void,0)=0")
    record("sold + booked == paid + still-owed",
           "PASS" if abs((sold + booked) - (paid + owed)) < 0.01 else "FAIL",
           f"{sold}+{booked} vs {paid}+{owed}")

    record("entry numbers: no nulls, no duplicates",
           "PASS" if scalar(
               db_path,
               "SELECT COUNT(*) FROM (SELECT entry_seq FROM pending_bill GROUP BY entry_seq "
               "HAVING entry_seq IS NULL OR COUNT(*)>1)") == 0 else "FAIL", "pending_bill")


def section_volume(app_root: str, workdir: str, n: int = 100) -> None:
    """Volume: payments must still settle at scale, and totals must add up."""
    head(f"7. VOLUME — {n} sales, {n} payments, {n} bookings")
    db_path = os.path.join(workdir, "volume.db")
    app, client = make_app_and_client(app_root, db_path)
    ids = seed_base(db_path, app)
    receive_stock(client, ids, qty=n * 10, price=500)

    made = {"sales": 0, "payments": 0, "bookings": 0}
    for i in range(n):
        r = client.post(
            "/add_direct_sale",
            data={
                "sale_date": f"2026-09-{(i % 28) + 1:02d}T10:00",
                "client_name": "Test Traders", "client_code": "C1",
                "category": "Credit Customer", "driver_name": "Driver 1",
                "product_name[]": ["OPC Cement"], "material_id[]": [ids["material_id"]],
                "qty[]": ["1"], "unit_rate[]": ["500"],
                "alternate_material[]": [""], "alternate_material_id[]": [""],
                "grn_item_id[]": [""], "ignore_booking_item[]": ["1"],
                "paid_amount": "0", "payment_method": "",
                "manual_bill_no": f"V-S{i:04d}", "idempotency_key": f"v-s{i}",
            },
            follow_redirects=True,
        )
        if any(c == "success" for c, _ in flashes(r.get_data(as_text=True))):
            made["sales"] += 1

    for i in range(n):
        r = client.post(
            "/add_payment",
            data={
                "client_name": "Test Traders", "client_code": "C1",
                "amount": "500", "method": "Cash",
                "payment_account_id": ids["account_id"],
                "date": f"2026-09-{(i % 28) + 1:02d}",
                "manual_bill_no": f"V-P{i:04d}", "note": "",
                "idempotency_key": f"v-p{i}",
            },
            follow_redirects=True,
        )
        if any(c == "success" for c, _ in flashes(r.get_data(as_text=True))):
            made["payments"] += 1

    for i in range(n):
        r = client.post(
            "/add_booking",
            data={
                "client_code": "C1", "client_name": "Test Traders",
                "date": f"2026-09-{(i % 28) + 1:02d}",
                "material_name[]": ["OPC Cement"],
                "material_id[]": [ids["material_id"]], "qty[]": ["2"],
                "unit_rate[]": ["500"], "amount": "1000", "paid_amount": "0",
                "manual_bill_no": f"V-B{i:04d}",
            },
            follow_redirects=True,
        )
        if any(c == "success" for c, _ in flashes(r.get_data(as_text=True))):
            made["bookings"] += 1

    record("all entries created", "PASS" if all(v == n for v in made.values()) else "FAIL", str(made))

    # NOTE: pending_bill is already NET of settlement, so summing it only ever
    # reproduces "owed" — it cannot test an identity. The truthful source is the
    # documents themselves. This is the check that a withdrawn CRITICAL finding
    # should have been: money received + money still owed == money billed.
    sold = scalar(db_path, "SELECT COALESCE(SUM(amount),0) FROM direct_sale WHERE COALESCE(is_void,0)=0")
    booked = scalar(db_path, "SELECT COALESCE(SUM(amount),0) FROM booking WHERE COALESCE(is_void,0)=0")
    paid = scalar(db_path, "SELECT COALESCE(SUM(COALESCE(amount,0)+COALESCE(discount,0)),0) FROM payment WHERE COALESCE(is_void,0)=0")
    owed = scalar(db_path, "SELECT COALESCE(SUM(amount),0) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0")
    record("sold + booked == paid + still-owed at volume",
           "PASS" if abs((sold + booked) - (paid + owed)) < 0.02 else "FAIL",
           f"{sold}+{booked} vs {paid}+{owed}")

    fk = scalar(db_path, "PRAGMA foreign_key_check")
    record("no orphans after volume", "PASS" if fk is None else "FAIL", str(fk))

    stock = scalar(db_path, "SELECT total FROM material LIMIT 1")
    in_qty = scalar(db_path, "SELECT COALESCE(SUM(qty),0) FROM entry WHERE type='IN' AND COALESCE(is_void,0)=0")
    out_qty = scalar(db_path, "SELECT COALESCE(SUM(qty),0) FROM entry WHERE type='OUT' AND COALESCE(is_void,0)=0")
    record("stock conserved at volume", "PASS" if abs(stock - (in_qty - out_qty)) < 0.001 else "FAIL",
           f"{stock} vs {in_qty}-{out_qty}")


def section_reports(app_root: str, workdir: str) -> None:
    """Every report route must render, and the formerly destructive one must be safe."""
    head("8. REPORT ROUTES")
    db_path = os.path.join(workdir, "reports.db")
    app, client = make_app_and_client(app_root, db_path)
    ids = seed_base(db_path, app)
    receive_stock(client, ids)

    urls = sorted({
        str(r) for r in app.url_map.iter_rules()
        if r.endpoint and "GET" in (r.methods or set())
        and not str(r).startswith("/static")
        and not any(p in str(r) for p in ("<", ">", "logout", "delete", "void"))
        and "material_info" not in str(r)
    })
    # Report- and ledger-shaped pages only; this is not a link checker.
    KEY = ("report", "ledger", "pending", "unpaid", "sale", "booking", "payment",
           "grn", "material", "client", "dashboard", "profit", "cash", "account",
           "supplier", "tracking", "delivery", "summary", "system")
    targets = [u for u in urls if any(k in u.lower() for k in KEY)][:60]

    ok = bad = 0
    failures = []
    for u in targets:
        try:
            r = client.get(u, follow_redirects=True)
            if r.status_code == 200:
                ok += 1
            else:
                bad += 1
                failures.append(f"{u} -> {r.status_code}")
        except Exception as exc:
            bad += 1
            failures.append(f"{u} -> {type(exc).__name__}")
    record(f"{ok}/{ok + bad} pages render", "PASS" if bad == 0 else "FAIL",
           "; ".join(failures[:4]))

    r = client.get("/profit_reports?start_date=2000-01-01&end_date=2000-01-31",
                   follow_redirects=True)
    body = r.get_data(as_text=True)
    record("P&L on an empty period explains itself",
           "PASS" if "No sales or purchases" in body else "FAIL",
           f"HTTP {r.status_code}")


# ---------------------------------------------------------------- old suite
def section_legacy(app_root: str, workdir: str) -> None:
    """Point the reader at the older, heavier instruments that still work."""
    head("9. HEAVY INSTRUMENTS (not run here — use when you need full proof)")
    for name, what in [
        ("verify_full.py", "42 independent SQL assertions on a seeded 2,000/section DB"),
        ("crosscheck_full.py", "recomputes every business total straight from SQL"),
        ("reports_full.py", "requests all 38 report routes on the big DB"),
        ("final_sweep_full.py", "orphan / duplicate / arithmetic sweep"),
    ]:
        present = os.path.exists(os.path.join(os.path.dirname(__file__), "EVIDENCE", name))
        record(name, "INFO", what + ("" if present else "  (see APPLICATION_AUDIT/STRESS_TEST/EVIDENCE)"))


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default=os.environ.get("AMS_APP_ROOT", "/home/user"),
                    help="path to the AMS application root (folder containing app/)")
    ap.add_argument("--quick", action="store_true", help="skip the volume section")
    ap.add_argument("--volume", type=int, default=100, help="records per section in the volume test")
    ap.add_argument("--keep", action="store_true", help="keep the scratch databases")
    args = ap.parse_args()

    if not os.path.isdir(os.path.join(args.app, "app")):
        print(f"error: {args.app} does not look like the AMS root (no app/ folder)")
        return 2

    workdir = tempfile.mkdtemp(prefix="ams_regression_")
    print(f"AMS regression suite")
    print(f"  app      : {args.app}")
    print(f"  scratch  : {workdir}")
    print(f"  mode     : {'quick' if args.quick else 'full'}")

    try:
        section_integrity(args.app, workdir)
        section_delete(args.app, workdir)
        section_reopen(args.app, workdir)
        section_advance(args.app, workdir)
        section_autofix(args.app, workdir)
        section_accounting(args.app, workdir)
        if args.quick:
            head("7. VOLUME — skipped (--quick)")
            record("volume section", "SKIP", "run without --quick")
        else:
            section_volume(args.app, workdir, n=args.volume)
        section_reports(args.app, workdir)
        section_legacy(args.app, workdir)
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"\nscratch databases kept in {workdir}")

    elapsed = time.time() - T0
    head("RESULT")
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    skipped = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    for section, status, detail in RESULTS:
        if status == "FAIL":
            print(f"  FAIL  {section}  {detail}")
    print(f"\n  {passed} passed · {failed} failed · {skipped} skipped · {elapsed:.1f}s")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "last_run.json")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(
                {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "app": args.app,
                 "seconds": round(elapsed, 1),
                 "results": [{"section": a, "status": b, "detail": c} for a, b, c in RESULTS]},
                fh, indent=2,
            )
        print(f"  written: {out}")
    except OSError:
        pass

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
