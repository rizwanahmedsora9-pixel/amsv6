AMS v5.2 — FULL APPLICATION, UPDATED 2026-11-18
================================================

WHAT THIS ZIP IS
  The complete AMS v5.2 application with every fix from the 2026-11-18 audit rounds already
  applied. Unzip it anywhere and run it — nothing else is needed except the Python packages in
  requirements-lock.txt.

  Status of this build: 86 of 86 audit tests pass (73/86 on the code before these fixes).
  As found: 73 PASS / 13 FAIL   ->   This build: 86 PASS / 0 FAIL


HOW TO RUN
  cd <unzipped folder>
  pip install -r requirements-lock.txt          # Python 3.11+
  python main.py                                # or: gunicorn wsgi:app

  The instance/ folder and the database file are created automatically on first start
  (SQLite at instance/ahmed_cement_v44_fresh.db, or point APP_DB_PATH at another file).
  Optional, for PDF exports:
  pip install -r requirements-pdf.txt

  This zip does NOT ship a database — see "your database" below.


WHAT WAS FIXED IN THIS BUILD (14 findings, all closed)
  HIGH    supplier payments from the Pay Supplier screen are now allocated to the GRN bills they
          pay, and overpaying a bill is refused with the amount that is actually still owed
  HIGH    voiding a bill that already had a material return reverses that return with it
          (no more phantom stock, no more credit for goods the shop already has back)
  HIGH    expense vouchers can be voided: money reversed, voucher kept and marked void
  HIGH    an anonymous visit to /logs redirects to the login page instead of raising a 500
  HIGH    a newly created user with no permissions sees a clear "no access yet" page instead of
          an endless redirect loop
  HIGH    a part-paid counter sale to a walk-in name registers that buyer, so the money owed
          appears in Current Payables and the client ledger
  MEDIUM  cancelled booking lines are kept (material, rate and cancelled quantity preserved)
  MEDIUM  a non-numeric opening balance is refused instead of being silently stored as zero
  MEDIUM  the P&L workbook states the revenue its own net profit is calculated from
  MEDIUM  Stock Summary shows the value of stock on hand at cost, with a total
  MEDIUM  the dashboard card is labelled "Net Client Dues" and also states the open-bill total
  LOW     material cost taken from real GRN lots is reported as recorded, not as an estimate
  INFO    PDF exports need the optional requirements-pdf.txt (deployment note, not a defect)
  INFO    cancelling a booking is client-wide LIFO by design — documented, unchanged

  Two new columns are added automatically on first start (idempotent migration):
    booking_item.cancelled_qty          keeps the ordered quantity on a cancelled booking line
    material_return.voided_with_sale    lets a restored bill restore exactly its own returns
  Nothing was removed or renamed. COGS and net profit are computed exactly as before.


YOUR DATABASE
  This zip deliberately contains no database, so it can never overwrite your live books.
  The original, untouched database handed over for auditing is kept here:
    _EXTRA_2026-11-18/database_backup/ahmed_cement_v44_fresh.db
  (md5 124f7a31809e32061d370f7c93e2d7d4 — identical to the day it was received)
  To use it, copy it into instance/ (or point APP_DB_PATH at it) BEFORE the first start, and
  take a backup first: the two new columns are added to it on that first start.


WHAT ELSE IS IN _EXTRA_2026-11-18
  audit/                          the complete 2026-11-18 audit trail, for your records:
    (root files)                  master audit report, bug registers, test results, financial
                                  reconciliation, route inventory (618 routes), fix notes
    audit/r2/                     the "as found" audit of the code before these fixes: report,
                                  14 findings, every row's expected-vs-actual evidence, 86 tests
                                  (73 PASS / 13 FAIL)
    audit/r3/                     the fix + verification report for THIS build, the patch file
                                  (FIXES_2026-11-18_R3.patch), 86 test results (all PASS), and
                                  the audit database those numbers were produced from
    audit/*/tools/                the audit scripts, so any result here can be re-run

  None of this is imported by the application. Deleting _EXTRA_2026-11-18 does not affect the
  app: keep it for the evidence, remove it for a clean deployment folder.


HOW THIS BUILD WAS VERIFIED
  The finished zip was extracted to an empty folder, given an empty database, and put through the
  full audit: 86 tests plus a sweep of all 313 GET routes.
    86 PASS / 0 FAIL, 313 routes fetched, 0 errors.
  Every headline figure was rebuilt independently from the database and compared with the screen:
  gross 39,500 / discounts 500 / net revenue 39,000 / COGS 25,468.75 / net profit 10,031.25 —
  exact to the paisa. Cash, stock, receivables, payables and both ledgers tie out.

  Nothing here has been applied to your live installation. To deploy, replace the program files
  (the database is separate), or apply _EXTRA_2026-11-18/audit/r3/FIXES_2026-11-18_R3.patch
  with `patch -p1`.
