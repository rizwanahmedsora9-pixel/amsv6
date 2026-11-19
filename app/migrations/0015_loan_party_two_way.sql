-- 0015_loan_party_two_way.sql
-- Two-way Loan Party register support (borrowing from and lending to parties).
-- Note: Removal of the legacy ck_loan_party_opening_nonnegative check
-- constraint on existing SQLite databases is handled safely and idempotently
-- during schema bootstrap (_ensure_loan_party_two_way_schema) so that table
-- rebuilds preserve all rows and foreign keys without destructive migration flags.

CREATE INDEX IF NOT EXISTS ix_loan_party_name ON loan_party (name);
CREATE INDEX IF NOT EXISTS ix_loan_party_is_active ON loan_party (is_active);
CREATE INDEX IF NOT EXISTS ix_loan_party_created_at ON loan_party (created_at);
CREATE INDEX IF NOT EXISTS ix_loan_movement_party_date ON loan_movement (loan_party_id, date_posted, id);
