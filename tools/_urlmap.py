import sys
sys.path.insert(0, '/home/user/AMS')
from app import create_app
app = create_app()
rows = []
for r in app.url_map.iter_rules():
    u = str(r)
    if u.startswith('/accounts'):
        rows.append((u, r.endpoint, sorted(r.methods - {'HEAD', 'OPTIONS'})))
for u, e, m in sorted(rows):
    print(f'{u:55s} {e:45s} {m}')
print('total', len(rows))
