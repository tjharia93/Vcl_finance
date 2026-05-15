# OAT Round 1 — vcl_finance v0.1.0 (Petty Cash Phase 2)

Operational Acceptance Testing — covers ops, deploy, recoverability, performance, and security gates. Run alongside UAT before approving Phase 2 for production use.

**Tester:** Tanuj (technical) + Finance Manager (process)
**Environment:** vimitconverters.frappe.cloud
**Date:** _fill at start_

## Section 1 — Deployment

| Check | Expected | Pass / Fail |
|---|---|---|
| Branch `main` on https://github.com/tjharia93/Vcl_finance.git contains v0.1.0 | Yes | _____ |
| Frappe Cloud bench is configured to track this app | `bench list-apps` includes `vcl_finance` | _____ |
| `bench get-app` succeeds against private/public repo | Clone clean, no auth prompt | _____ |
| `bench install-app vcl_finance` returns 0 | App appears in installed list | _____ |
| `bench migrate` runs the install patch without DB errors | 6 categories seeded; no duplicate-key errors on second run | _____ |
| Static assets (`/assets/vcl_finance/css/petty_cash.css`, `.../js/petty_cash.js`) load with 200 | Network tab shows both | _____ |
| `/petty-cash/` returns 200 to a logged-in user | Page renders | _____ |
| `/petty-cash/` returns 403 / login redirect to Guest | Redirect or `frappe.PermissionError` | _____ |

## Section 2 — Idempotency

| Check | Expected | Pass / Fail |
|---|---|---|
| Re-run `bench --site <site> install-app vcl_finance` | Does not duplicate Petty Cash Category records | _____ |
| Open an existing sheet twice | `ensure_grid()` does not add duplicate child rows | _____ |
| Submit + cancel + amend a sheet | Cycle works, no orphan children | _____ |
| Delete a Draft sheet | Cascade removes all child rows (`vouchers`, `parking_entries`, `misc_entries`, `wages_entries`) | _____ |

## Section 3 — Performance

| Check | Target | Pass / Fail |
|---|---|---|
| `/petty-cash/` list page first paint, 50 sheets | < 1.5 s | _____ |
| `/petty-cash/sheet?name=...` initial render | < 2 s | _____ |
| Single autosave round-trip (PUT + summary GET) | < 800 ms | _____ |
| `/petty-cash/print?copies=3` render | < 2.5 s | _____ |
| DB query count on editor open | Inspect via Toolbar → reasonable; no N+1 spike when child rows render | _____ |

## Section 4 — Data integrity

| Check | Expected | Pass / Fail |
|---|---|---|
| Two sheets with the same week_ending | Second insert fails (unique constraint on `week_ending`) | _____ |
| Sheet with `week_ending` set to Thursday | `validate()` blocks with "must be a Friday" | _____ |
| Manually set `total_out` via Desk → save | Server recomputes; manual override is overwritten by `compute_totals()` | _____ |
| Amend a submitted sheet, change one voucher | New sheet with `amended_from` link populated; totals recomputed | _____ |
| Currency formatting | All KES values render rounded to nearest 1 (no decimals) in editor + print | _____ |

## Section 5 — Recoverability

| Check | Expected | Pass / Fail |
|---|---|---|
| Frappe Cloud automatic daily backup includes `Petty Cash Sheet` rows | Confirm with most-recent backup includes test sheet | _____ |
| Restore a sheet from a backup | Test by dropping a sheet, restoring previous backup → sheet returns | _____ |
| `bench --site <site> uninstall-app vcl_finance` removes the module cleanly | DocTypes dropped; data archived to backup | _____ |
| Reinstall after uninstall | Categories re-seeded; no errors | _____ |

## Section 6 — Permissions

| Check | Expected | Pass / Fail |
|---|---|---|
| Accounts User can create, edit Draft, print | Yes | _____ |
| Accounts User cannot Submit / Cancel / Amend | No Submit button in Desk; API call returns 403 | _____ |
| Accounts Manager can Submit | Yes | _____ |
| Guest accessing `/petty-cash/sheet?name=...` | Login redirect | _____ |
| Direct API call `GET /api/resource/Petty Cash Sheet/X` as Guest | 403 | _____ |
| Audit trail: `Modified By` reflects the user who saved | Yes | _____ |

## Section 7 — Cross-cutting

| Check | Expected | Pass / Fail |
|---|---|---|
| `Open in Desk` from web editor opens `/app/petty-cash-sheet/<name>` | Yes | _____ |
| Frappe Communication / Comments on the Sheet work | Comments persist | _____ |
| Search the global ERPNext search bar for "PCS-2026-" | Sheet shows up in results | _____ |
| Frappe Standard "Send" / "Print" / "Email" buttons in Desk | All functional against `Petty Cash Sheet` | _____ |

## Section 8 — Rollback plan

If Phase 2 needs to be rolled back:

1. `bench --site <site> uninstall-app vcl_finance` (preserves backups)
2. Revert Frappe Cloud deployment to previous commit
3. Restore most recent backup if data corruption is suspected
4. Phase 1 print form (`vcl_custom/www/petty-cash/print.html`) remains the fallback — custodian can resume the handwritten + scan workflow with no app dependency

Rollback drill on staging _passed / failed_: _____

## Sign-off

- [ ] **Tanuj** — technical OAT pass
- [ ] **Finance Manager** — process OAT pass

Date: _________

---

## Notes captured during testing

_paste screenshots / logs into the Notion OAT sub-page; this file is the canonical checklist_
