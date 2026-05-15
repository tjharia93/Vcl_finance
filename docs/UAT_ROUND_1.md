# UAT Round 1 — vcl_finance v0.1.0 (Petty Cash Phase 2)

**Tester:** Tanuj (with Shiro for floor walk-through)
**Environment:** vimitconverters.frappe.cloud (after `bench install-app vcl_finance`)
**Date:** _fill at start_

## Pre-flight

- [ ] App installs without error: `bench --site <site> install-app vcl_finance`
- [ ] `bench --site <site> migrate` completes clean
- [ ] `bench --site <site> clear-cache && clear-website-cache` clears
- [ ] Six `Petty Cash Category` records exist: TG, TE, SE, OA, CM, OT
- [ ] `Petty Cash` module visible in the Desk module list
- [ ] `/petty-cash/` route resolves for logged-in users
- [ ] `/petty-cash/` redirects to login for `Guest`

## TC-01 — Create new weekly sheet

| Step | Expected |
|---|---|
| 1. Open `/petty-cash/new` | New-sheet form renders. Week Ending defaults to next Friday. Custodian = "Shiro". Opening = 0. Authorised Float = 50000. |
| 2. Pick a Friday in the past, click Create Sheet | Redirects to `/petty-cash/sheet?name=PCS-2026-XXXX`. |
| 3. Try a Tuesday/Wednesday/Thursday in the form | Server rejects with "Week Ending must be a Friday." (Validation works.) |
| 4. Re-submit the same Friday | Existing sheet is returned (no duplicate). |

Pass / Fail: _____

## TC-02 — Editor renders a fully populated grid

| Step | Expected |
|---|---|
| 1. Open the sheet from TC-01 | 18 voucher rows visible. Parking matrix shows all 6 days × 5 plates × 2 slots = 60 inputs. Bike has 6 numbered rows. Forklift has 4. Wages has 18 rows. |
| 2. Scroll to the bottom | Status pill = Draft. Back / Print / Open in Desk buttons all visible. |
| 3. Click "Open in Desk" | Opens `/app/petty-cash-sheet/<name>` showing the same data in the standard Frappe form. |

Pass / Fail: _____

## TC-03 — Autosave + live totals (voucher)

| Step | Expected |
|---|---|
| 1. In voucher row 1, type `100` in the TG column, tab out | Save indicator flashes "Saving…" → "✓ Saved". TG footer cell shows 100. Total Out (top card) shows 100. Expected Close = Opening − 100. |
| 2. Type `50` in the IN column of row 1, tab out | Total In shows 50. Expected Close = Opening − 100 + 50. |
| 3. Type recipient name "Test User", tab out | Saves silently. |
| 4. Reload the page | All values persist. |
| 5. Open the underlying record via "Open in Desk" → vouchers child table | Row 1 shows recipient="Test User", amt_tg=100, amt_in=50. |

Pass / Fail: _____

## TC-04 — Parking matrix

| Step | Expected |
|---|---|
| 1. Enter `200` in (Monday, KAP 466, slot 1) | Daily total for Monday shows 200. Vehicle weekly total for KAP 466 shows 200. Grand parking total = 200. |
| 2. Enter `150` in (Monday, KAP 466, slot 2) | Daily total → 350. Vehicle total → 350. |
| 3. Enter `100` in (Tuesday, KAY 635, slot 1) | Tuesday daily = 100. KAY 635 weekly = 100. Grand = 450. |
| 4. Top summary "Parking" card | Shows 450. |

Pass / Fail: _____

## TC-05 — Bike / Forklift / Wages

| Step | Expected |
|---|---|
| 1. Enter `500` for Bike row 1 with a date | Bike/Forklift summary card increments. Total Out picks up the 500. |
| 2. Enter `2000` for Forklift row 1 with a date | Bike/Forklift summary = 500+2000 = 2500. |
| 3. In Wages row 1: type=Wage, recipient="Test Wage", amount=10000 | Wages summary = 10000. Total Out picks up 10000. |
| 4. In Wages row 2: type=Loan, recipient="Test Loan", amount=5000 | Wages summary = 15000. |

Pass / Fail: _____

## TC-06 — Reconciliation flow

| Step | Expected |
|---|---|
| 1. Top meta: set Opening Balance = 50000 | Expected Close recalculates. |
| 2. Set Cash Count End = expected closing balance | Variance = 0. |
| 3. Set Cash Count End = expected − 500 | Variance = −500 (red). |
| 4. Set Cash Count End = expected + 200 | Variance = 200 (positive). |

Pass / Fail: _____

## TC-07 — Print view

| Step | Expected |
|---|---|
| 1. Click "Print Single" | Opens `/petty-cash/print?...&copies=1`. Renders: Voucher Register (1 sheet) + Vehicle + Wages + Recon. |
| 2. Click "Print 3-Sheet" | Same but Voucher Register × 3 pages with "Sheet 1/2/3 of 3" labels. |
| 3. All entered values visible in print | Voucher row 1 shows recipient, TG amount, IN amount. Parking matrix shows entered values. Bike, Forklift, Wages, Recon all hydrated. |
| 4. Browser Print preview (Cmd/Ctrl+P) | Pages render A4 landscape, edge-to-edge, no input-panel controls. |
| 5. Empty cells in voucher rows | Render as blank (no "0" or "—"). |

Pass / Fail: _____

## TC-08 — Permissions

| Step | Expected |
|---|---|
| 1. Log out, browse `/petty-cash/` | Redirected to login. |
| 2. Log in as a user with only the Accounts User role | Can see the list, can create + edit Draft sheets. Cannot Submit (button absent in Desk view). |
| 3. Log in as Accounts Manager | Can Submit. After submit, status pill shows "Submitted", editor is read-only. |
| 4. Submitted sheet, click "Edit" from list | Editor opens but inputs are disabled (or the editor refuses changes — Frappe blocks writes to submitted docs). |

Pass / Fail: _____

## TC-09 — Submit + cancel + amend

| Step | Expected |
|---|---|
| 1. As Accounts Manager, open a complete sheet, click Submit in Desk | Sheet status flips to Submitted. `docstatus=1`. |
| 2. From Desk, click Cancel | Status flips to Draft, `docstatus=2`. |
| 3. From Desk, click Amend | New sheet created with `amended_from` populated, fields copied. |

Pass / Fail: _____

## Defects / observations

- _list any issues — screenshots in the Notion UAT sub-page_

## Sign-off

- [ ] **Tanuj** — overall pass
- [ ] **Shiro** — usability on the floor (entry speed, clarity of fields)
- [ ] **Finance Manager** — submit + recon flow

Date: _________
