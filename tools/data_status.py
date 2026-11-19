"""Count every table in the AMS database and print a status report.

Usage:
    python3 tools/data_status.py            # human table
    python3 tools/data_status.py --json     # machine readable

Read-only: opens the SQLite file directly (no app, no writes, no locks beyond a
shared read). One section per business area, plus a separate list of the tables
that are *meant* to keep rows (users, settings, schema history).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / 'instance' / 'ahmed_cement_v44_fresh.db'

# Business data, grouped the way an operator thinks about it.
GROUPS = [
    ('Clients & suppliers', [
        'client', 'client_category', 'supplier', 'delivery_person',
        'delivery_person_payment', 'loan_party', 'loan_movement',
    ]),
    ('Entries & stock', [
        'entry', 'grn', 'grn_item', 'grn_charge', 'grn_allocation',
        'stock_adjustment', 'material_return', 'material_return_item',
        'delivery', 'delivery_item', 'delivery_rent', 'sale_delivery_persons',
    ]),
    ('Bills, sales & payments', [
        'direct_sale', 'direct_sale_item', 'direct_sale_draft', 'booking',
        'booking_item', 'booking_allocation', 'pending_bill', 'invoice',
        'payment', 'supplier_payment', 'waive_off',
        'follow_up_contact', 'follow_up_reminder',
    ]),
    ('Accounts & cash', [
        'account', 'account_category', 'account_transaction',
        'account_reconciliation', 'recon_basket',
        'cash_flow_entry', 'cash_flow_entry_audit', 'cash_flow_category',
        'cash_flow_subcategory', 'cash_flow_party',
        'cash_flow_difference_adjustment', 'cash_flow_reconciliation_audit',
        'cash_day_account_position', 'cash_day_difference', 'cash_day_lock',
        'cash_day_shift_close', 'cash_day_shift_close_position',
        'fbm_cash_drawer_entry', 'fbm_cash_drawer_category',
    ]),
    ('Materials & services', ['material', 'material_category', 'service']),
    ('Rentals (FBM)', ['fbm_client', 'fbm_rental', 'fbm_rental_item']),
    ('Archives & notifications', [
        'booking_allocation_repair_archive', 'staff_email',
    ]),
    ('Import / export & migration', [
        'import_upload', 'import_job', 'import_history_entry',
        'migration_run', 'migration_mapping', 'migration_row',
    ]),
    ('Logs (ours + legacy)', [
        'activity_event', 'activity_session',
        'user_login_session', 'audit_log', 'accounting_audit_log',
    ]),
]

# Tables the app must keep working after a wipe.  `bill_counter` is the next
# bill number (not money) — the app needs it to allocate the following SB/MB
# number, so it is preserved like settings, and reported separately from money.
KEPT_WITH_COUNTER = ['bill_counter']

# Tables that are supposed to survive a wipe.
KEPT = [
    'user', 'settings', 'terms_and_conditions', 'sidebar_category', 'sidebar_item',
    'schema_version', 'migration_history', 'system_lock',
    'daily_db_backup_settings', 'daily_db_backup_history',
    'root_backup_settings', 'root_backup_email_history', 'root_recovery_code',
    'tenant_wipe_backup_history', 'future_account_audit_log',
]


def db_path(argv):
    for i, arg in enumerate(argv):
        if arg == '--db' and i + 1 < len(argv):
            return Path(argv[i + 1])
    env = os.environ.get('APP_DB_PATH')
    return Path(env) if env else DEFAULT_DB


def table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def count(conn, table):
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return None


def conn_peek(path, table):
    return None


def main(argv):
    path = db_path(argv)
    if not path.exists():
        print(f'Database not found: {path}')
        return 1
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    present = set(table_names(conn))
    result = {'db': str(path), 'groups': [], 'kept': [], 'unclassified': []}

    for label, tables in GROUPS:
        rows = [(t, count(conn, t)) for t in tables if t in present]
        result['groups'].append({
            'label': label,
            'tables': rows,
            'total': sum(n or 0 for _t, n in rows),
        })

    result['kept'] = [(t, count(conn, t)) for t in KEPT if t in present]
    result['numbering'] = [(t, count(conn, t)) for t in KEPT_WITH_COUNTER if t in present]

    classified = {t for _l, ts in GROUPS for t in ts} | set(KEPT) | set(KEPT_WITH_COUNTER)
    leftovers = sorted(present - classified)
    result['unclassified'] = [(t, count(conn, t)) for t in leftovers]
    conn.close()

    if '--json' in argv:
        print(json.dumps(result, indent=2))
        return 0

    business_total = sum(g['total'] for g in result['groups']
                         if g['label'] != 'Logs (ours + legacy)')
    print(f'\nDatabase : {path}')
    print(f'Business rows across all sections: {business_total:,}\n')
    width = max(len(t) for _l, ts in GROUPS for t in ts) + 2
    for group in result['groups']:
        if not group['tables']:
            continue
        print(f'--- {group["label"]} ' + '-' * max(0, 46 - len(group['label'])))
        for name, n in group['tables']:
            flag = '' if n == 0 else '   <-- has rows'
            print(f'  {name:<{width}} {0 if n is None else n:>8,}{flag}')
        print(f'  {"subtotal":<{width}} {group["total"]:>8,}\n')
    print('--- Numbering (preserved on purpose — not money) ---')
    for name, n in result['numbering']:
        nxt = None
        try:
            row = conn_peek(path, name)
        except Exception:
            row = None
        print(f'  {name:<{width}} {0 if n is None else n:>8,}   (holds the next bill number)')
    print('\n--- Kept on purpose (users / settings / history) ' + '-' * 6)
    for name, n in result['kept']:
        print(f'  {name:<{width}} {0 if n is None else n:>8,}')
    if result['unclassified']:
        print('\n--- Unclassified tables (not in any list — review) ---')
        for name, n in result['unclassified']:
            print(f'  {name:<{width}} {0 if n is None else n:>8,}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
