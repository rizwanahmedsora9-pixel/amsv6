"""GRN linked-payment lookup.

The old soft-cancel path was removed with the immediate-delete policy:
GRN deletion is now a real delete handled by
``app.services.void_rebuild.hard_delete_transaction('GRN', id)``, which
reverses stock and posted effects in the same transaction.  This module only
keeps the shared helper used by that delete path to refuse deletion while
Accounts payments exist for the GRN.
"""
from __future__ import annotations

from models import SupplierPayment


def grn_linked_payments(grn):
    return SupplierPayment.query.filter(
        SupplierPayment.grn_id == grn.id,
        SupplierPayment.source_type == 'GRN_PAYMENT',
        SupplierPayment.is_void == False,  # noqa: E712
    ).all()


__all__ = ['grn_linked_payments']
