# Petty Cash Continuous Ledger + AM Weekly Close — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make petty-cash entry continuous (no manual week creation) with an Accounts-Manager weekly close that counts the cash, locks the week, and a date-range report — by evolving the existing weekly `Petty Cash Sheet`, not rebuilding it.

**Architecture:** The weekly `Petty Cash Sheet` stays as an auto-created bucket. Backend (`vcl_finance`) gains `closed_by`/`closed_on` fields, a `Closed` status, whitelisted `close_week`/`reopen_week` methods, an edit-lock guard in the entry API, and a `range_report` aggregator. Frontend (`vcl_compass`/`PettyCash.tsx` + a new report screen) surfaces a prominent New Entry, the AM close/reopen flow, read-only locking, and a from→to report gated to Accounts Managers.

**Tech Stack:** Frappe v15 (Python, DocType JSON), Frappe Cloud (deploy = git push main + dashboard Deploy + migrate), React + Vite + TypeScript + frappe-react-sdk (Compass SPA), vcl-erpnext MCP for live verification.

## Global Constraints

- Two repos: `~/projects/Vcl_finance` (backend, GitHub `tjharia93/Vcl_finance`) and `~/projects/frappe-cloud-rebrand/vcl_fiori` (Compass, GitHub `tjharia93/vcl_compass`).
- **Drafts/working-tree only — do NOT `git push` or trigger Frappe Cloud Deploy.** Tanuj pushes + deploys. Commit locally per task.
- **Never submit** ERPNext docs; never hard-delete petty cash entries (void via `cancelled`).
- Privileged roles for every gate = `{"Accounts Manager", "System Manager"}`. Custodian role = `"Petty Cash User"`.
- Both floats: `Cash`, `Hauz-Pay`. Week boundary = Friday-ending, auto-derived. Lifecycle `Draft → Closed` (reopen → Draft); historical `Approved`/`Submitted` are treated as Closed (locked).
- Frappe Cloud blocks `frappe.client.sql`; verify via MCP `get_meta`/`list_docs`/`run_method` or `bench console`.
- SPA build is committed (Frappe Cloud runs no Node): after frontend changes run `npm run build` in `frontend/` and commit the regenerated `vcl_compass/public/compass/assets/*`.

---

## File map

- `vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.json` — add `closed_by`, `closed_on`; add `Closed` to `status` options.
- `vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.py` — `is_locked()` helper; keep compute logic.
- `vcl_finance/petty_cash/api.py` — `close_week`, `reopen_week`, `range_report`; extend the write-guard used by `quick_entry`/`cancel_entry`/`reinstate_entry`.
- `frontend/src/dashboards/PettyCash.tsx` — prominent New Entry, read-only when locked, AM close/reopen controls.
- `frontend/src/dashboards/PettyCashReport.tsx` — NEW date-range report screen.
- `frontend/src/App.tsx`, `frontend/src/shell/Sidebar.tsx`, `frontend/src/launchpad/Launchpad.tsx` — route + nav tile for the report (gated).
- `vcl_compass/api/access.py` — grant `petty_cash_report` module to Accounts Managers only (mirror `petty_cash_analytics`).

---

### Task 1: Sheet doctype — `closed_by` / `closed_on` + `Closed` status

**Files:**
- Modify: `vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.json`

**Interfaces:**
- Produces: sheet fields `closed_by` (Link User, read-only), `closed_on` (Datetime, read-only); `status` Select gains `Closed`.

- [ ] **Step 1: Add the two fields + status option to the JSON.** In `fields`, after `variance`, add:

```json
{ "fieldname": "closed_by", "fieldtype": "Link", "options": "User", "label": "Closed By", "read_only": 1 },
{ "fieldname": "closed_on", "fieldtype": "Datetime", "label": "Closed On", "read_only": 1 }
```

Add both fieldnames to `field_order` (after `variance`). Change the `status` field `options` from `"Draft\nSubmitted\nApproved"` to `"Draft\nSubmitted\nApproved\nClosed"`.

- [ ] **Step 2: Validate the JSON parses and has no duplicate fieldnames.**

Run: `python3 -c "import json; d=json.load(open('vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.json')); fns=[f['fieldname'] for f in d['fields']]; assert len(fns)==len(set(fns)), 'dup'; assert 'closed_by' in fns and 'closed_on' in fns; assert 'Closed' in d['fields'][[f['fieldname'] for f in d['fields']].index('status')]['options']; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit.**

```bash
cd ~/projects/Vcl_finance && git add vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.json && git commit -m "feat(petty_cash): sheet closed_by/closed_on + Closed status"
```

---

### Task 2: `is_locked()` helper on the sheet controller

**Files:**
- Modify: `vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.py`

**Interfaces:**
- Produces: `PettyCashSheet.is_locked() -> bool` (True when `status in {"Closed","Submitted","Approved"}`). Consumed by Task 3's guard.

- [ ] **Step 1: Add the method to the controller class.**

```python
def is_locked(self):
    """A week is locked once closed (or historically Submitted/Approved)."""
    return self.status in ("Closed", "Submitted", "Approved")
```

- [ ] **Step 2: Verify it imports/parses.**

Run: `python3 -c "import ast; ast.parse(open('vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit.**

```bash
cd ~/projects/Vcl_finance && git add vcl_finance/petty_cash/doctype/petty_cash_sheet/petty_cash_sheet.py && git commit -m "feat(petty_cash): sheet.is_locked() helper"
```

---

### Task 3: Edit-lock guard on the entry API

**Files:**
- Modify: `vcl_finance/petty_cash/api.py`

**Interfaces:**
- Consumes: `PettyCashSheet.is_locked()` (Task 2).
- Produces: `_assert_can_write(sheet_doc)` — raises `frappe.PermissionError` if the sheet is locked and the caller is not `{Accounts Manager, System Manager}`. Called at the top of `quick_entry`, `cancel_entry`, `reinstate_entry`, and any whitelisted update path.

- [ ] **Step 1: Add the role helper + guard near the existing guards in `api.py`.**

```python
PETTY_PRIV = {"Accounts Manager", "System Manager"}

def _is_accounts_manager():
    return bool(set(frappe.get_roles()) & PETTY_PRIV)

def _assert_can_write(sheet):
    """Block edits to a locked week unless the caller is an Accounts Manager."""
    if sheet.is_locked() and not _is_accounts_manager():
        frappe.throw("This week is closed. Only an Accounts Manager can edit it.",
                     frappe.PermissionError)
```

- [ ] **Step 2: Call `_assert_can_write(sheet)` at the top of each write path** in `api.py` — `quick_entry`, `cancel_entry`, `reinstate_entry` (right after the sheet doc is loaded, replacing/augmenting the existing Submitted/Approved check).

- [ ] **Step 3: Verify the module parses and the guard is wired.**

Run: `python3 -c "import ast,re; s=open('vcl_finance/petty_cash/api.py').read(); ast.parse(s); assert s.count('_assert_can_write(')>=4; print('ok')"`
Expected: `ok` (1 def + ≥3 call sites)

- [ ] **Step 4: Commit.**

```bash
cd ~/projects/Vcl_finance && git add vcl_finance/petty_cash/api.py && git commit -m "feat(petty_cash): edit-lock guard — closed weeks are AM-only"
```

---

### Task 4: `close_week` + `reopen_week` whitelisted methods

**Files:**
- Modify: `vcl_finance/petty_cash/api.py`

**Interfaces:**
- Consumes: `_is_accounts_manager()` (Task 3).
- Produces:
  - `close_week(sheet, cash_count_end) -> dict` — AM-only; sets `cash_count_end`, recomputes `variance`/`expected_close` via the controller, sets `status="Closed"`, `closed_by=frappe.session.user`, `closed_on=now`, saves; returns `{name, status, expected_close, cash_count_end, variance}`.
  - `reopen_week(sheet) -> dict` — AM-only; sets `status="Draft"`, clears `closed_by`/`closed_on`, saves; returns the same shape.

- [ ] **Step 1: Add both methods to `api.py`.**

```python
@frappe.whitelist(methods=["POST"])
def close_week(sheet, cash_count_end):
    if not _is_accounts_manager():
        frappe.throw("Only an Accounts Manager can close a week.", frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    doc.cash_count_end = float(cash_count_end)
    doc.status = "Closed"
    doc.closed_by = frappe.session.user
    doc.closed_on = frappe.utils.now_datetime()
    doc.save()  # controller recomputes total_out/expected_close/variance
    return {"name": doc.name, "status": doc.status, "expected_close": doc.expected_close,
            "cash_count_end": doc.cash_count_end, "variance": doc.variance}

@frappe.whitelist(methods=["POST"])
def reopen_week(sheet):
    if not _is_accounts_manager():
        frappe.throw("Only an Accounts Manager can reopen a week.", frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    doc.status = "Draft"
    doc.closed_by = None
    doc.closed_on = None
    doc.save()
    return {"name": doc.name, "status": doc.status, "expected_close": doc.expected_close,
            "cash_count_end": doc.cash_count_end, "variance": doc.variance}
```

- [ ] **Step 2: Verify the module parses and the two whitelisted methods exist.**

Run: `python3 -c "import ast; s=open('vcl_finance/petty_cash/api.py').read(); ast.parse(s); assert 'def close_week(' in s and 'def reopen_week(' in s; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit.**

```bash
cd ~/projects/Vcl_finance && git add vcl_finance/petty_cash/api.py && git commit -m "feat(petty_cash): close_week/reopen_week (AM-only)"
```

---

### Task 5: `range_report` aggregation method (AM-only)

**Files:**
- Modify: `vcl_finance/petty_cash/api.py`

**Interfaces:**
- Consumes: `_is_accounts_manager()`.
- Produces: `range_report(from_date, to_date, float=None) -> dict` — AM-only; aggregates non-cancelled child rows across sheets whose `week_ending` is in range (optionally one float). Returns `{from_date, to_date, float, total_out, total_in, net, by_category:{code:amt}, sections:{wages,loans,parking,bike,forklift}, weeks:[{name,week_ending,float,expected_close,cash_count_end,variance,status}]}`.

- [ ] **Step 1: Add the method to `api.py`** (reuse the same exclusion logic as `petty_cash_analytics` — skip `row.cancelled`; iterate the sheets' child tables via `frappe.get_doc`).

```python
@frappe.whitelist()
def range_report(from_date, to_date, float=None):
    if not _is_accounts_manager():
        frappe.throw("Accounts Manager only.", frappe.PermissionError)
    filters = {"week_ending": ["between", [from_date, to_date]]}
    if float:
        filters["float"] = float
    names = frappe.get_all("Petty Cash Sheet", filters=filters, pluck="name")
    by_cat, weeks = {}, []
    out = tin = wages = loans = parking = bike = forklift = 0.0
    for nm in names:
        d = frappe.get_doc("Petty Cash Sheet", nm)
        for v in d.vouchers:
            if v.cancelled: continue
            if v.cash_in: tin += v.amount
            else:
                out += v.amount
                by_cat[v.category] = by_cat.get(v.category, 0.0) + v.amount
        for w in d.wages_entries:
            if not w.cancelled: out += w.amount; wages += w.amount
        for l in d.loan_entries:
            if not l.cancelled: out += l.amount_issued; loans += l.amount_issued
        for p in d.parking_entries:
            if not p.cancelled: out += p.amount; parking += p.amount
        for m in d.misc_entries:
            if m.cancelled: continue
            out += m.amount
            if m.kind == "Forklift": forklift += m.amount
            else: bike += m.amount
        weeks.append({"name": d.name, "week_ending": str(d.week_ending), "float": d.float,
                      "expected_close": d.expected_close, "cash_count_end": d.cash_count_end,
                      "variance": d.variance, "status": d.status})
    return {"from_date": from_date, "to_date": to_date, "float": float,
            "total_out": out, "total_in": tin, "net": tin - out, "by_category": by_cat,
            "sections": {"wages": wages, "loans": loans, "parking": parking,
                         "bike": bike, "forklift": forklift},
            "weeks": sorted(weeks, key=lambda x: x["week_ending"])}
```

- [ ] **Step 2: Verify parse.**

Run: `python3 -c "import ast; s=open('vcl_finance/petty_cash/api.py').read(); ast.parse(s); assert 'def range_report(' in s; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit.**

```bash
cd ~/projects/Vcl_finance && git add vcl_finance/petty_cash/api.py && git commit -m "feat(petty_cash): range_report aggregation (AM-only)"
```

---

### Task 6: Compass — prominent New Entry + read-only when locked

**Files:**
- Modify: `frontend/src/dashboards/PettyCash.tsx`, `frontend/src/brand.css`

**Interfaces:**
- Consumes: existing `quick_entry`/`get_feed` fetch helpers; sheet `status` from `useFrappeGetDoc`.
- Produces: a `locked = status === 'Closed' || status === 'Submitted' || status === 'Approved'` flag used to disable entry/edit/cancel controls for non-AM users.

- [ ] **Step 1: Surface a global ＋ New Entry** — in `EntryView`/landing, render the existing wizard-open control as a persistent primary button on both the desktop two-pane and the mobile bar (already present on mobile as `.pcx-newbar`; ensure it shows on the landing and is the primary call to action). No new data wiring.

- [ ] **Step 2: Compute `locked` and an `isAM` flag.** Read `status` from the loaded sheet; derive `const locked = ['Closed','Submitted','Approved'].includes(status)`. Get `isAM` from `get_my_modules`/access (a module the custodian lacks, e.g. `petty_cash_report` from Task 8) or a dedicated `is_accounts_manager` flag. When `locked && !isAM`: disable the wizard save, the feed cancel/reinstate, and the grid edit inputs (reuse the existing `closed` disabling path already used for Approved sheets).

- [ ] **Step 3: Build to verify types.**

Run: `cd ~/projects/frappe-cloud-rebrand/vcl_fiori/frontend && npm run build`
Expected: tsc + vite exit 0.

- [ ] **Step 4: Commit (incl. regenerated build assets).**

```bash
cd ~/projects/frappe-cloud-rebrand/vcl_fiori && git add frontend/src vcl_compass/public/compass/assets vcl_compass/public/compass/index.html vcl_compass/www/compass/index.html && git commit -m "feat(compass/petty_cash): prominent New Entry + read-only when locked"
```

---

### Task 7: Compass — Accounts-Manager close / reopen flow

**Files:**
- Modify: `frontend/src/dashboards/PettyCash.tsx`, `frontend/src/brand.css`

**Interfaces:**
- Consumes: backend `close_week(sheet, cash_count_end)` and `reopen_week(sheet)` (Tasks 4); `isAM` flag (Task 6).
- Produces: a `closeWeek(sheet, count)` / `reopenWeek(sheet)` fetch helper pair calling `/api/method/vcl_finance.petty_cash.api.close_week|reopen_week` with CSRF.

- [ ] **Step 1: Add the fetch helpers** (mirror the existing `quick_entry` POST helper — JSON body, `X-Frappe-CSRF-Token`).

- [ ] **Step 2: Add a Close-week control, shown only when `isAM` and `status === 'Draft'`.** It opens a small panel: shows `expected_close` for the week, an input for **counted cash**, computes `variance = count − expected` live, and a Confirm button → `closeWeek` → toast → refetch. When `isAM` and `locked`, show a **Reopen** control → `reopenWeek` → refetch.

- [ ] **Step 3: Build to verify.**

Run: `cd ~/projects/frappe-cloud-rebrand/vcl_fiori/frontend && npm run build`
Expected: exit 0.

- [ ] **Step 4: Commit.**

```bash
cd ~/projects/frappe-cloud-rebrand/vcl_fiori && git add frontend/src vcl_compass/public/compass/assets vcl_compass/public/compass/index.html vcl_compass/www/compass/index.html && git commit -m "feat(compass/petty_cash): AM weekly close + reopen"
```

---

### Task 8: Compass — date-range Report screen (AM-gated)

**Files:**
- Create: `frontend/src/dashboards/PettyCashReport.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/shell/Sidebar.tsx`, `frontend/src/launchpad/Launchpad.tsx`, `vcl_compass/api/access.py`

**Interfaces:**
- Consumes: backend `range_report(from_date, to_date, float?)` (Task 5).
- Produces: route `/petty-cash-report`, module key `petty_cash_report` granted only to `{Accounts Manager, System Manager}`.

- [ ] **Step 1: Gate the module in `access.py`** — add `"petty_cash_report"` to `ALL_MODULES` and to the `_ACCOUNTS_MGR_ONLY` set (so the broad grant excludes it and only the AM branch adds it), exactly as `petty_cash_analytics` is handled.

- [ ] **Step 2: Build `PettyCashReport.tsx`** — from-date + to-date pickers (+ optional float select), a Run button that fetches `range_report`, and renders KPI cards (total out / in / net), a by-category table, the sections breakdown, and a per-week table (expected, counted, variance, status). Add a Print button (`window.print()`), reusing the analytics screen's brand/format helpers.

- [ ] **Step 3: Wire route + nav tile** — add `/petty-cash-report` to `App.tsx` guarded by `mods.includes('petty_cash_report')`; add a Sidebar item (Finance group) and a Launchpad tile, both conditional on that module.

- [ ] **Step 4: Build to verify.**

Run: `cd ~/projects/frappe-cloud-rebrand/vcl_fiori/frontend && npm run build`
Expected: exit 0; assert `petty_cash_report` referenced in the built bundle.

- [ ] **Step 5: Commit.**

```bash
cd ~/projects/frappe-cloud-rebrand/vcl_fiori && git add frontend/src vcl_compass/api/access.py vcl_compass/public/compass/assets vcl_compass/public/compass/index.html vcl_compass/www/compass/index.html && git commit -m "feat(compass/petty_cash): AM-gated date-range report"
```

---

### Task 9: Live verification after deploy (Tanuj deploys; this is the acceptance checklist)

**Files:** none (verification only).

- [ ] **Step 1:** After Tanuj pushes both repos + clicks Frappe Cloud Deploy (runs migrate for the new fields/status), confirm the fields exist: MCP `get_meta("Petty Cash Sheet")` → `closed_by`, `closed_on` present, `status` options include `Closed`.
- [ ] **Step 2:** As an Accounts Manager, `close_week` a Draft test week with a counted figure → status `Closed`, variance computed, `closed_by`/`closed_on` set. Then `quick_entry` as the custodian on that week → expect `PermissionError`. Then `reopen_week` → status `Draft`, custodian entry succeeds again.
- [ ] **Step 3:** `range_report` for a known span → totals match the analytics screen for the same span; confirm a non-AM call raises `PermissionError`.
- [ ] **Step 4:** In `/compass`: New Entry is prominent; a closed week is read-only for the custodian and editable/reopenable for the AM; the Report tile appears only for the AM.

---

## Self-review notes
- **Spec coverage:** New Entry → T6; continuous/auto-week → existing behaviour retained (no task needed, called out in T6 Step 1); weekly count+variance+lock by AM → T1/T4/T7; edit-lock AM-only → T2/T3/T6; date-range report AM-gated → T5/T8; both floats → T5/T8 (float filter) + entry unchanged; no migration → confirmed (T1 fields are additive, status back-compat in T2 `is_locked`).
- **Status mapping:** `is_locked()` treats `Submitted`/`Approved` as locked → historical sheets stay locked (spec decision 2).
- **Naming consistency:** `_is_accounts_manager`, `_assert_can_write`, `close_week`, `reopen_week`, `range_report`, module key `petty_cash_report` — used identically across backend + frontend tasks.
