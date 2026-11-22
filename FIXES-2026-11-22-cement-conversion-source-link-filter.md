# Cement Conversion — "Source Cement Item" now lists only linked bagged items

**Date:** 2026-11-22 · **Scope:** Inventory → Convert Cement Bags to Open Cement
(`blueprints/inventory/cement_conversion.py`, `app/services/open_cement.py`,
`templates/cement_conversion.html`)

## What was reported

> The "Source Cement Item *" dropdown on the conversion screen offered every
> active bagged cement item, even ones with no Open Cement item linked to
> them. Such an item can never be converted — the destination dropdown would
> always be empty — so it is not a usable source.

## The change

* New helper **`sources_with_open_cement_links()`** in
  `app/services/open_cement.py`: bagged items that are active, not services,
  not Open Cement items, **and** are linked to at least one **active Open
  Cement category item** (via that item's `source_material_id`). Only links
  whose linked item belongs to the reserved "Open Cement" category count —
  a `source_material_id` pointing from a plain bagged item at another bagged
  item is not a link.
* The conversion route passes this restricted list to the template as
  **`conversion_source_materials`**, which the "Source Cement Item *"
  select renders from. A small helper line under the select now says that
  only linked bagged items are listed.
* Everything else is unchanged:
  * the history filter's "Source Cement" dropdown keeps offering **all**
    bagged sources, so history can still be filtered by a source that is no
    longer linked;
  * the Material Brands screen keeps using
    `source_materials_for_conversion()` for the "Linked Bagged Cement Item"
    dropdown, so an Open Cement item can still be linked to a source that
    has no link yet.

## Tests

`tests/test_cement_conversion.py` — new section **L. "Source Cement Item" only
lists bagged items linked to an Open Cement item** (7 tests):

| Test | Covers |
| --- | --- |
| `test_l_source_dropdown_lists_only_linked_bagged_items` | linked source listed, unlinked dropped |
| `test_l_source_dropdown_drops_source_when_open_cement_link_is_inactive` | inactive link does not count |
| `test_l_source_dropdown_drops_inactive_source_item` | inactive source never offered |
| `test_l_non_open_cement_row_link_does_not_count` | non-Open-Cement link ignored |
| `test_l_history_filter_keeps_all_bagged_sources` | history filter stays unrestricted |
| `test_l_materials_form_link_dropdown_still_lists_unlinked_bagged_items` | brands link list stays open |
| `test_l_service_items_are_never_conversion_sources` | rent-style services excluded |

Results: `pytest tests/test_cement_conversion.py` → **50 passed**.
Full suite: `pytest -q` → **141 passed, 5 skipped**.

## Deploy note

No schema change, no data repair — the filter is computed at render time.
Restart/redeploy the app so the new route and template are loaded.
