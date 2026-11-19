"""Full delete suite: every kind, must return to the exact pre-entry state."""
import os, sys, sqlite3, time
DB=os.environ.get("PROOF_DB", "/tmp/ams_proof_suite.db")
for s in ("","-wal","-shm","-journal"):
    if os.path.exists(DB+s): os.remove(DB+s)
os.environ.update(APP_DB_PATH=DB, AMS_SCHEMA_VERSION="v44", AMS_ENABLE_BACKGROUND_WORKERS="0",
                  ALLOW_EMPTY_DB="1", WTF_CSRF_ENABLED="0", ALLOW_DB_DROP="1")
sys.path.insert(0, os.environ.get("AMS_APP_ROOT", "/home/user"))
from app import create_app
app=create_app(); app.config["TESTING"]=True; app.config["WTF_CSRF_ENABLED"]=False
c=app.test_client(); c.post("/login", data={"username":"Admin","password":"Admin@fbm12345"}, follow_redirects=True)
with app.app_context():
    from models import db as _db, Client, Material, Supplier, DeliveryPerson, Account
    _db.session.add(Client(code="C1", name="Test Traders", is_active=True))
    _db.session.add(Material(code="M1", name="OPC Cement", unit="Bags", unit_price=500.0, total=0.0, is_active=True))
    _db.session.add(Supplier(name="Supplier A")); _db.session.add(DeliveryPerson(name="Driver 1"))
    _db.session.add(Account(name="Cash", account_type="Cash", type="Asset", category="Cash",
        balance=0.0, balance_minor=0, opening_balance=5000000.0, opening_balance_minor=500000000, is_active=True))
    _db.session.commit()
    mid=str(Material.query.first().id); sid=str(Supplier.query.first().id); aid=str(Account.query.first().id)

T=["direct_sale","direct_sale_item","booking","booking_item","pending_bill","entry","account_transaction",
   "payment","invoice","delivery_rent","sale_delivery_persons","booking_allocation","grn","grn_item",
   "supplier_payment","material_return","material_return_item","waive_off"]
def snap():
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
    o={}
    for t in T:
        try: o[t]=con.execute("SELECT COUNT(*) FROM %s"%t).fetchone()[0]
        except Exception: pass
    o["cash"]=round(con.execute("SELECT COALESCE(balance,0) FROM account LIMIT 1").fetchone()[0],2)
    o["stock"]=con.execute("SELECT total FROM material LIMIT 1").fetchone()[0]
    o["open"]=con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0").fetchone()[0]
    con.close(); return o
def run(label, fn, kind, getid):
    base=snap(); fn()
    mid_s=snap()
    rid=getid()
    t0=time.time()
    r=c.post("/delete_transaction/%s/%s"%(kind,rid), data={"reason":"test"}, follow_redirects=True)
    el=(time.time()-t0)*1000
    after=snap()
    drift={k:(base.get(k),after.get(k)) for k in base if base.get(k)!=after.get(k)}
    ok = not drift
    print("  %-42s %-4s %6.0fms  %s" % (label, r.status_code, el, "CLEAN" if ok else "RESIDUE"))
    if drift:
        for k,(a,b) in drift.items(): print("        %-22s %s -> %s" % (k,a,b))
    return ok
def lastid(table):
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
    r=con.execute("SELECT MAX(id) FROM %s"%table).fetchone()[0]; con.close(); return r

print("="*78); print("DELETE SUITE — every delete must restore the exact prior state"); print("="*78)
stock=0
c.post("/grn", data={"action":"add","supplier":"Supplier A","supplier_id":sid,"date":"2026-11-01",
  "time":"09:00","manual_bill_no":"G-INIT","mat_name[]":["OPC Cement"],"qty[]":["500"],"price[]":["500"],
  "tax_type":"none"}, follow_redirects=True)
ok=[]

ok.append(run("cash sale, fully paid at counter", lambda: c.post("/add_direct_sale", data={
  "sale_date":"2026-11-02T10:00","client_name":"Test Traders","client_code":"C1","category":"Cash",
  "driver_name":"Driver 1","product_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["2"],
  "unit_rate[]":["500"],"alternate_material[]":[""],"alternate_material_id[]":[""],"grn_item_id[]":[""],
  "ignore_booking_item[]":["1"],"paid_amount":"1000","payment_method":"Cash","payment_account_id":aid,
  "manual_bill_no":"D-CASH","idempotency_key":"d-cash"}, follow_redirects=True),
  "DirectSale", lambda: lastid("direct_sale")))

ok.append(run("credit sale, nothing paid", lambda: c.post("/add_direct_sale", data={
  "sale_date":"2026-11-02T11:00","client_name":"Test Traders","client_code":"C1","category":"Credit Customer",
  "driver_name":"Driver 1","product_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["3"],
  "unit_rate[]":["500"],"alternate_material[]":[""],"alternate_material_id[]":[""],"grn_item_id[]":[""],
  "ignore_booking_item[]":["1"],"paid_amount":"0","payment_method":"",
  "manual_bill_no":"D-CREDIT","idempotency_key":"d-credit"}, follow_redirects=True),
  "DirectSale", lambda: lastid("direct_sale")))

ok.append(run("credit sale, half paid at counter", lambda: c.post("/add_direct_sale", data={
  "sale_date":"2026-11-02T12:00","client_name":"Test Traders","client_code":"C1","category":"Credit Customer",
  "driver_name":"Driver 1","product_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["4"],
  "unit_rate[]":["500"],"alternate_material[]":[""],"alternate_material_id[]":[""],"grn_item_id[]":[""],
  "ignore_booking_item[]":["1"],"paid_amount":"1000","payment_method":"Cash","payment_account_id":aid,
  "manual_bill_no":"D-HALF","idempotency_key":"d-half"}, follow_redirects=True),
  "DirectSale", lambda: lastid("direct_sale")))

ok.append(run("booking, nothing paid", lambda: c.post("/add_booking", data={
  "client_code":"C1","client_name":"Test Traders","date":"2026-11-03",
  "material_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["5"],"unit_rate[]":["500"],
  "amount":"2500","paid_amount":"0","manual_bill_no":"D-BK0"}, follow_redirects=True),
  "Booking", lambda: lastid("booking")))

ok.append(run("booking, part paid at booking", lambda: c.post("/add_booking", data={
  "client_code":"C1","client_name":"Test Traders","date":"2026-11-03",
  "material_name[]":["OPC Cement"],"material_id":[mid],"qty[]":["6"],"unit_rate[]":["500"],
  "amount":"3000","paid_amount":"1500","payment_method":"Cash","receive_in_account_id":aid,
  "manual_bill_no":"D-BK1"}, follow_redirects=True),
  "Booking", lambda: lastid("booking")))

ok.append(run("standalone payment (no bill attached)", lambda: c.post("/add_payment", data={
  "client_name":"Test Traders","client_code":"C1","amount":"750","method":"Cash",
  "payment_account_id":aid,"date":"2026-11-04","manual_bill_no":"D-PAY0","note":"",
  "idempotency_key":"d-pay0"}, follow_redirects=True),
  "Payment", lambda: lastid("payment")))

print()
print("  %d/%d delete operations returned to the exact prior state" % (sum(ok), len(ok)))
