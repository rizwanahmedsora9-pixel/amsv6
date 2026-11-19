"""CASE B in isolation: delete a payment -> the bills it settled must reopen."""
import os, sys, sqlite3
DB=os.environ.get("PROOF_DB", "/tmp/ams_proof_del4.db")
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
        balance=0.0, balance_minor=0, opening_balance=10000000.0, opening_balance_minor=1000000000, is_active=True))
    _db.session.commit()
    mid=str(Material.query.first().id); sid=str(Supplier.query.first().id); aid=str(Account.query.first().id)
def q(s):
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True); r=con.execute(s).fetchall(); con.close(); return r
def st(tag):
    open_amt=q("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0")[0][0]
    cash=q("SELECT ROUND(COALESCE(balance,0),2) FROM account LIMIT 1")[0][0]
    pays=q("SELECT COUNT(*) FROM payment")[0][0]
    print("   %-32s payments=%s  open_receivable=%-10s cash=%s" % (tag,pays,open_amt,cash))
    return open_amt, cash
c.post("/grn", data={"action":"add","supplier":"Supplier A","supplier_id":sid,"date":"2026-11-01",
    "time":"09:00","manual_bill_no":"G1","mat_name[]":["OPC Cement"],"qty[]":["100"],"price[]":["500"],
    "tax_type":"none"}, follow_redirects=True)
print("CASE B ISOLATED — credit sale 5,000 then pay it, then delete the payment")
st("start")
c.post("/add_direct_sale", data={
  "sale_date":"2026-11-04T10:00","client_name":"Test Traders","client_code":"C1",
  "category":"Credit Customer","driver_name":"Driver 1",
  "product_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["10"],"unit_rate[]":["500"],
  "alternate_material[]":[""],"alternate_material_id[]":[""],"grn_item_id[]":[""],
  "ignore_booking_item[]":["1"],"paid_amount":"0","payment_method":"",
  "manual_bill_no":"SALE-B","idempotency_key":"sale-b"}, follow_redirects=True)
st("after credit sale 5,000")
c.post("/add_payment", data={"client_name":"Test Traders","client_code":"C1","amount":"5000","method":"Cash",
  "payment_account_id":aid,"date":"2026-11-05","manual_bill_no":"PAY-B","note":"","idempotency_key":"pay-b"}, follow_redirects=True)
st("after payment 5,000")
pid=q("SELECT id FROM payment ORDER BY id DESC LIMIT 1")[0][0]
c.post("/delete_transaction/Payment/%d"%pid, data={"reason":"wrong"}, follow_redirects=True)
o,cash = st("AFTER DELETING THE PAYMENT")
print()
print("   EXPECTED after deleting the payment: open_receivable = 5000.00, cash = 0.00")
print("   ACTUAL                             : open_receivable = %-8s cash = %s" % (o,cash))
print("   VERDICT:", "CORRECT" if abs(o-5000)<0.01 else "*** WRONG - the bill did not reopen ***")
print()
print("   pending bills now:", q("SELECT id, bill_no, amount, is_paid FROM pending_bill"))
print("   payments now     :", q("SELECT id, amount, is_void FROM payment"))
