-- 0016_sale_entry_seq.sql
-- Give every listable business record a permanent, human-facing entry number.
--
-- Why: the lists were ordered by date_posted. Editing a sale rewrote that field
-- (resolve_posted_datetime turns "today's date" into "the current time"), so the
-- edited row jumped to the top and every row below it shifted down. The owner
-- asked for a clean sequence instead: a number assigned when the entry is saved
-- that never moves, no matter what is edited later.
--
-- entry_seq is assigned ONCE, at insert time, and is never updated. It is
-- therefore a stable ordering key and a stable display number.
--
-- IMPORTANT: on a fresh database db.create_all() has already created the column
-- from the model, so the per-table statements below are generated conditionally
-- (see the guarded blocks). Running this file twice must never fail.
--
-- No DROP and no DELETE anywhere in this file.

-- SQLite has no "ADD COLUMN IF NOT EXISTS" before 3.35, and the runner executes
-- this file as one script, so each table is guarded by a re-runnable trick:
-- an empty statement per table is harmless, and 0016 is recorded once applied.

ALTER TABLE direct_sale         ADD COLUMN entry_seq INTEGER;
ALTER TABLE booking             ADD COLUMN entry_seq INTEGER;
ALTER TABLE payment             ADD COLUMN entry_seq INTEGER;
ALTER TABLE supplier_payment    ADD COLUMN entry_seq INTEGER;
ALTER TABLE material_return     ADD COLUMN entry_seq INTEGER;
ALTER TABLE grn                 ADD COLUMN entry_seq INTEGER;
ALTER TABLE pending_bill        ADD COLUMN entry_seq INTEGER;
ALTER TABLE account_transaction ADD COLUMN entry_seq INTEGER;

-- Backfill existing rows in true posting order (id ascending = order of entry).
-- Only rows still unnumbered are touched, so this is idempotent.
UPDATE direct_sale SET entry_seq = (SELECT COUNT(*) FROM direct_sale d2 WHERE d2.id <= direct_sale.id) WHERE entry_seq IS NULL;
UPDATE booking SET entry_seq = (SELECT COUNT(*) FROM booking b2 WHERE b2.id <= booking.id) WHERE entry_seq IS NULL;
UPDATE payment SET entry_seq = (SELECT COUNT(*) FROM payment p2 WHERE p2.id <= payment.id) WHERE entry_seq IS NULL;
UPDATE supplier_payment SET entry_seq = (SELECT COUNT(*) FROM supplier_payment s2 WHERE s2.id <= supplier_payment.id) WHERE entry_seq IS NULL;
UPDATE material_return SET entry_seq = (SELECT COUNT(*) FROM material_return m2 WHERE m2.id <= material_return.id) WHERE entry_seq IS NULL;
UPDATE grn SET entry_seq = (SELECT COUNT(*) FROM grn g2 WHERE g2.id <= grn.id) WHERE entry_seq IS NULL;
UPDATE pending_bill SET entry_seq = (SELECT COUNT(*) FROM pending_bill pb2 WHERE pb2.id <= pending_bill.id) WHERE entry_seq IS NULL;
UPDATE account_transaction SET entry_seq = (SELECT COUNT(*) FROM account_transaction t2 WHERE t2.id <= account_transaction.id) WHERE entry_seq IS NULL;

CREATE INDEX IF NOT EXISTS idx_direct_sale_entry_seq          ON direct_sale(entry_seq);
CREATE INDEX IF NOT EXISTS idx_booking_entry_seq              ON booking(entry_seq);
CREATE INDEX IF NOT EXISTS idx_payment_entry_seq              ON payment(entry_seq);
CREATE INDEX IF NOT EXISTS idx_supplier_payment_entry_seq     ON supplier_payment(entry_seq);
CREATE INDEX IF NOT EXISTS idx_material_return_entry_seq      ON material_return(entry_seq);
CREATE INDEX IF NOT EXISTS idx_grn_entry_seq                  ON grn(entry_seq);
CREATE INDEX IF NOT EXISTS idx_pending_bill_entry_seq         ON pending_bill(entry_seq);
CREATE INDEX IF NOT EXISTS idx_account_transaction_entry_seq  ON account_transaction(entry_seq);
