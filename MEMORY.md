# MEMORY — read this first

Last updated: **2026-10-28**

## State
- Live on site: unknown. The newest commit on `main` is 083a1df (24/10). Which commit Frappe Cloud
  last deployed is not recorded in the repo.
- Migrate pending: unknown. Commits that add DocType fields since 2026-09 include a54bcc4 (22/09,
  reconcile fields and receipts on five child DocTypes), 9ea7ae9 (08/10, VAT fields on Petty Cash
  Entry) and 083a1df (24/10, ETR invoice number field). Whether each has been migrated: unknown.
- `app_version` in `hooks.py` is 0.1.0.
- Not on main: branch `hauzpay-import` has 10 commits (14/10 to 24/10): Hauz Pay statement import,
  `move_row` between Vouchers and Wages, Hauz-Pay top-ups posting to ERPNext, and the Frappe
  version range in `pyproject.toml`. Two of its commits were cherry-picked to main (ad804be, 083a1df).
  Branch `claude/fc-dev-pages` has one commit (bf23375, 27/10, old finance dev routes to /fc-dev).
- Reverted: the AR Control Check DocTypes (ed9ca9e, reverted 54f6b0e on 01/10).

## What happened in dev
- 2026-10-24 Phone approvals: each pending entry carries the slip's file URL; Finance records PC and
  ETR marks (and the ETR Control Unit invoice number) while approving, written back to the sheet row.
- 2026-10-13 to 10-14 Posting: a week's lines come from its sheet, not the week date; ERPNext journal
  for chosen companies only; `set_exclude` keeps one line out of the books; QuickBooks gets one line
  per entry with ETR, net and gross.
- 2026-10-08 VAT on Petty Cash Entry and week-level posting (`weekpost`): ERPNext draft journal and a
  tax-inclusive QuickBooks Expense staged unapproved in the QBO Petty Cash Journal queue.
- 2026-10-05 Monthly VAT Report: output and input VAT by month, net payable, ledger check.
- 2026-09-24 to 09-25 QBO vs ERPNext Balances report, as at a date, against the signed accounts.
- 2026-09-22 Finance reconcile tick on every child table, and a receipt on every petty cash row.
- 2026-09-05 to 09-07 Approval queue: recorder may sign without Accounts Manager; approver given
  permlevel 1; orphaned rows no longer offered; 417 on the queue total fixed; newest-first option.
- 2026-09-01 Control totals (paper sheet vs captured, seven voucher codes, wages split five ways);
  the week's workbook emailed to reviewers on submit.
- 2026-08-30 to 08-31 Posting Map routes and guard rails; one resolver (`resolve.py`); post one line
  at a time; QuickBooks as an Expense, not a journal; four companies, one in QuickBooks; Bahati
  Ventures added as a destination; weekly override review; sheet-level posting route retired.
- 2026-08-28 to 08-29 Petty Cash Entry becomes the atom, mirrored one way from the sheet; the row lock
  is the approval, both ways; only Finance may tick it; approvals reseeded on migrate.
- 2026-07-23 to 07-27 Cash: live MPESA float balance; Daily Cash Position DocType and API, with
  AVAILABLE beside actual.
- 2026-07-01 to 07-10 Spend voucher needs PC or ETR; Sun–Sat weeks; separate Approve step; per-row
  lock; parking as dated single entries; posting date mandatory; print to A4 landscape, dd/mm/yyyy.
- 2026-06-29 to 06-30 Continuous ledger and Accounts Manager weekly close (close/reopen, edit lock,
  range report, analytics); cancel, not delete.
- 2026-06-19 to 06-22 Phase 1 DocType migration, Phase 2+3 (draft-JE posting, quick-entry API,
  carry-forward, keypad entry wizard), native print format, backfill flag.
- 2026-06-15 AR Reconciliation module with three DocTypes.
- 2026-05-15 Initial commit: Petty Cash Phase 2 (DocTypes and the web page editor).

## Decisions
- D-01 The mirror is synchronous: no enqueue, no scheduler, and it swallows its own errors so it never
  blocks a custodian's save (`hooks.py`).
- D-02 The row lock is the approval. Locked means approved and approved means locked, both ways
  (2339e61, 2026-08-29). Only Finance may tick or untick it (37a6913, 2026-08-29).
- D-03 The map proposes and the approver disposes. An override of the map needs a reason; the weekly
  override review only reads and proposes, it never rewrites the map (978d51f, ec90107, 2026-08-30).
- D-04 QuickBooks gets petty cash as an Expense, not a journal entry (92cbc89, 2026-08-31). Petty cash
  does not ride the Bill queue: 478 payees, one of them a Supplier (31c5b30, 2026-08-30).
- D-05 Four companies share the site; only one is in QuickBooks (`posts_to_qbo()`). A line for another
  company resolves with no QBO account on purpose (d978718, 2026-08-30).
- D-06 A row with no date stays blank rather than guessed (3350a0b, 2026-08-31).
- D-07 The sheet-level posting route raises with a message instead of being deleted (67864d3, 2026-08-30).
- D-08 AR Control Check DocTypes stay out of this app; they were created on the site directly
  (54f6b0e, 2026-10-01).

## Traps
1. Frappe drops fields a user may not write at that permlevel and saves the rest, with no error. An
   approval can return 200 and change nothing (5105f21).
2. Permlevel does not protect whitelisted methods. The role check must be in the method body
   (`petty_cash/entry_api.py`).
3. `patches.txt` matches the full line. To re-run a patch, add a `#marker` after it; the
   `reseed_entry_approval_from_lock #2026-08-29-two-way` line exists for this (f518cbc).
4. Frappe Cloud pauses the scheduler on every deploy. The weekly `override_review` job is the only
   scheduled job; nothing on the save path may depend on it.
5. A Frappe upgrade refuses SQL functions as field strings (`"sum(amount)"`), so the approval queue
   returned 417 and the phone showed its cache (1c20f72). Sum in Python.
6. `bench update --reset` can leave a stale `.lock`; `check_if_locked` is overridden to swallow only
   that FileNotFoundError (455c41a).
7. `txn_date` once defaulted to Today, so undated rows were stamped with the sync day and two were
   posted into August (3350a0b).
8. A partial migrate: only one of four AR Control DocTypes was created on Frappe Cloud (54f6b0e).
9. The README and `docs/ARCHITECTURE.md` still say `week_ending` must be a Friday. Since 08/07 weeks
   run Sun–Sat and the sheet controller requires a Saturday (ff4b975). Older sheets are Friday-anchored.
   Trust the code.

## Open questions
- Which `main` commit is live, and is any migrate pending? — Tanuj.
- Does `hauzpay-import` merge to main, and when? — Tanuj.
- How is `test_monthly_vat_report` meant to run (it needs `frappe`)? — unknown.
