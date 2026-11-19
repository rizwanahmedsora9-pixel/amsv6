"""Sequential deletes: delete the payment that settled a bill, then the whole chain."""
import os, sys, sqlite3
DB=os.environ.get("PROOF_DB", "/tmp/ams_proof_seq.db")
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
def st(tag):
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
    d=dict(sale=con.execute("SELECT COUNT(*) FROM direct_sale").fetchone()[0],
           bk=con.execute("SELECT COUNT(*) FROM booking").fetchone()[0],
           pay=con.execute("SELECT COUNT(*) FROM payment").fetchone()[0],
           pb=con.execute("SELECT COUNT(*) FROM pending_bill WHERE COALESCE(is_void,0)=0").fetchone()[0],
           open=con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0").fetchone()[0],
           cash=round(con.execute("SELECT COALESCE(balance,0) FROM account LIMIT 1").fetchone()[0],2),
           stock=con.execute("SELECT total FROM material LIMIT 1").fetchone()[0])
    con.close()
    print("   %-38s sale=%s bk=%s pay=%s pb=%s open=%-9s cash=%-9s stock=%s" % (tag,d['sale'],d['bk'],d['pay'],d['pb'],d['open'],d['cash'],d['stock']))
    return d
def lastid(t):
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True); r=con.execute("SELECT MAX(id) FROM %s"%t).fetchone()[0]; con.close(); return r
c.post("/grn", data={"action":"add","supplier":"Supplier A","supplier_id":sid,"date":"2026-11-01",
  "time":"09:00","manual_bill_no":"G0","mat_name[]":["OPC Cement"],"qty[]":["500"],"price[]":["500"],
  "tax_type":"none"}, follow_redirects=True)
print("="*78); print("SEQUENCE 1 — credit sale 2,000 paid in full, delete payment, then delete sale"); print("="*78)
s0=st("start")
c.post("/add_direct_sale", data={"sale_date":"2026-11-02T10:00","client_name":"Test Traders","client_code":"C1",
  "category":"Credit Customer","driver_name":"Driver 1","product_name[]":["OPC Cement"],"material_id[]":[mid],
  "qty[]":["4"],"unit_rate[]":["500"],"alternate_material[]":[""],"alternate_material_id[]":[""],
  "grn_item_id[]":[""],"ignore_booking_item[]":["1"],"paid_amount":"0","payment_method":"",
  "manual_bill_no":"SEQP-1","idempotency_key":"seq-1"}, follow_redirects=True)
s1=st("after credit sale 2,000")
c.post("/add_payment", data={"client_name":"Test Traders","client_code":"C1","amount":"2000","method":"Cash",
  "payment_account_id":aid,"date":"2026-11-03","manual_bill_no":"SEQP-PAY","note":"",
  "idempotency_key":"seqp-1"}, follow_redirects=True)
s2=st("after payment 2,000 against that bill")
c.post("/delete_transaction/Payment/%d"%lastid("payment"), data={}, follow_redirects=True)
s3=st("AFTER DELETING THE PAYMENT")
print("      -> sale still there with full debt? %s (open should be 2000)" % ("YES" if abs(s3['open']-2000)<0.01 else "NO open=%s"%s3['open']))
c.post("/delete_transaction/DirectSale/%d"%lastid("direct_sale"), data={}, follow_redirects=True)
s4=st("AFTER DELETING THE SALE")
print("      -> back to the very start? %s" % ("YES" if s4==s0 else "NO  drift=%s" % {k:(s0[k],s4[k]) for k in s0 if s0[k]!=s4[k]}))

print()
print("="*78); print("SEQUENCE 2 — booking 3,000 (1,000 paid), pay 2,000, delete booking"); print("="*78)
b0=st("start")
c.post("/add_booking", data={"client_code":"C1","client_name":"Test Traders","date":"2026-11-03",
  "material_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["6"],"unit_rate[]":["500"],
  "amount":"3000","paid_amount":"1000","payment_method":"Cash","receive_in_account_id":aid,
  "manual_bill_no":"SEQ-BK"}, follow_redirects=True)
st("after booking 3,000 with 1,000 paid")
c.post("/add_payment", data={"client_name":"Test Traders","client_code":"C1","amount":"2000","method":"Cash",
  "payment_account_id":aid,"date":"2026-11-04","manual_bill_no":"SEQ-BKP","note":"",
  "idempotency_key":"seq-bkp"}, follow_redirects=True)
st("after separate payment 2,000")
c.post("/delete_transaction/Booking/%d"%lastid("booking"), data={}, follow_redirects=True)
b1=st("AFTER DELETING THE BOOKING")
resid={k:(b0[k],b1[k]) for k in b0 if b0[k]!=b1[k]}
print("      -> residue vs start: %s" % (resid if resid else "NONE"))
print("      -> the separate 2,000 payment must remain as a client credit:")
con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
print("        ", con.execute("SELECT id, amount, client_name FROM payment").fetchall())
con.close()
