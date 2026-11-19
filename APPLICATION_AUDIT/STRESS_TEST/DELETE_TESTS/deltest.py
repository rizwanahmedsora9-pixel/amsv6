"""Owner scenario: delete a mistaken bill and prove NOTHING is left behind."""
import os, sys, sqlite3, json
DB=os.environ.get("PROOF_DB", "/tmp/ams_proof_del.db")
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
        balance=0.0, balance_minor=0, opening_balance=1000000.0, opening_balance_minor=100000000, is_active=True))
    _db.session.commit()
    mid=str(Material.query.first().id); sid=str(Supplier.query.first().id); aid=str(Account.query.first().id)

WATCH = ["direct_sale","direct_sale_item","booking","booking_item","pending_bill","entry",
         "account_transaction","payment","invoice","delivery_rent","sale_delivery_persons",
         "booking_allocation","grn","grn_item","material","client","audit_log","reminder","contact_log"]
def snap():
    con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
    out={}
    for t in WATCH:
        try:
            n=con.execute("SELECT COUNT(*) FROM %s"%t).fetchone()[0]
        except Exception:
            n=None
        out[t]=n
    out["cash_balance"]=con.execute("SELECT balance_minor FROM account LIMIT 1").fetchone()[0]
    out["material_total"]=con.execute("SELECT total FROM material LIMIT 1").fetchone()[0]
    out["open_receivable"]=con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM pending_bill WHERE COALESCE(is_void,0)=0 AND is_paid=0").fetchone()[0]
    con.close(); return out

def diff(a,b,label):
    print("\n--- %s ---" % label)
    bad=False
    for k in a:
        if a[k]!=b[k]:
            print("   %-24s %-12s -> %-12s" % (k, a[k], b[k]))
            bad=True
    if not bad: print("   (no change in any watched table)")
    return not bad

print("="*72); print("OWNER SCENARIO 1 — CASH SALE WITH MONEY RECEIVED, THEN DELETE"); print("="*72)
c.post("/grn", data={"action":"add","supplier":"Supplier A","supplier_id":sid,"date":"2026-11-01",
    "time":"09:00","manual_bill_no":"G1","mat_name[]":["OPC Cement"],"qty[]":["100"],
    "price[]":["500"],"tax_type":"none"}, follow_redirects=True)
base=snap()
print("  starting state:", {k:base[k] for k in ("direct_sale","cash_balance","material_total","open_receivable")})
c.post("/add_direct_sale", data={
  "sale_date":"2026-11-02T10:00","client_name":"Test Traders","client_code":"C1",
  "category":"Cash","driver_name":"Driver 1",
  "product_name[]":["OPC Cement"],"material_id[]":[mid],"qty[]":["2"],"unit_rate[]":["500"],
  "alternate_material[]":[""],"alternate_material_id[]":[""],"grn_item_id[]":[""],
  "ignore_booking_item[]":["1"],"paid_amount":"1000","payment_method":"Cash",
  "payment_account_id":aid,"manual_bill_no":"SALE-1","idempotency_key":"sale-1"}, follow_redirects=True)
after_sale=snap()
diff(base, after_sale, "after cash sale of 1000, fully paid")
con=sqlite3.connect("file:%s?mode=ro"%DB, uri=True)
sale_id=con.execute("SELECT id FROM direct_sale ORDER BY id DESC LIMIT 1").fetchone()[0]
con.close()
r=c.post("/delete_transaction/DirectSale/%d"%sale_id, data={"reason":"test"}, follow_redirects=True)
print("\n  DELETE DirectSale id=%d -> HTTP %s" % (sale_id, r.status_code))
after_del=snap()
same = diff(after_sale, after_del, "after deleting the sale")
print("\n  RESULT: back to the pre-sale state cleanly? %s" % ("YES" if all(
    after_del[k]==base[k] for k in ("direct_sale","direct_sale_item","entry","account_transaction",
                                    "payment","pending_bill","invoice","cash_balance","material_total","open_receivable")) else "NO"))
print("  residue:", {k:(base[k], after_del[k]) for k in base if base[k]!=after_del[k]})
