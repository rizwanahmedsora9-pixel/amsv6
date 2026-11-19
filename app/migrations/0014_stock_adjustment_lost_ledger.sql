-- 0014_stock_adjustment_lost_ledger.sql
--
-- Lost / damaged / surplus stock adjustment ledger (StockAdjustment).
-- Additive and idempotent: CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS only.

CREATE TABLE IF NOT EXISTS stock_adjustment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference VARCHAR(50) NOT NULL UNIQUE,
    adjustment_date VARCHAR(20) NOT NULL,
    adjustment_time VARCHAR(20) DEFAULT '00:00:00',
    date_posted DATETIME,
    material VARCHAR(100) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    qty FLOAT NOT NULL DEFAULT 0,
    system_qty FLOAT,
    physical_qty FLOAT,
    diff_qty FLOAT NOT NULL DEFAULT 0,
    reason VARCHAR(80) NOT NULL DEFAULT 'Damage / Broken',
    note VARCHAR(500) DEFAULT '',
    unit_cost FLOAT DEFAULT 0.0,
    total_value FLOAT DEFAULT 0.0,
    cost_source VARCHAR(200) DEFAULT '',
    source VARCHAR(40) DEFAULT 'lost_ledger',
    entry_id INTEGER REFERENCES entry(id),
    created_by VARCHAR(80),
    updated_by VARCHAR(80),
    created_at DATETIME,
    updated_at DATETIME,
    is_void BOOLEAN DEFAULT 0,
    void_reason VARCHAR(300),
    voided_by VARCHAR(80),
    voided_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_stock_adjustment_reference       ON stock_adjustment(reference);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_date            ON stock_adjustment(adjustment_date);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_date_posted     ON stock_adjustment(date_posted);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_material        ON stock_adjustment(material);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_direction       ON stock_adjustment(direction);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_reason          ON stock_adjustment(reason);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_entry_id        ON stock_adjustment(entry_id);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_is_void         ON stock_adjustment(is_void);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_created_at      ON stock_adjustment(created_at);
