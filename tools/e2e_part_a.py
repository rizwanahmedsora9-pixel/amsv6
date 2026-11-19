"""PART A — master records (clients, supplier, materials) and the cash account.

All entered through the app's own POST routes.  The cash account is needed
because every payment screen requires an account to post against.
"""
import sys
sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client, db_rows

RESULTS = []


def rec(part, label, res, note=''):
    RESULTS.append({'part': part, 'label': label, 'path': res['path'],
                    'status': res['status'], 'location': res['location'],
                    'flashes': res['flashes'], 'ok': res['ok'], 'error': res['error'],
                    'note': note})
    flag = 'OK  ' if res['ok'] else 'FAIL'
    print(f"[{flag}] {part:3s} {label:42s} {res['status']} -> {res['location'][:60]}")
    for kind, text in res['flashes']:
        print(f"        ({kind}) {text[:200]}")
    return res


def run(c):
    # ---------------------------------------------------------------- clients
    rec('A', 'client Asif', c.post('/add_client', [
        ('name', 'Asif'), ('code', 'ASIF-001'), ('category', 'General'),
        ('phone', '03000000001'), ('address', 'Islamabad'),
        ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
        ('page_entry', 'bookings'),
    ]))
    rec('A', 'client Bilal', c.post('/add_client', [
        ('name', 'Bilal'), ('code', 'BILAL-002'), ('category', 'General'),
        ('phone', '03000000002'), ('address', 'Islamabad'),
        ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
        ('page_entry', 'bookings'),
    ]))
    # ---------------------------------------------------------------- supplier
    rec('A', 'supplier Nadeem', c.post('/add_supplier', [
        ('name', 'Nadeem'), ('phone', '03000000003'), ('address', 'Islamabad'),
        ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
    ]))
    # --------------------------------------------------------------- materials
    for name, code, unit in (
        ('Cement Bag', 'CEM-01', 'Bag'),
        ('12mm Steel', 'STL-12', 'KG'),
        ('20mm Steel', 'STL-20', 'KG'),
    ):
        rec('A', f'material {name}', c.post('/add_material', [
            ('material_name', name), ('material_code', code),
            ('category_id', '1'), ('material_unit', unit),
        ]))
    # ------------------------------------------------------------ cash account
    rec('A', 'cash account Main Cash', c.post('/accounts/accounts/add', [
        ('name', 'Main Cash'), ('class_category', 'Assets'),
        ('class_subcategory', 'Cash'), ('class_account_type', 'Main Cash'),
        ('channel', 'cash'), ('account_status', 'active'),
        ('opening_amount', '0'), ('opening_position', 'debit'),
        ('opening_effective_date', '2026-11-01'),
        ('cash_location', 'Shop'), ('note', 'E2E test cash account'),
    ]))
    return RESULTS


if __name__ == '__main__':
    c = Client()
    s, _ = c.get('/login')
    r = c.post('/login', [('username', 'Admin'), ('password', 'Admin@fbm12345')])
    print('login ->', r['status'], r['flashes'])
    run(c)
    print('\n--- db cross-check ---')
    print('clients  ', db_rows("select id,code,name,phone,opening_balance from client"))
    print('suppliers', db_rows("select id,name,phone,opening_balance from supplier"))
    print('materials', db_rows("select id,name,code,unit from material"))
    print('accounts ', db_rows("select id,name,category,class_category,class_subcategory,class_account_type,balance,opening_balance from account"))
