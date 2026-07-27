# Petty Cash Backend Tasks 1–5 — Implementation Report

Generated: 2026-06-29

---

## Task 1 — Sheet doctype: `closed_by` / `closed_on` + `Closed` status

**File changed:** `vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.json`

**Changes:**
- Added `"closed_by"` and `"closed_on"` to `field_order` (after `"variance"`).
- Added two field objects after the `variance` field:
  - `closed_by` — Link/User, read_only
  - `closed_on` — Datetime, read_only
- Changed `status` field options from `"Draft\nSubmitted\nApproved"` → `"Draft\nSubmitted\nApproved\nClosed"`

**Verify command:**
```
python3 -c "import json; d=json.load(open('vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.json')); fns=[f['fieldname'] for f in d['fields']]; assert len(fns)==len(set(fns)), 'dup'; assert 'closed_by' in fns and 'closed_on' in fns; assert 'Closed' in d['fields'][[f['fieldname'] for f in d['fields']].index('status')]['options']; print('ok')"
```
**Output:** `ok`

**Commit:** `9a2d0e2` — `feat(petty_cash): sheet closed_by/closed_on + Closed status`

---

## Task 2 — `is_locked()` helper on the sheet controller

**File changed:** `vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.py`

**Changes:**
- Added `is_locked()` instance method to `PettyCashSheet` (placed before `on_submit`):
  ```python
  def is_locked(self):
      """A week is locked once closed (or historically Submitted/Approved)."""
      return self.status in ("Closed", "Submitted", "Approved")
  ```

**Verify command:**
```
python3 -c "import ast; ast.parse(open('vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.py').read()); print('ok')"
```
**Output:** `ok`

**Commit:** `fb79193` — `feat(petty_cash): sheet.is_locked() helper`

---

## Task 3 — Edit-lock guard on the entry API

**File changed:** `vcl_finance/petty_cash/api.py`

**Changes:**
- Added `PETTY_PRIV`, `_is_accounts_manager()`, and `_assert_can_write()` before `_open_sheet_for_write`.
- Called `_assert_can_write(doc)` in three write-path functions:
  - `quick_entry` — after loading the doc and the existing Submitted/Approved check
  - `cancel_entry` — after `_open_sheet_for_write(sheet)` returns the doc
  - `reinstate_entry` — after `_open_sheet_for_write(sheet)` returns the doc

The existing Submitted/Approved hard-block in `_open_sheet_for_write` is preserved. The new guard adds the Closed-status check on top, allowing AMs through while blocking custodians.

**Verify command:**
```
python3 -c "import ast,re; s=open('vcl_finance/petty_cash/api.py').read(); ast.parse(s); assert s.count('_assert_can_write(')>=4; print('ok')"
```
**Output:** `ok` (1 definition + 3 call sites = 4 occurrences)

**Commit:** `3c56869` — `feat(petty_cash): edit-lock guard — closed weeks are AM-only`

---

## Task 4 — `close_week` + `reopen_week` whitelisted methods

**File changed:** `vcl_finance/petty_cash/api.py`

**Changes:**
- Added `close_week(sheet, cash_count_end)` — `@frappe.whitelist(methods=["POST"])`, AM-only; sets `cash_count_end`, `status="Closed"`, `closed_by`, `closed_on`; calls `doc.save()` (controller recomputes totals/variance); returns dict.
- Added `reopen_week(sheet)` — `@frappe.whitelist(methods=["POST"])`, AM-only; resets `status="Draft"`, clears `closed_by`/`closed_on`; returns same shape dict.

Both inserted before the analytics section separator.

**Verify command:**
```
python3 -c "import ast; s=open('vcl_finance/petty_cash/api.py').read(); ast.parse(s); assert 'def close_week(' in s and 'def reopen_week(' in s; print('ok')"
```
**Output:** `ok`

**Commit:** `fa3f1c0` — `feat(petty_cash): close_week/reopen_week (AM-only)`

---

## Task 5 — `range_report` aggregation method (AM-only)

**File changed:** `vcl_finance/petty_cash/api.py`

**Changes:**
- Added `range_report(from_date, to_date, float=None)` — `@frappe.whitelist()`, AM-only.
- Filters sheets by `week_ending between [from_date, to_date]` (and optionally by `float`).
- Iterates all child tables (vouchers, wages_entries, loan_entries, parking_entries, misc_entries), skipping cancelled rows exactly as `petty_cash_analytics` does.
- Returns `{from_date, to_date, float, total_out, total_in, net, by_category, sections, weeks}`.
- `weeks` list sorted ascending by `week_ending`.

Inserted between `reopen_week` and the `petty_cash_analytics` section separator.

**Verify command:**
```
python3 -c "import ast; s=open('vcl_finance/petty_cash/api.py').read(); ast.parse(s); assert 'def range_report(' in s; print('ok')"
```
**Output:** `ok`

**Commit:** `e9aacaf` — `feat(petty_cash): range_report aggregation (AM-only)`

---

## Summary

| Task | Commit | File(s) |
|------|--------|---------|
| 1 | `9a2d0e2` | `petty_cash_sheet.json` |
| 2 | `fb79193` | `petty_cash_sheet.py` |
| 3 | `3c56869` | `api.py` |
| 4 | `fa3f1c0` | `api.py` |
| 5 | `e9aacaf` | `api.py` |

All static checks (`python3 -c "import ast..."` / JSON parse) passed. No `git push` performed.

---

## Fix Pass — api.py Code-Review Fixes (2026-06-29)

Commit: `af82d22`

| Fix | Description | Verify |
|-----|-------------|--------|
| FIX 1 | Wrapped every raw amount in `range_report` with `flt()` — vouchers (in+out), wages, loans, parking, misc/bike/forklift | `grep 'flt(' api.py` shows all amounts in range_report loop wrapped |
| FIX 2 | Moved `PETTY_PRIV` + `_is_accounts_manager` + `_assert_can_write` to lines 204–215, above `quick_entry` at line 242 (was after `_find_row`) | `grep -n 'def _assert_can_write\|def quick_entry' api.py` → 211, 242 |
| FIX 3 | Added `frappe.has_permission("Petty Cash Sheet","write",sheet,throw=True)` at the top of `close_week` and `reopen_week` after the AM role check | present in both functions |
| FIX 4 | In `range_report` by_cat accumulation, guard with `if v.category:` before adding to dict — blank/None category rows still count toward `out` total | line 523 in updated file |
| FIX 5 | In `quick_entry`, `_assert_can_write(doc)` now runs BEFORE the `Submitted/Approved` status check | lines 252–253 in updated file |

**Verify command output:**
```
python3 -c "import ast; s=open('vcl_finance/petty_cash/api.py').read(); ast.parse(s); assert s.count('_assert_can_write(')>=4 and 'def close_week(' in s and 'def range_report(' in s; print('ok')"
ok
```
