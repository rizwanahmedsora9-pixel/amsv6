# Cement Conversion — linked Open Cement item never appears in the destination

**Date:** 2026-11-22 · **Scope:** Inventory → Convert Cement Bags to Open Cement
(`blueprints/inventory/cement_conversion.py`, `templates/cement_conversion.html`)

## What was reported

> I added a new material "Kohat Open Cement", linked it to Kohat in Material
> Brands, then selected Kohat as the source in the conversion tool — the
> destination dropdown only shows “-- Linked Open Cement item --” and the item
> never appears.

The Material Brands screen was correct: the Open Cement item existed, was
active, was in the reserved **Open Cement** category and its
`source_material_id` pointed at Kohat (the edit modal showed
“Kohat (Bags)” pre-selected). The defect was entirely in the conversion
screen's server/JS flow.

## What the reproduction found — two defects

The destination dropdown is filled by JavaScript from the **cost preview**
endpoint (`GET /inventory/cement_conversion/preview`), which returns
`linked_open`. That coupling broke in two different ways.

### 1. Empty quantity → preview refused → dropdown never filled

`loadLinkedOpen()` is called the moment a source is selected, when the
“Conversion Qty (bags)” box is still empty. It sent `bags=0`, and
`preview_conversion()` validates the quantity first:

```python
bags_f = _parse_positive(bags, 'Conversion quantity')
# -> ValueError: Conversion quantity must be greater than zero.
```

The route turned that into `400 {'ok': false, ...}`, and the JS did:

```js
if (!data.ok || !data.linked_open) return;   // dropdown stays empty
```

`loadLinkedOpen()` is only re-run when the **source changes**, so typing a
quantity later never retried it. The user was left with an empty destination
select and the misleading red alert “Conversion quantity must be greater than
zero.” (visible in the screenshot).

### 2. Even a valid quantity crashed the server

`cement_conversion_preview()` called `linked_open_materials(source)` without
importing it. The function is not exported by `._common`, so once the
quantity check passed the route raised:

```
NameError: name 'linked_open_materials' is not defined
blueprints/inventory/cement_conversion.py:156
```

i.e. a 500 on every successful preview. There was no code path in which the
dropdown could ever receive the linked item.

## The fix

* **New dedicated endpoint** `GET /inventory/cement_conversion/linked_open?source=<id>`
  returning only the Open Cement items linked to a source. It performs no
  quantity, stock or costing validation, so the dropdown loads as soon as a
  source is picked. Unknown source → 404; Open Cement used as a source → 400.
* **`linked_open_materials` imported** and the payload extracted into
  `_linked_open_payload()`.
* **The preview response now always carries `linked_open`**, including its
  400 validation-error response, so the client can fill the dropdown from
  either call.
* **Template JS:** `loadLinkedOpen()` calls the new endpoint and no longer
  aborts on a preview error; the preview's error branch fills the destination
  from `data.linked_open` as a fallback, shows a helpful info message when the
  only problem is the still-empty quantity, and reports the real
  “no item linked” case when the list is empty. The list is also loaded on
  page load if the browser restored a previously selected source.

## Tests

`tests/test_cement_conversion.py` — new section **K. Destination dropdown must
load independently of the cost preview** (5 tests):

| Test | Covers |
| --- | --- |
| `test_k_linked_open_list_loads_without_a_quantity` | new endpoint, empty quantity |
| `test_k_linked_open_endpoint_rejects_unknown_and_open_cement_sources` | 404 / 400 guards |
| `test_k_preview_always_carries_the_linked_list` | 400 payload + valid preview (NameError regression) |
| `test_k_conversion_screen_uses_the_linked_open_endpoint` | rendered screen references the new route |
| `test_k_materials_form_link_is_visible_on_the_conversion_screen` | full flow: Add Brand → link → dropdown |

Results: `pytest tests/test_cement_conversion.py` → **43 passed**.
Full suite: `pytest -q` → **133 passed, 5 skipped**.

## Deploy note

No schema change, no migration, no data repair — the already-created
“Kohat Open Cement” row and its link are correct. Restart/redeploy the app so
the new route and template are loaded.
