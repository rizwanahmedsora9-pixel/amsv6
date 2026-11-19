"""AUDIT-039 regression: a fully paid sale must survive clicking Auto-Fix."""
import os, sys, sqlite3, re
DB=os.environ.get("PROOF_DB", "/tmp/ams_proof_afix.db")
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
    _db.session.add(Material(code="M1", name="OPC Cement", unit="Bags", unit_price=1000.0, total=0.0, is_active=True))
    _db.session.add(Supplier(name="Supplier A")); _db.session.add(DeliveryPerson(name="Driver 1"))
    _db.session.add(Account(name="Cash", account_type="Cash", type="Asset", category="Cash",
        balance=0.0, balance_minor=0, opening_balance=5000000.0, opening_balance_minor=500000000, is_active=True))
    _db.session.commit()
    mid=str(Material.query.first().id); sid=str(Supplier.query.first().id); aid=str(Account.query.first().id)
def snap():
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
    d=dict(pb=con.execute("SELECT COUNT(*) FROM pending_bill").fetchone()[0],
           open=con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0").fetchone()[0],
           paid=con.execute("SELECT COUNT(*) FROM pending_bill WHERE is_paid=1").fetchone()[0],
           cash=round(con.execute("SELECT COALESCE(balance,0) FROM account LIMIT 1").fetchone()[0],2),
           pay=con.execute("SELECT COUNT(*) FROM payment").fetchone()[0])
    con.close(); return d
c.post("/grn", data={"action":"add","supplier":"Supplier A","supplier_id":sid,"date":"2026-11-01","time":"09:00",
  "manual_bill_no":"G0","mat_name[]":["OPC Cement"],"qty[]":["500"],"price[]":["1000"],"tax_type":"none"}, follow_redirects=True)
c.post("/add_direct_sale", data={"sale_date":"2026-11-02T10:00","client_name":"Test Traders","client_code":"C1",
  "category":"Cash","driver_name":"Driver 1","product_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["1"],
  "unit_rate[]":["1000"],"alternate_material[]":[""],"alternate_material_id[]":[""],"grn_item_id[]":[""],
  "ignore_booking_item[]":["1"],"paid_amount":"1000","payment_method":"Cash","payment_account_id":aid,
  "manual_bill_no":"AF-1","idempotency_key":"af-1"}, follow_redirects=True)
before=snap()
print("  sale 1,000 fully paid  ->", before)
# click the button 4 times, exactly as AUDIT-039 did
for i in range(4):
    r=c.get("/fix_system_issues", follow_redirects=True)
after=snap()
print("  after clicking Auto-Fix 4x ->", after)
print()
print("  pending bills created by clicking : %d (was %d)" % (after['pb']-before['pb'], before['pb']))
print("  amount now shown as owed          : %.2f (was %.2f)" % (after['open'], before['open']))
verdict = (after==before)
print("  VERDICT: %s" % ("UNCHANGED — the button no longer touches data" if verdict else "DATA STILL CHANGES"))
