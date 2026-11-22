-- 0018_cement_bag_to_open_cement.sql
--
-- Cement Bag -> Open Cement (KG) internal inventory conversion.
--
-- Additive and idempotent only: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
-- EXISTS and guarded ALTER TABLE ADD COLUMN.  No DROP, no DELETE, no table
-- rebuild, no historical row rewrite.  Safe to re-run.
--
-- New ORM tables (cement_conversion / cement_conversion_layer) are created by
-- db.create_all() on boot; the statements here keep an upgraded database
-- identical to a freshly bootstrapped one.
--
-- The ALTER TABLE statements are the migration's record of the new columns.
-- On a fresh database db.create_all() + schema._ensure_model_columns() have
-- already added them, so "duplicate column name" is expected and ignored by the
-- runner (it replays the rest of the file statement by statement).

CREATE TABLE IF NOT EXISTS cement_conversion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference VARCHAR(50) NOT NULL UNIQUE,
    conversion_date VARCHAR(20) NOT NULL,
    conversion_time VARCHAR(20) DEFAULT '00:00:00',
    date_posted DATETIME,
    source_material_id INTEGER REFERENCES material(id),
    source_material VARCHAR(100) NOT NULL,
    source_unit VARCHAR(20) DEFAULT 'Bags',
    bags_converted FLOAT NOT NULL DEFAULT 0,
    weight_per_bag_kg FLOAT NOT NULL DEFAULT 50,
    kg_produced FLOAT NOT NULL DEFAULT 0,
    source_stock_after FLOAT,
    dest_stock_after FLOAT,
    dest_material_id INTEGER REFERENCES material(id),
    dest_material VARCHAR(100) NOT NULL,
    dest_unit VARCHAR(20) DEFAULT 'KG',
    source_cost_per_bag FLOAT DEFAULT 0,
    source_cost_per_kg FLOAT DEFAULT 0,
    total_transferred_cost FLOAT DEFAULT 0,
    cost_basis VARCHAR(400) DEFAULT '',
    source_entry_id INTEGER REFERENCES entry(id),
    dest_entry_id INTEGER REFERENCES entry(id),
    reversal_entry_id INTEGER REFERENCES entry(id),
    status VARCHAR(20) DEFAULT 'POSTED',
    note VARCHAR(500) DEFAULT '',
    idempotency_key VARCHAR(64) UNIQUE,
    created_by VARCHAR(80),
    updated_by VARCHAR(80),
    created_at DATETIME,
    updated_at DATETIME,
    reversed_at DATETIME,
    reversed_by VARCHAR(80),
    reverse_reason VARCHAR(300)
);

CREATE INDEX IF NOT EXISTS ix_cement_conversion_reference        ON cement_conversion(reference);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_conversion_date ON cement_conversion(conversion_date);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_date_posted     ON cement_conversion(date_posted);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_source_material ON cement_conversion(source_material_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_dest_material   ON cement_conversion(dest_material_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_source_entry    ON cement_conversion(source_entry_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_dest_entry      ON cement_conversion(dest_entry_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_reversal_entry  ON cement_conversion(reversal_entry_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_status          ON cement_conversion(status);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_idempotency     ON cement_conversion(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_created_at      ON cement_conversion(created_at);

CREATE TABLE IF NOT EXISTS cement_conversion_layer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversion_id INTEGER NOT NULL REFERENCES cement_conversion(id),
    grn_item_id INTEGER REFERENCES grn_item(id),
    grn_id INTEGER REFERENCES grn(id),
    reference VARCHAR(50) DEFAULT '',
    supplier VARCHAR(100) DEFAULT '',
    lot_date VARCHAR(20) DEFAULT '',
    source_qty_bags FLOAT DEFAULT 0,
    source_unit_cost FLOAT DEFAULT 0,
    kg_qty FLOAT DEFAULT 0,
    kg_unit_cost FLOAT DEFAULT 0,
    cost_amount FLOAT DEFAULT 0,
    is_void BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_cement_conversion_layer_conversion ON cement_conversion_layer(conversion_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_layer_grn_item   ON cement_conversion_layer(grn_item_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_layer_grn        ON cement_conversion_layer(grn_id);
CREATE INDEX IF NOT EXISTS ix_cement_conversion_layer_is_void    ON cement_conversion_layer(is_void);

-- material: explicit bagged -> Open Cement link + bag weight (kg).
ALTER TABLE material ADD COLUMN source_material_id INTEGER REFERENCES material(id);
ALTER TABLE material ADD COLUMN bag_weight_kg FLOAT;

CREATE INDEX IF NOT EXISTS ix_material_source_material_id ON material(source_material_id);

-- stock_adjustment: preserve the conversion reference an Open Cement loss was
-- costed from.
ALTER TABLE stock_adjustment ADD COLUMN conversion_reference VARCHAR(50);
CREATE INDEX IF NOT EXISTS ix_stock_adjustment_conversion_reference ON stock_adjustment(conversion_reference);
