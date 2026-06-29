# Petty Cash — Continuous Ledger + Accounts-Manager Weekly Close

*Vimit Converters Limited · design spec · prepared 2026-06-29 · follows `petty_cash_ui_unification_plan.md`*

## Problem

Today entry is bucketed into **manually-managed weekly sheets** per float, and reconciliation is weekly. Tanuj wants:

1. A prominent **New Entry** button.
2. **Continuous entry** — stop creating weeks by hand.
3. The weekly **cash-count + variance** preserved, but done by the **Accounts Manager**, who also **closes + locks** the week.
4. Once closed, **only an Accounts Manager** can edit.
5. **Date-range reports** (from→to) for flexible viewing.

## Approach (chosen: A — Evolve, not rebuild)

Keep the weekly `Petty Cash Sheet` as an **auto-created, invisible bucket**. Layer on the close/lock workflow, the permission gate, a prominent entry button, and a date-range report. This preserves reconciliation, carry-forward, draft-JE posting, the weekly filing copy, and the June data we just reconciled — with no migration. (Approach B — a flat continuous ledger replacing the sheet — was rejected as weeks of rework and real risk to the books.)

**Scope:** the Compass petty cash screen + `vcl_finance` backend. **Both floats** — manual entry is primarily Cash; Hauz-Pay keeps arriving via the bank-statement import and is simply also closeable + reportable.

## Model & components

### 1. Continuous entry + prominent New Entry
- Landing becomes a **running ledger/feed**, not a list of weeks to manage.
- A global, always-visible **＋ New Entry** opens the keypad wizard → float → type → amount → category/details → date (defaults today).
- The entry **auto-files into that date's week sheet for that float** (find-or-create — already the behaviour today). The user never sees "create a week."
- The custodian (**Petty Cash User** role) may add / edit / cancel entries **only in open (not-yet-closed) weeks**.

### 2. Weekly close = reconciliation + lock (Accounts Manager only)
- **Week boundary:** the existing **Friday-ending** convention, auto-derived from the entry date. (Decision — confirm on review.)
- A **Close week** action visible **only to Accounts Manager / System Manager**. Flow:
  1. Show the week's `expected_close` per float.
  2. AM enters the **counted cash** (`cash_count_end`).
  3. `variance` = counted − expected, computed and shown.
  4. AM confirms → sheet `status` Draft → **Closed**; `closed_by` + `closed_on` stamped; all the week's entries lock.
- **Posting:** the existing draft-JE generation runs at/after close; AM still submits the JE in ERPNext (drafts only — unchanged).
- **Reopen:** an Accounts Manager can **reopen** a Closed week (status → Draft), edit, and re-close. The reopen is audit-stamped.

### 3. Edit-lock (the rule), enforced server-side
- A guard in `vcl_finance/petty_cash/api.py`: `quick_entry`, the update path, `cancel_entry`, and `reinstate_entry` **reject** when the parent sheet `status == Closed` **and** the caller lacks `{Accounts Manager, System Manager}`. Reuses/extends the current "reject if Submitted/Approved" guard.
- **Historical `Approved` sheets are treated as Closed** for locking (they stay locked). (Decision — confirm.)
- UI reflects it: closed weeks render read-only for the custodian; the AM sees a **reopen/edit** affordance.

### 4. Date-range report
- A new **Report** view in Compass: **from-date → to-date** (+ optional float filter). Shows every entry in range plus totals by category, by float, cash in/out, net, wages/loans/parking/fuel, and per-week variance for weeks fully inside the range. **Printable / exportable.**
- **Additive** — it does **not** replace the per-week **filing copy**, which stays the signed audit document.
- **Visibility:** Accounts Manager / managers (gated like the analytics screen). The custodian sees their live ledger, not the cross-period report. (Decision — confirm.)

## Data model changes (small)
- **Petty Cash Sheet:** status lifecycle **Draft → Closed** (with reopen → Draft). Add `closed_by` (Link User, read-only) and `closed_on` (Datetime, read-only). `cash_count_end`, `expected_close`, `variance` already exist. Existing `Approved`/`Submitted` are treated as Closed (locked) for back-compat.
- **No new doctype**; **no change** to the child entry doctypes (the `cancelled` audit fields already exist).
- **Roles:** `Petty Cash User` (custodian — edit open weeks only); `Accounts Manager` (close / reopen / edit-closed / report / analytics).

## What deliberately stays / out of scope
- **Stays:** the weekly sheet bucket (now auto-created), carry-forward, draft-JE posting, the weekly filing-copy print, the analytics screen, and the reconciled June data.
- **Out of scope:** changing the Hauz-Pay import; rebuilding to a flat ledger; native-app changes (the Android wrapper picks up the new button/report automatically from the web).

## Migration
**None required.** Existing sheets keep working; the close workflow applies going forward. The status mapping above keeps historical locked sheets locked.

## Decisions to confirm on review
1. Week boundary stays **Friday-ending**, auto-derived.
2. Lifecycle is **Draft → Closed** (reopenable); historical **Approved → treated as Closed**.
3. The **date-range report** is **Accounts-Manager-gated** (custodian doesn't see it).

## Ships with
The pending Frappe Cloud deploy + the existing cancel/analytics work — one release.
