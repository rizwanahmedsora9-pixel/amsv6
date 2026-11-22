from __future__ import annotations
"""Shared imports and module globals."""
"""HTTP routes: sales."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, Response, make_response, send_from_directory, abort, session
from flask_login import login_required, login_user, logout_user, current_user
from datetime import datetime, date, timedelta
from sqlalchemy import func, case, text, or_, and_, exists, not_
from types import SimpleNamespace
from decimal import Decimal, ROUND_HALF_UP
import os, io, json, re, logging, calendar

from models import *
from app.services.api import *  # noqa
from utils.audit import audit_log


def resolve_open_cement_item(service_id=None, material_id=None, typed_text=None,
                             *, require_active=True, allow_inactive_names=None,
                             keg_qty=None, keg_kg=None):
    """Resolve a sale line, then derive its KG quantity for Open Cement.

    Open Cement is stocked and sold in KG, but cashiers enter it by the keg.
    When the posted line resolves to an Open Cement item, ``keg_qty[]`` x
    ``keg_kg[]`` overrides the KG field so the stored/deducted/costed
    quantity is always kilograms.  Non-Open-Cement lines are untouched and a
    keg helper on them is ignored, so this can never corrupt a bagged sale.
    """
    from app.services.open_cement import DEFAULT_BAG_WEIGHT_KG, is_open_cement_material

    kind, obj = resolve_transaction_item(
        service_id=service_id,
        material_id=material_id,
        typed_text=typed_text,
        require_active=require_active,
        allow_inactive_names=allow_inactive_names,
    )
    if obj is None or kind != 'material' or not is_open_cement_material(obj):
        return kind, obj

    def _as_float(value):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    kegs = _as_float(keg_qty)
    per_keg = _as_float(keg_kg)
    if kegs is None and per_keg is None:
        return kind, obj
    # A keg quantity without a positive per-keg weight is a data-entry slip,
    # not an instruction to default silently; the caller keeps the KG field.
    if kegs is None or kegs <= 0:
        return kind, obj
    if per_keg is None or per_keg <= 0:
        per_keg = DEFAULT_BAG_WEIGHT_KG
    obj._keg_derived_kg = round(kegs * per_keg, 4)
    return kind, obj

from itertools import zip_longest
from sqlalchemy.orm import selectinload
from app.services.constants import (
    AUTO_BILL_NAMESPACES,
    AUTO_BILL_NS_DEFAULT,
    SALE_CATEGORY_CHOICES,
    OPEN_KHATA_CODE,
    OPEN_KHATA_NAME,
    ENDPOINT_PERMISSION_MAP,
)
from models import MaterialCategory


bp = Blueprint('sales', __name__)


