# Architecture — Petty Cash module

## Data model

```
Petty Cash Sheet (parent, Submittable)
├── week_no            Int (computed from week_ending)
├── week_ending        Date — unique, must be a Friday
├── custodian          Link → Employee (optional)
├── custodian_name     Data — fallback display name (defaults to "Shiro")
├── opening_balance    Currency
├── cash_count_end     Currency — physical count at end of week
├── authorised_float   Currency — float ceiling per custodian
├── total_out          Currency — auto-summed across all child tables
├── total_in           Currency — auto-summed from vouchers.amt_in
├── expected_close     Currency — opening − total_out + total_in
├── variance           Currency — cash_count_end − expected_close
├── closing_balance    Currency — mirror of expected_close (kept for legacy)
├── status             Select Draft / Submitted / Approved
├── notes              Text Editor
│
├── vouchers           Table → Petty Cash Voucher × 18
├── parking_entries    Table → Petty Cash Parking Entry × 60 (6 days × 5 vehicles × 2 slots)
├── misc_entries       Table → Petty Cash Misc Entry × 10 (6 Bike + 4 Forklift)
└── wages_entries      Table → Petty Cash Wages Entry × 18

Petty Cash Category (master)
├── code               Data — unique (TG / TE / SE / OA / CM / OT)
├── label              Data — human-readable
├── gl_account         Link → Account (for Phase 3 GL posting)
└── disabled           Check
```

## Validation rules

`PettyCashSheet.validate()` runs on every save (Draft + amendments):

1. **week_ending must be a Friday** — throws if `.weekday() != 4`.
2. **derive_week_no** from `week_ending.isocalendar()[1]`.
3. **ensure_grid()** idempotently appends missing rows so the editor always sees a fully-populated grid (skipped once submitted to keep the document frozen).
4. **compute_totals()** sums every child table's amounts and recomputes `total_out`, `total_in`, `expected_close`, `variance`.

`before_save()` mirrors `expected_close → closing_balance`.

`on_submit()` flips status to `Submitted`. `on_cancel()` flips back to `Draft`.

## Autosave path

Editor JS (`vcl_finance/public/js/petty_cash.js`) talks to Frappe's REST API:

```
PUT /api/resource/Petty Cash Sheet/<name>
Content-Type: application/json
X-Frappe-CSRF-Token: ...

{
  "vouchers": [
    {"name": "<child-row-name>", "amt_tg": 1500, "recipient": "John Doe"}
  ]
}
```

Frappe matches the child by its `name` (row UUID) and patches in place. Because `PettyCashSheet.validate()` runs on every save, totals refresh server-side on every keystroke change.

After each save, the JS calls `vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet.summary` to re-pull computed totals (server is the source of truth — JS never does arithmetic).

## Print path

`/petty-cash/print?name=...&copies=N` renders the same data through `print.html`, which is a faithful port of the Phase 1 standalone HTML print form. The `copies` parameter splits vouchers into 18-row continuation pages. Page 1×N → voucher register, Page 2 → vehicle, Page 3 → wages, Page 4 → recon & summary.

The page does not depend on Frappe's default web shell chrome — it sets `no_sidebar` and `no_header` so it renders pixel-equivalent to Phase 1.

## Permissions model

| Action | System Manager | Accounts Manager | Accounts User |
|---|---|---|---|
| Create Sheet | ✓ | ✓ | ✓ |
| Read | ✓ | ✓ | ✓ |
| Write (Draft) | ✓ | ✓ | ✓ |
| Submit | ✓ | ✓ | ✗ |
| Cancel | ✓ | ✓ | ✗ |
| Amend | ✓ | ✓ | ✗ |

`Accounts User` is intended as Shiro's role. She can create and fill weekly sheets but cannot submit them — that's the Finance Manager's responsibility (Accounts Manager).

## Boundaries with the rest of ERPNext

- **No GL posting in Phase 2** — by design. The sheet is a record-keeping document only. Phase 3 will introduce a Journal Entry hook on submit (debit category accounts, credit `Petty Cash on Hand`).
- **No Employee creation** — `custodian_name` is a free-text fallback so deployment doesn't depend on Shiro being in HR.
- **No Vehicle master** — vehicle plates are a Select option list on the Parking Entry DocType. Five plates hard-coded for now; promote to a master in Phase 3 if the fleet grows.
- **Float ceiling is a check, not an enforcement** — `authorised_float` is shown on the recon page but doesn't block over-spending. Hard cap can be added as a `validate()` rule if needed.
