# VCL Finance — start here

A Frappe app (`vcl_finance`) for VCL's finance work in ERPNext. Most of it is petty cash:
the weekly Petty Cash Sheet, the Petty Cash Entry ledger mirrored from it, approvals,
VAT, and posting to ERPNext (draft journals) and QuickBooks (Expense, through a queue).
It also holds Book Alignment (QBO vs ERPNext balances, Monthly VAT Report), Daily Cash
Position and the MPESA float API, and three AR Reconciliation DocTypes.

**Read `MEMORY.md` next**: current state, decisions and traps.

## Where it runs
- Site: https://vimitconverters.frappe.cloud
- Deploys from branch: `main` (`docs/DEPLOY.md`). Pushing to main does NOT deploy. Tanuj deploys
  on Frappe Cloud, and runs a migrate when a DocType or patch changes.
- Front ends that call it: the Compass app (`vcl_compass` repo) for approvals, postings and cash;
  the `/petty-cash/` web pages in `vcl_finance/www/petty-cash/`; Desk via `doc_events` in `hooks.py`.
- Open branches not on main: `hauzpay-import` (Hauz Pay statement import, `move_row`, Frappe
  version range in `pyproject.toml`) and `claude/fc-dev-pages` (one commit, 27/10).

## Working rules
- Commit as `-c user.name=Claude -c user.email=noreply@anthropic.com`.
- Never put an API key, token or password in the repo or in chat.
- Every whitelisted method checks the caller's role in its own body. Permlevel does not protect
  the RPC path (`petty_cash/entry_api.py`).
- Do not use the scheduler or `enqueue` for the sheet mirror. Frappe Cloud pauses the scheduler
  on every deploy (`hooks.py`, `petty_cash/mirror.py`).
- Tests (plain unittest, no bench, from the repo root):
  `python3 -m unittest vcl_finance.petty_cash.tests.test_weekpost_core` (36 pass) and
  `python3 -m unittest vcl_finance.book_alignment.tests.test_rules`.
  `test_monthly_vat_report` imports `frappe`, so it fails without a bench. How it is meant to run: unknown.
- Root files `PROJECT_STATUS.md`, `BLOCKERS.md`, `NEXT_ACTIONS.md`, `DECISIONS.md` are a PMO pass
  of 2026-06-07 and are stale. `PIPELINE.md` describes the `/ship` multi-agent pipeline.

## Map
| Path | What lives there |
|---|---|
| `vcl_finance/hooks.py`, `patches.txt`, `install.py` | hooks, migrate patches, after_install seeds |
| `vcl_finance/petty_cash/` | sheet, entry, posting map, QBO journal DocTypes; `api.py`, `entry_api.py`, `mirror.py`, `resolve.py`, `vat.py`, `weekpost.py` (+ `weekpost_core.py`, tested), `post.py`, `qbo.py`, `review_pack.py`, `override_review.py`, `control.py` |
| `vcl_finance/petty_cash/patches/` | petty cash migrate patches |
| `vcl_finance/www/petty-cash/` | `/petty-cash/` list, new, sheet, entry and print pages |
| `vcl_finance/book_alignment/` | crosswalk, QBO snapshot, audited FS DocTypes; QBO vs ERPNext Balances and Monthly VAT reports; `rules.py` (tested) |
| `vcl_finance/cash/` | Daily Cash Position DocTypes; `api.py` (`mpesa_balance`, `daily_position`, `record_position`) |
| `vcl_finance/ar_reconciliation/` | AR Allocation, AR Customer Recon, AR Open Entry DocTypes |
| `vcl_finance/fixtures/role.json` | the `Petty Cash User` role |
| `docs/` | deploy guide, architecture, petty cash plans, UAT/OAT round 1 |
| `supplier_ageing_2026_05/` | a one-off supplier ageing build script |
| `.claude/`, `.codex/`, `.gemini/`, `.pipeline/` | the `/ship` pipeline prompts and handoff files |
