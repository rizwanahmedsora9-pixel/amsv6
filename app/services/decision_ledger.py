"""Gross booking material valuation for the decision ledger."""
from models import Material, MaterialCategory, Service


def _material_values(items, delivered_qty, allocated_by_item):
    """Value deliveries at their booking rates; estimate unallocated legacy rows FIFO.

    Quantities without a booking rate are left unpriced rather than inventing one.
    Values are gross line prices, before discounts.
    """
    booked_value = sum(float(i.qty or 0) * float(i.price_at_time or 0) for i in items)
    remaining_delivery = max(0.0, delivered_qty)
    delivered_value = 0.0
    used = {}
    for item in items:
        qty = min(max(0.0, allocated_by_item.get(item.id, 0)), remaining_delivery)
        used[item.id] = qty
        delivered_value += qty * float(item.price_at_time or 0)
        remaining_delivery -= qty
    # Older dispatches may not have allocation records. Use remaining booking lots FIFO.
    for item in items:
        qty = min(max(0.0, float(item.qty or 0) - used.get(item.id, 0)), remaining_delivery)
        delivered_value += qty * float(item.price_at_time or 0)
        remaining_delivery -= qty
    return booked_value, delivered_value, booked_value - delivered_value, remaining_delivery


def _new_category_group(key, category_id, name):
    return {
        'key': key,
        'id': category_id,
        'name': name,
        'materials': [],
        'booked': 0,
        'dispatched': 0,
        'remaining': 0,
        'booked_cost': 0.0,
        'dispatched_cost': 0.0,
        'remaining_cost': 0.0,
        'unpriced_qty': 0.0,
    }


def _qty_buckets(materials):
    """Per-unit quantity totals for one category.

    Quantities measured in different units must never be added together, so a
    category carries one bucket per distinct unit (case-insensitive; services
    and unknown names keep their own bucket).  A single bucket means the
    category can show one compatible total.
    """
    buckets = {}
    for item in materials:
        label = (item.get('unit') or '').strip()
        bucket = buckets.setdefault(label.lower(), {
            'label': label, 'booked': 0, 'dispatched': 0, 'remaining': 0,
        })
        for field in ('booked', 'dispatched', 'remaining'):
            bucket[field] += item.get(field) or 0
    return sorted(buckets.values(), key=lambda b: (b['label'].lower(), b['label']))


def build_category_summary(material_rows, norm_key):
    """Bucket the flat material summary under Material-Master categories.

    Presentation only: every figure on a group is the plain sum of the rows
    the caller already computed — this never re-prices, re-counts or
    re-allocates anything, so the All-Categories totals stay byte-identical
    to the flat summary.

    * Category and unit come from the masters, matched by the same normalised
      name key the caller used (booking/entry names are free text, no FKs).
    * Inactive masters still count, so historical rows keep their category.
    * Services (``Rent …``, ``Chaugath Labour``) carry no category by design
      and land in the ``Uncategorised`` group, as do names with no master row.
    * Every ``MaterialCategory`` row becomes a group, even one with no
      activity yet; ``Uncategorised`` is appended only when it has rows.
    """
    meta = {}
    for mat in Material.query.all():
        key = norm_key(mat.name)
        if key and key not in meta:
            meta[key] = (mat.category_id, (mat.unit or '').strip())
    for svc in Service.query.all():
        key = norm_key(svc.name)
        if key and key not in meta:
            meta[key] = (None, (svc.unit or '').strip())

    groups = {}
    for cat in MaterialCategory.query.all():
        key = f'cat-{cat.id}'
        groups[key] = _new_category_group(
            key, cat.id, (cat.name or '').strip() or f'Category {cat.id}')
    order = sorted(groups, key=lambda k: groups[k]['name'].lower())

    uncat = None
    for row in material_rows:
        cat_id, unit = meta.get(norm_key(row.get('name')), (None, ''))
        group = groups.get(f'cat-{cat_id}') if cat_id else None
        if group is None:
            if uncat is None:
                uncat = _new_category_group('uncat', None, 'Uncategorised')
                groups['uncat'] = uncat
                order.append('uncat')
            group = uncat
        item = dict(row)
        item['unit'] = unit
        item['group_key'] = group['key']
        group['materials'].append(item)
        for field in ('booked', 'dispatched', 'remaining', 'booked_cost',
                      'dispatched_cost', 'remaining_cost', 'unpriced_qty'):
            group[field] += row.get(field) or 0

    summary = [groups[k] for k in order]
    for group in summary:
        group['qty_buckets'] = _qty_buckets(group['materials'])
    return summary


