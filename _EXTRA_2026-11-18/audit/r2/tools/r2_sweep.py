"""R2 Phase B — route inventory + render sweep of every GET route."""
import csv
import json
import re
import sys

sys.path.insert(0, "/home/user/AUDIT_2026-11-18/r2/tools")
import harness as H

DANGER = re.compile(r'(wipe|reset|purge|drop|truncate|restore|backup|migrate|seed|demo|nuke|erase)', re.I)

rows = []
for rule in H.app.url_map.iter_rules():
    if rule.endpoint == 'static':
        continue
    methods = sorted((rule.methods or set()) - {'HEAD', 'OPTIONS'})
    rows.append({
        'module': rule.endpoint.split('.')[0],
        'endpoint': rule.endpoint,
        'url': str(rule),
        'methods': ','.join(methods),
        'get': 'yes' if 'GET' in methods else 'no',
        'mutating': 'yes' if any(m in ('POST', 'PUT', 'PATCH', 'DELETE') for m in methods) else 'no',
        'params': len(rule.arguments),
        'rule_args': ','.join(sorted(rule.arguments)),
        'danger_name': 'yes' if DANGER.search(str(rule)) or DANGER.search(rule.endpoint) else 'no',
    })
rows.sort(key=lambda r: (r['module'], r['url']))
print(f"routes: {len(rows)}  GET: {sum(1 for r in rows if r['get']=='yes')}  "
      f"mutating: {sum(1 for r in rows if r['mutating']=='yes')}  "
      f"danger-named: {sum(1 for r in rows if r['danger_name']=='yes')}")

# ---------------------------------------------------------------- render sweep
c = H.client()
LOGIN_PAGE = c.get("/", follow_redirects=True).get_data(as_text=True)
assert 'Logout' in LOGIN_PAGE, "login failed"

def build_url(url):
    out = url
    for arg in re.findall(r'<(?:\w+:)?(\w+)>', url):
        out = re.sub(r'<(?:[^:>]+:)?' + arg + r'>', '1', out, count=1)
    return out

results = []
for r in rows:
    if r['get'] != 'yes':
        continue
    if r['danger_name'] == 'yes':
        results.append({**r, 'status': 'SKIPPED-DANGER', 'bytes': 0, 'verdict': 'excluded'})
        continue
    url = build_url(r['url'])
    try:
        resp = c.get(url, follow_redirects=False)
        raw = resp.get_data()                      # bytes: exports are binary
        ctype = (resp.headers.get('Content-Type') or '').split(';')[0]
        status = resp.status_code
        if status in (200, 302):
            verdict = 'ok'
        elif status == 404:
            verdict = 'missing-entity'
        elif status == 400:
            verdict = 'rejected-input'             # validation refused a bogus argument
        else:
            verdict = 'ERROR'
        results.append({**r, 'status': status, 'bytes': len(raw), 'ctype': ctype,
                        'verdict': verdict, 'location': resp.headers.get('Location', '')})
    except Exception as exc:  # noqa: BLE001
        results.append({**r, 'status': 'EXC', 'bytes': 0, 'verdict': 'ERROR', 'location': str(exc)[:200]})

errors = [r for r in results if r['verdict'] == 'ERROR']
print(f"\nGET routes swept: {len([r for r in results if r['verdict'] != 'excluded'])}")
print(f"  ok/redirect: {sum(1 for r in results if r['verdict']=='ok')}")
print(f"  404 (no entity id 1): {sum(1 for r in results if r['verdict']=='missing-entity')}")
print(f"  ERRORS: {len(errors)}")
for e in errors:
    print(f"    {e['status']}  {e['url']}  ({e['endpoint']})  {e.get('location','')[:120]}")

with open("/home/user/AUDIT_2026-11-18/r2/evidence/routes_sweep.json", "w") as fh:
    json.dump(results, fh, indent=2, default=str)
with open("/home/user/AUDIT_2026-11-18/r2/evidence/routes_all.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# module summary
mods = {}
for r in rows:
    m = mods.setdefault(r['module'], {'routes': 0, 'get': 0, 'mut': 0, 'tested': 0, 'errors': 0})
    m['routes'] += 1
    m['get'] += 1 if r['get'] == 'yes' else 0
    m['mut'] += 1 if r['mutating'] == 'yes' else 0
for res in results:
    m = mods[res['module']]
    if res['verdict'] not in ('excluded',):
        m['tested'] += 1
    if res['verdict'] == 'ERROR':
        m['errors'] += 1
print("\nmodule summary:")
for name, m in sorted(mods.items(), key=lambda kv: -kv[1]['routes']):
    print(f"  {name:18s} routes={m['routes']:3d} get={m['get']:3d} mutating={m['mut']:3d} "
          f"get-tested={m['tested']:3d} errors={m['errors']}")
