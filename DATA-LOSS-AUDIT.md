# "Data disappeared overnight" — code audit result

I traced every path in the code that can delete, rewrite, hide or reject data
without the user asking. Below is what I found, what I ruled out, and what I
changed.

## The one real bug found: identical sale lines were silently DROPPED

`_dedupe_direct_sale_items()` (`app/services/sales_core.py`) removed any sale
line whose **material + rate + booking flags + GRN lot** matched an earlier
line — it did not merge, it discarded. Verified:

| entered | stored before fix | flash shown |
|---|---|---|
| 5 + 5 (two loads, same material & rate) | **5** (one line) | "added successfully" |
| 5 + 5 | 10 (one line) | "added successfully" (after fix) |

So if you entered two loads/invoices on the same bill with the same material
and the same rate — e.g. two trucks of 5 bags, or the same item twice — the
second line **vanished without any warning**, and the bill, the stock movement
and the amount were all short. On a busy night with repeated materials and a
fixed rate this is exactly what "I entered it and it was gone" looks like.

Fixed by **merging** equal lines (quantities and any per-line discount are
added) instead of dropping them. Different rate / different lot / different
booking flag still stay separate lines, exactly as before. Both the add and the
edit path use it. Verified stock movement now matches: line qty 10, amount
12,000, stock OUT moved 10.

## Also fixed: a failed save could hang the user for 30+ seconds

When the database was busy, a refused save stayed on screen far too long, so a
cashier would assume a frozen page, close it, and the entry was gone:

* the write-lock wait ran 40 attempts × the driver's 30 s connect timeout —
  measured **636 seconds** against a held lock (longer than any web request);
* the audit-log write then blocked another 15 s (`busy_timeout`) on the same
  lock, on every failed POST.

Now the wait is a **hard ~8 s wall-clock budget** (cheap lock probe first, so it
really stops at 8 s), the audit write uses a 1.5 s timeout and never blocks the
user, and the message says plainly: *"The database was busy for 8 seconds … so
this one was not saved. Nothing was changed and your entry is kept in the form
— press Save again."* Refused save: **19.8 s → 6.3 s**.

## What I checked and RULED OUT (no bug)

| Path | Result |
|---|---|
| Nightly/scheduled jobs | Only create backups and clean temp files. No delete, no date-based purge. |
| Boot-time "void purge" that deletes rows | **Not called anywhere** — only a read-only count that logs a warning. |
| Fresh DB created at boot over your data | Only when the file is missing/0 bytes; an existing DB is never recreated. |
| Database swapped by `wsgi.py` | No — a fixed path, nothing silently reselects another file. |
| Restore from backup | POST-only, from the Backups screen. |
| A shrinking DB being ignored | The health guard **refuses to start** (does not silently continue) when rows drop by ≥50 or ≥20%. |
| Duplicate save overwriting another | Same bill number → refused ("already exists"); same idempotency key → no overwrite. |
| Two cashiers saving at once | 4/4 saved, 3 s, no loss. |
| Validation failure (missing driver, unknown material) | Clear message, nothing deleted. |
| Date handling ("entered at 23:30") | Stored exactly as entered; **no** timezone shift. |
| Sale list date filter | None — the list is not date-limited. |

## One thing that looked like a bug but is not

Your morning list starts at today's date only if the data really is older.
Rows are ordered by **transaction date, newest first**, so a sale entered for an
*older* date (or a backdated bill) sits further down the list — it is not lost,
it is just below the newest rows. If you enter a bill with an older date, use
the date/material/bill filters or open the client ledger to see it on top.

## Still worth doing on the server

The database lock is still the root of the "database is busy" family of
problems. Two operational fixes remain outside the code: switching the
PythonAnywhere site to **WAL** if your plan allows it, and paginating `/grn` and
`/ledger/2`, which are very heavy pages. The sale-path lock time is already down
from ~2.2 s to ~0.9 s per save.
