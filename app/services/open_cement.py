"""Reserved "Open Cement" inventory category and item helpers.

The category is system-managed: it is created idempotently at bootstrap and
must never be renamed, disabled or deleted through the normal category
management screens.  Individual open-cement items are ordinary
:class:`models.Material` rows created through the existing Material Brands
screen; membership of this category is what gives them the open-cement rules
(KG stock, no GRN, explicit link back to the bagged cement item).

Nothing here hard-codes a brand or an item name.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func

from models import db, Material, MaterialCategory
from app.services.lookups import _material_norm_key

LOG = logging.getLogger(__name__)

OPEN_CEMENT_CATEGORY_NAME = 'Open Cement'
DEFAULT_BAG_WEIGHT_KG = 50.0


def _norm(value) -> str:
    return (value or '').strip().lower()


def _category_is_open(cat) -> bool:
    return bool(cat) and _norm(getattr(cat, 'name', '')) == _norm(OPEN_CEMENT_CATEGORY_NAME)


def open_cement_category() -> Optional[MaterialCategory]:
    """Return the reserved category row, or ``None`` if it does not exist yet."""
    return MaterialCategory.query.filter(
        func.lower(func.trim(MaterialCategory.name)) == _norm(OPEN_CEMENT_CATEGORY_NAME)
    ).first()


def ensure_open_cement_category() -> MaterialCategory:
    """Create the reserved Open Cement category if missing (idempotent).

    Re-running never creates a duplicate and never deactivates an existing
    row.  Safe to call on every bootstrap.
    """
    cat = open_cement_category()
    if cat is None:
        cat = MaterialCategory(name=OPEN_CEMENT_CATEGORY_NAME, is_active=True)
        db.session.add(cat)
        db.session.flush()
        LOG.info('Open Cement category initialised (id=%s)', cat.id)
    elif not cat.is_active:
        # A reserved category is never allowed to disappear from the masters.
        cat.is_active = True
        db.session.flush()
    return cat


def is_open_cement_category(cat) -> bool:
    return _category_is_open(cat)


def is_open_cement_material(mat) -> bool:
    """True when *mat* belongs to the reserved Open Cement category."""
    return _category_is_open(getattr(mat, 'category', None))


def material_by_name(value) -> Optional[Material]:
    """Resolve a material by exact name/code (case/space-insensitive)."""
    raw = (value or '').strip()
    if not raw:
        return None
    mat = Material.query.filter(
        func.lower(func.trim(Material.name)) == raw.lower()
    ).first()
    if mat is None:
        mat = Material.query.filter(
            func.lower(func.trim(Material.code)) == raw.lower()
        ).first()
    if mat is None:
        # Normalised fallback (ignores punctuation) for a manipulated request
        # that still spells one of the open-cement item names.
        key = _material_norm_key(raw)
        if key:
            for candidate in Material.query.all():
                if _material_norm_key(candidate.name) == key:
                    return candidate
    return mat


def is_open_cement_material_name(value) -> bool:
    """True when the submitted name resolves to an Open Cement item."""
    return is_open_cement_material(material_by_name(value))


def open_cement_materials() -> list:
    """All active Open Cement items (ordered by name)."""
    cat = open_cement_category()
    if cat is None:
        return []
    return Material.query.filter(
        Material.category_id == cat.id,
        Material.is_active == True,  # noqa: E712 - SQLAlchemy boolean filter
    ).order_by(Material.name.asc()).all()


def source_material_for(open_mat) -> Optional[Material]:
    """The bagged item an Open Cement item is linked to (explicit FK)."""
    if not is_open_cement_material(open_mat):
        return None
    source_id = getattr(open_mat, 'source_material_id', None)
    if not source_id:
        return None
    return db.session.get(Material, int(source_id))


def linked_open_materials(source_mat) -> list:
    """Open Cement items explicitly linked to *source_mat*."""
    if source_mat is None:
        return []
    return Material.query.filter(
        Material.source_material_id == int(source_mat.id),
        Material.is_active == True,  # noqa: E712
    ).order_by(Material.name.asc()).all()


def source_bag_weight_kg(source_mat) -> float:
    """Bag weight for a source item: its own setting, else the default."""
    value = getattr(source_mat, 'bag_weight_kg', None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else DEFAULT_BAG_WEIGHT_KG


def source_materials_for_conversion() -> list:
    """Materials that may be converted: active, not services, not Open Cement."""
    from app.services.lookups import is_service_name

    cat = open_cement_category()
    open_id = cat.id if cat else None
    rows = []
    for mat in Material.query.filter(Material.is_active == True).order_by(Material.name.asc()).all():  # noqa: E712
        if is_service_name(mat.name):
            continue
        if open_id is not None and mat.category_id == open_id:
            continue
        if is_open_cement_material(mat):
            continue
        rows.append(mat)
    return rows


def protect_open_cement_category(cat) -> Optional[str]:
    """Return a refusal message when *cat* is the reserved category, else None.

    Used by the category rename / toggle screens so the reserved category can
    never be renamed, disabled or removed by accident.
    """
    if _category_is_open(cat):
        return 'The "Open Cement" category is system-managed and cannot be renamed or disabled.'
    return None
