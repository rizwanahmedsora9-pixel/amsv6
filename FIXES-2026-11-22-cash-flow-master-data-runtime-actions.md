# Cash Flow master-data actions — browser submission fix

**Date:** 2026-11-22 · **Scope:** Categories, sub-categories and parties

## What users saw

Save, Disable, Restore and Delete on the Cash Flow **Master Data** rows appeared
not to do anything. The row stayed unchanged and no success/error message was
shown. In particular, a category such as Salaries can remain in place if it is
used by transaction history; that Delete control is intentionally unavailable,
but the old UI only explained this in a tooltip.

## Root cause

The shared `layout.html` form-submit handler locked every submit button as soon
as a form was submitted. Each Master Data row carries its operation in the
clicked button (`name="action" value="delete_category"`, for example). A
browser omits disabled controls when it serializes a form, so the server
received no `action`, did not run a CRUD handler, and could not flash a result.
The HTTP route tests had posted an explicit `action` field and therefore did not
exercise this browser-side failure.

## Fix

* Keep the clicked submitter enabled until the browser serializes the request;
  disable the other submit controls. The existing in-flight submit latch still
  blocks a second request.
* Redirect every Master Data action back to `#tab-master`, so the updated rows
  and server flash remain in view after the POST/redirect/GET cycle.
* Show **“In use — disable instead”** next to Delete when historical entries
  prevent deletion. This is visible without relying on a tooltip over a disabled
  button. Used masters retain history and should be disabled, not deleted.

No database/schema changes were needed. Regression coverage checks the shared
submit lock, the return tab, rendered feedback, and the visible in-use message.
