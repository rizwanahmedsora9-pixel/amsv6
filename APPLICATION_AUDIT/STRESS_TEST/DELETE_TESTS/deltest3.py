"""Harder delete cases: separate payment, and deleting a payment that settled bills."""
import os, sys, sqlite3
DB=os.environ.get("PROOF_DB", "/tmp/ams_proof_del3.db")
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
def state(tag):
    r=dict(
      booking=q("SELECT COUNT(*) FROM booking")[0][0],
      payment=q("SELECT COUNT(*) FROM payment")[0][0],
      ledger=q("SELECT COUNT(*) FROM account_transaction")[0][0],
      pb=q("SELECT COUNT(*) FROM pending_bill")[0][0],
      open_amt=q("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0")[0][0],
      cash=q("SELECT ROUND(COALESCE(balance,0),2) FROM account LIMIT 1")[0][0],
    )
    print("   %-34s booking=%s payment=%s ledger=%s pb=%s open=%s cash=%s" % (tag, r['booking'],r['payment'],r['ledger'],r['pb'],r['open_amt'],r['cash']))
    return r

print("="*78); print("CASE A — booking 5000, 2000 paid now, then a SEPARATE 2000 payment, delete booking"); print("="*78)
s0=state("start")
c.post("/add_booking", data={"client_code":"C1","client_name":"Test Traders","date":"2026-11-02",
  "material_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["10"],"unit_rate[]":["500"],
  "amount":"5000","paid_amount":"2000","payment_method":"Cash","receive_in_account_id":aid,
  "manual_bill_no":"BOOK-A"}, follow_redirects=True)
state("after booking 5000 (2000 paid now)")
c.post("/add_payment", data={"client_name":"Test Traders","client_code":"C1","amount":"2000","method":"Cash",
  "payment_account_id":aid,"date":"2026-11-03","manual_bill_no":"PAY-A","note":"","idempotency_key":"pay-a"}, follow_redirects=True)
state("after separate payment 2000")
bid=q("SELECT id FROM booking ORDER BY id DESC LIMIT 1")[0][0]
c.post("/delete_transaction/Booking/%d"%bid, data={"reason":"wrong entry"}, follow_redirects=True)
sa=state("AFTER DELETE BOOKING")
print("\n   residue after deleting the booking:")
print("     payment rows left :", q("SELECT id, amount, client_name FROM payment"))
print("     ledger rows left  :", q("SELECT id, amount, note FROM account_transaction"))
print("     pending left      :", q("SELECT id, bill_no, amount FROM pending_bill"))
print("     -> payable-to-client / dangling cash:", sa['cash']-s0['cash'], "| open receivable:", sa['open_amt'])

print()
print("="*78); print("CASE B — credit sale 5000, paid 5000, then DELETE THE PAYMENT"); print("="*78)
b0=state("start")
c.post("/add_direct_sale", data={
  "sale_date":"2026-11-04T10:00","client_name":"Test Traders","client_code":"C1",
  "category":"Credit Customer","driver_name":"Driver 1",
  "product_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["10"],"unit_rate[]":["500"],
  "alternate_material[]":[""],"alternate_material_id[]":[""],"grn_item_id[]":[""],
  "ignore_booking_item[]":["1"],"paid_amount":"0","payment_method":"",
  "manual_bill_no":"SALE-B","idempotency_key":"sale-b"}, follow_redirects=True)
state("after credit sale 5000")
c.post("/add_payment", data={"client_name":"Test Traders","client_code":"C1","amount":"5000","method":"Cash",
  "payment_account_id":aid,"date":"2026-11-05","manual_bill_no":"PAY-B","note":"","idempotency_key":"pay-b"}, follow_redirects=True)
state("after payment 5000")
pid=q("SELECT id FROM payment ORDER BY id DESC LIMIT 1")[0][0]
c.post("/delete_transaction/Payment/%d"%pid, data={"reason":"wrong"}, follow_redirects=True)
sb=state("AFTER DELETE PAYMENT")
print("\n   should be back to the un-paid credit-sale state:")
print("     expected open receivable: 5000.00   actual:", sb['open_amt'])
print("     expected cash           : %.2f   actual: %s" % (b0['cash'], sb['cash']))
print("     payment rows left:", q("SELECT COUNT(*) FROM payment")[0][0])
print("     ledger rows left :", q("SELECT id, amount, note FROM account_transaction"))
