import sys, re
sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client

c = Client()
s, b = c.get('/login')
print('login', s)
st, loc, fl, t = c.post('/login', [('username','Admin'),('password','Admin@fbm12345')])
print('POST login ->', st, loc)

def fields(path):
    st, body = c.get(path)
    names = []
    for m in re.finditer(r'<(input|select|textarea)[^>]*name="([^"]+)"', body):
        tag, name = m.group(1), m.group(2)
        if name in names: continue
        names.append(name)
    print(f'\n--- {path} [{st}] {len(body)}B')
    print('  ', ', '.join(names))

for p in ['/accounts/add', '/clients', '/suppliers', '/materials', '/bookings', '/dispatching',
          '/direct_sales', '/grn', '/payments', '/cash_flow', '/material_returns', '/supplier_ledger/1']:
    fields(p)

st, body = c.get('/accounts/add')
print('\n=== accounts/add select options ===')
for m in re.finditer(r'<select[^>]*name="(class_category|class_subcategory|class_account_type|channel|account_status)"[^>]*>(.*?)</select>', body, re.S):
    opts = re.findall(r'<option value="([^"]*)"', m.group(2))
    print(m.group(1), opts[:20])
