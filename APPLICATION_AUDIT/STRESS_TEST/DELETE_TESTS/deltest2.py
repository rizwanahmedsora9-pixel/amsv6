"""Owner scenario 2: booking with money paid, wrong entry, delete -> must clear everything."""
import os, sys, sqlite3
DB=os.environ.get("PROOF_DB", "/tmp/ams_proof_del2.db")
for s in ("","-wal","-shm","-journal"):
    if os.path.exists(DB+s): os.remove(DB+s)
os.environ.update(APP_DB_PATH=DB, AMS_SCHEMA_VERSION="v44", AMS_ENABLE_BACKGROUND_WORKERS="0",
                  ALLOW_EMPTY_DB="1", WTF_CSRF_ENABLED="0", ALLOW_DB_DROP="1")
sys.path.insert(0, os.environ.get("AMS_APP_ROOT", "/home/user"))
from app import create_app
app=create_app(); app.config["TESTING"]=True; app.config["WTF_CSRF_ENABLED"]=False
c=app.test_client(); c.post("/login", data={"username":"Admin","password":"Admin@fbm12345"}, follow_redirects=True)
with app.app_context():
    from models import db as _db, Client, Material, Account
    _db.session.add(Client(code="C1", name="Test Traders", is_active=True))
    _db.session.add(Material(code="M1", name="OPC Cement", unit="Bags", unit_price=500.0, total=0.0, is_active=True))
    _db.session.add(Account(name="Cash", account_type="Cash", type="Asset", category="Cash",
        balance=0.0, balance_minor=0, opening_balance=1000000.0, opening_balance_minor=100000000, is_active=True))
    _db.session.commit()
    mid=str(Material.query.first().id); aid=str(Account.query.first().id)

WATCH=["booking","booking_item","pending_bill","entry","account_transaction","payment",
       "invoice","booking_allocation","direct_sale","grn"]
def snap():
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
    o={}
    for t in WATCH:
        try: o[t]=con.execute("SELECT COUNT(*) FROM %s"%t).fetchone()[0]
        except Exception: o[t]=None
    o["cash"]=con.execute("SELECT balance_minor FROM account LIMIT 1").fetchone()[0]/100.0
    o["open_recv"]=con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0").fetchone()[0]
    con.close(); return o
def show(a,b,label):
    print("\n--- %s ---" % label)
    ch=False
    for k in a:
        if a[k]!=b[k]: print("   %-22s %-12s -> %-12s" % (k,a[k],b[k])); ch=True
    if not ch: print("   (nothing changed)")
base=snap()
print("="*74); print("OWNER SCENARIO 2 — BOOKING WITH 'PAID NOW', THEN DELETE"); print("="*74)
r=c.post("/add_booking", data={
  "client_code":"C1","client_name":"Test Traders","date":"2026-11-02",
  "material_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["10"],"unit_rate[]":["500"],
  "amount":"5000","paid_amount":"2000","receive_in_account_id":aid,"payment_method":"Cash","payment_account_id":aid,
  "manual_bill_no":"BOOK-1"}, follow_redirects=True)
import re
for m in re.findall(r'<div class="alert alert-(\w+)[^"]*"[^>]*>(.*?)</div>', r.get_data(as_text=True), re.S)[:3]:
    print("  FLASH[%s]: %s" % (m[0], re.sub(r'<[^>]+>','',m[1]).strip()[:120]))
after=snap(); show(base, after, "after booking 5000 with 2000 paid now")
con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
bid=con.execute("SELECT id FROM booking ORDER BY id DESC LIMIT 1").fetchone()
bid=bid[0] if bid else None
print("\n  booking id:", bid, "| payment rows:", con.execute("SELECT id, amount, note FROM payment").fetchall())
print("  ledger rows:", con.execute("SELECT id, amount, note FROM account_transaction").fetchall())
print("  pending bills:", con.execute("SELECT id, bill_no, amount, is_paid FROM pending_bill").fetchall())
con.close()
if bid:
    r=c.post("/delete_transaction/Booking/%d"%bid, data={"reason":"mistake"}, follow_redirects=True)
    print("\n  DELETE Booking -> HTTP %s" % r.status_code)
    d=snap(); show(after, d, "after deleting the booking")
    print("\n  back to pre-booking state? %s" % ("YES" if all(d[k]==base[k] for k in ("booking","booking_item","pending_bill","account_transaction","payment","cash","open_recv")) else "NO"))
    print("  residue:", {k:(base[k],d[k]) for k in base if base[k]!=d[k]})
