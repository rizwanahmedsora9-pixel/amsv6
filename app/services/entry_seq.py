"""Permanent, never-changing entry numbers for listable business records.

Why this exists
---------------
Every list screen was ordered by ``date_posted``.  Editing a record rewrote
``date_posted``: ``resolve_posted_datetime`` turns "today's date" into *the
current time*, and turns any other bare date into *midnight*.  So an edited row
**jumped to the top of the list** and every row below it shifted down.  The
owner asked for a clean sequence — a number assigned when the record is created
that never moves, whatever is edited afterwards.

How it works
------------
``entry_seq`` is stamped by a SQLAlchemy ``before_insert`` listener: exactly
once, on the INSERT that creates the row.

There is deliberately **no ``before_update`` listener and no setter**.  Nothing
in the application can change an existing number, and the schema has no
mechanism to renumber.  That is the whole guarantee.

Numbers are allocated per table as ``MAX(entry_seq) + 1``.  This runs inside the
INSERT's own transaction on a single SQLite file, where writers are serialised,
so two concurrent inserts cannot read the same ``MAX``.  Even in the
pathological case they could, the outcome is a duplicated display number — a
cosmetic flaw — never a lost or deleted row.

Existing rows are numbered in true posting order by
``app/migrations/0016_sale_entry_seq.sql`` (``id`` ascending = order of entry).
"""
from __future__ import annotations

import logging

from sqlalchemy import event, func, select

LOG = logging.getLogger(__name__)

# (model name, table name) — explicit so a new model is never numbered by accident.
_SEQ_TABLES = (
    ("DirectSale", "direct_sale"),
    ("Booking", "booking"),
    ("Payment", "payment"),
    ("SupplierPayment", "supplier_payment"),
    ("MaterialReturn", "material_return"),
    ("GRN", "grn"),
    ("PendingBill", "pending_bill"),
    ("AccountTransaction", "account_transaction"),
)

_installed = False


def _stamp(mapper, connection, target):
    """``before_insert`` hook: give this row its permanent number if it has none."""
    if getattr(target, "entry_seq", None):
        return
    table = mapper.local_table.name
    current = connection.execute(
        select(func.max(func.coalesce(
            getattr(mapper.class_, "entry_seq"), 0)))
    ).scalar()
    if current is None or int(current) == 0:
        # Column empty (fresh or partially-migrated database): number from 1.
        count = connection.execute(
            select(func.count()).select_from(mapper.class_)
        ).scalar()
        target.entry_seq = int(count or 0) + 1
    else:
        target.entry_seq = int(current) + 1


def install_listeners() -> list[str]:
    """Attach the ``before_insert`` stamping listener to each listable model.

    Safe to call more than once: returns the list of models it actually
    attached to (empty on later calls).
    """
    global _installed
    if _installed:
        return []
    from models import (
        AccountTransaction,
        Booking,
        DirectSale,
        GRN,
        MaterialReturn,
        Payment,
        PendingBill,
        SupplierPayment,
    )

    classes = {
        "DirectSale": DirectSale,
        "Booking": Booking,
        "Payment": Payment,
        "SupplierPayment": SupplierPayment,
        "MaterialReturn": MaterialReturn,
        "GRN": GRN,
        "PendingBill": PendingBill,
        "AccountTransaction": AccountTransaction,
    }
    attached = []
    for name, table in _SEQ_TABLES:
        cls = classes.get(name)
        if cls is None:
            continue
        event.listen(cls, "before_insert", _stamp, propagate=False)
        attached.append(name)
    _installed = True
    LOG.info("entry_seq: permanent numbering installed on %s", ", ".join(attached))
    return attached


def highest_seq(table: str) -> int:
    """Current highest entry number for *table* (0 when empty).  Read-only."""
    from models import db
    row = db.session.execute(
        db.text('SELECT COALESCE(MAX(entry_seq), 0) FROM "%s"' % table)
    ).scalar()
    return int(row or 0)
