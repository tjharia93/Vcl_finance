# VCL Finance

Frappe custom app for Vimit Converters Limited's finance workflows. Currently scopes the **Petty Cash** module — a digital replacement for the weekly handwritten petty cash book.

## Modules

### Petty Cash

A weekly petty-cash sheet (Mon–Sat) with four sections:

1. **Voucher Register** — 18 rows per page × N continuation sheets. Each row captures recipient, voucher no, six category codes (TG/TE/SE/OA/CM/OT), cash-in refunds, PC/ETR ticks.
2. **Vehicle Parking** — 6 days × 5 vehicles × 2 entries per day.
3. **Bike Fuel & Forklift** — flat numbered rows.
4. **Wages & Loans** — 18 rows. The recipient signature on this sheet is the supporting evidence (no separate PC slip).

Plus an end-of-week **Reconciliation & Summary** page.

#### DocTypes
- `Petty Cash Sheet` (parent, Submittable, autonamed `PCS-YYYY-####`)
- `Petty Cash Voucher` (child table)
- `Petty Cash Parking Entry` (child table)
- `Petty Cash Misc Entry` (child table — Bike Fuel / Forklift)
- `Petty Cash Wages Entry` (child table)
- `Petty Cash Category` (master — seeded automatically on install)

#### Website Pages
Mounted under `/petty-cash/`:
- `/petty-cash/` — list of weekly sheets (newest first) with live totals
- `/petty-cash/new` — create form (pick Friday)
- `/petty-cash/sheet?name=...` — editor with autosave & live totals
- `/petty-cash/print?name=...&copies=1|3` — A4 landscape printable, 4 pages

## Installation

```bash
# In your bench directory:
bench get-app https://github.com/tjharia93/Vcl_finance.git --branch main
bench --site <your-site> install-app vcl_finance
bench --site <your-site> migrate
bench --site <your-site> clear-cache
bench --site <your-site> clear-website-cache
```

On install, six `Petty Cash Category` masters (TG/TE/SE/OA/CM/OT) are seeded automatically.

## Permissions

| Role | Petty Cash Sheet | Petty Cash Category |
|---|---|---|
| System Manager | full (create / read / write / submit / cancel / amend) | full |
| Accounts Manager | full | read + write |
| Accounts User | create / read / write / print | read |

Plain-user access to `/petty-cash/*` web routes requires being logged in (no guest access).

## Roadmap

- **Phase 2 (this version)** — DocTypes + Website Page editor. Internal testing.
- **Phase 3 (next)** — Journal Entry posting on submit, workflow Draft → Submitted → Approved, optional mobile entry.

Phase plan + design notes live in Notion at https://www.notion.so/3518e0265cd581f69db5f05190fb198d

## Development

```bash
bench --site <your-site> console
>>> import frappe
>>> doc = frappe.new_doc("Petty Cash Sheet")
>>> doc.week_ending = "2026-05-15"  # must be a Friday
>>> doc.opening_balance = 50000
>>> doc.custodian_name = "Shiro"
>>> doc.insert()
>>> # ensure_grid() runs automatically — sheet now has 18 voucher rows + parking grid + misc + wages
```

## License

MIT — see `LICENSE` if present.
