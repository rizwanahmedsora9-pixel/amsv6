"""CSRF probe — must run in a process where AMS_CSRF_ALWAYS is set before the app is imported."""
import sys
sys.path.insert(0, "tools")
import harness as H          # the flag is read while the app is created
import r2lib as L

c = H.app.test_client()
c.post("/login", data={"username": "Admin", "password": "Admin@fbm12345"})
before = L.f(H.scalar("SELECT COUNT(*) FROM payment"))
bad = c.post("/add_payment", data={"client_code": "AC02", "client_name": "R2-CLI-02",
                                   "amount": "1", "payment_type": "Receipt", "method": "Cash",
                                   "payment_account_id": "1", "date": L.TODAY})
after = L.f(H.scalar("SELECT COUNT(*) FROM payment"))
verdict = "PASS" if bad.status_code == 400 and after == before else "FAIL"
print(f"{verdict} status={bad.status_code} rows {before}->{after}")
