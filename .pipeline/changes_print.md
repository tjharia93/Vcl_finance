# Petty Cash Weekly Sheet — native ERPNext Print Format

**Date:** 2026-06-20

## What was built
A standard, app-owned Jinja Print Format for the `Petty Cash Sheet` DocType, so the
A4 weekly filing copy prints straight from the Desk / Compass print button (and PDFs
via the print API). Replicates the intranet filing layout (`filing.html`) and the
already-ported repo `www/petty-cash/print.html`.

## Files created
- `vcl_finance/petty_cash/print_format/__init__.py` (empty)
- `vcl_finance/petty_cash/print_format/petty_cash_weekly_sheet/__init__.py` (empty)
- `vcl_finance/petty_cash/print_format/petty_cash_weekly_sheet/petty_cash_weekly_sheet.json`

Print Format **name: "Petty Cash Weekly Sheet"** (scrubs to `petty_cash_weekly_sheet`,
matching the folder — required by Frappe's standard-format sync).

## JSON shape (so migrate picks it up)
Mirrors an existing app-owned standard format (`vcl_quote_v1`):
- `"doctype": "Print Format"`, `"doc_type": "Petty Cash Sheet"`
- `"standard": "Yes"`, `"print_format_type": "Jinja"`, `"custom_format": 1`
- `"module": "Petty Cash"` (a real module in this app's `modules.txt`)
- full template lives in the `html` field; CSS is inlined inside the html via `<style>`

**Why it migrates:** `frappe/model/sync.py` `IMPORTABLE_DOCTYPES` includes
`("printing", "print_format")`. On `bench migrate`, `sync_for` walks
`<module>/print_format/<docname>/<docname>.json` and imports each one. Our folder layout
matches exactly. (`import_file.py` keeps `disabled` on re-import, so a manually disabled
format won't be re-enabled by migrate — expected.)

**Rendering path:** with `custom_format: 1` + `print_format_type: Jinja`,
`printview.py` renders the `html` field directly through Jinja with the real `doc`
object in context (confirmed in frappe source).

## How it iterates the child tables
A standard Print Format has **no Python `get_context`** — all summary maths is done in
Jinja using `namespace()` accumulators (the `www` page did this in `print.py`):
- `doc.vouchers` → category totals (TG..OT), cash-in, PC/ETR counts, rows-used; amber
  "missing" row when a voucher has OUT spend but neither PC nor ETR ticked.
- `doc.parking_entries` → per-vehicle / per-day grid + totals (day→vehicle→slot dict
  built in Jinja).
- `doc.misc_entries` → split by `kind` ("Bike Fuel" / "Forklift") via `selectattr`.
- `doc.wages_entries` / `doc.loan_entries` → wages total + loans-issued total.
- Week dates derived with `frappe.utils.add_days(doc.week_ending, -4..+1)`.
- Reconciliation block reads the controller-computed `doc.total_out`,
  `doc.expected_close`, `doc.variance` directly (don't re-derive).

4 page sections (page-break-before): (1) Reconciliation & Summary cover,
(2) Voucher Register, (3) Vehicle Expenses, (4) Wages & Loans + sign-off.
Brand navy `#1D2766` title bars / blue `#2B3990` headers; `@page { size: A4 landscape }`.

## Validated
- JSON parses; `name` scrubs to folder; module/doc_type correct.
- Jinja template parses (`jinja2.Environment().parse`).
- Functional render against a mock populated doc (totals, amber row, dates all correct)
  AND against an empty doc (no errors).

## Risky / watch
- **Not run through `bench migrate`** (per instruction) — first real migrate is the true
  test. Layout + JSON shape verified against frappe source, so it should sync clean.
- `custom_format: 1` is the key flag — without it Frappe would try the block builder and
  ignore our html. Set correctly.
- Standard print format JSON intentionally omits letterhead; output is self-contained html.
- The format is **landscape A4** like the intranet original (the repo `print.html` is also
  landscape). The brief said "A4" generically; kept landscape to preserve the wide voucher
  grid. Flip `@page size` to portrait if a portrait filing copy is wanted.
