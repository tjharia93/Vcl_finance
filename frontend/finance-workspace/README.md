# VCL Finance workspace

`finance-dev` is the native Finance application for Financial Control, Planning & Analysis and Operations. It has its own forms, record views, tables and dialogs. Existing authenticated server handlers retain accounting validation, posting, approval and integration responsibility. No Compass or Desk screens are embedded.

Read [native rebuild coverage and limitations](NATIVE_REBUILD.md) and [backend rollout instructions](../../docs/finance-workspace-rollout.md).

## Build and verify

Node 22 is sufficient. Original private page snapshots are no longer build inputs.

```powershell
node frontend/finance-workspace/scripts/build.mjs
node frontend/finance-workspace/scripts/verify.mjs
node frontend/finance-workspace/scripts/verify-tools.mjs
node frontend/finance-workspace/scripts/verify-services.mjs
```

`scripts/native-shell.js` owns workspace navigation. `native-ui.js` provides shared fields, links, tables, dialogs, uploads and formatting. Separate native controllers cover documents, bank reconciliation/allocation, cheques, receipts, petty cash, Audit, supplier accounting, reporting, payroll and related records. `native.js` retains the existing native shared FC/BP&A workflows. `finance-tools.js` is a permission-aware catalogue and legacy URL mapper.

## Deployment and privacy

This is a frontend release. Python/schema changes continue through the established Frappe Cloud release/migration process; visible tabs never grant permissions.

Deployment uses the configured `FRAPPE_URL`, `FRAPPE_API_KEY` and `FRAPPE_API_SECRET` environment variables. Never store their values in files. The deployment scripts can read the original Web Page identity from `VCL_FINANCE_SOURCE`, defaulting to a local `source` directory. The protected original snapshots remain outside Git at `C:\Second_Brain\repos\vcl-finance-workspace\source`.

Build first, then run `scripts/deploy.ps1 -Phase Workspace -ExpectedModified <reviewed timestamp>`. It backs up the current page privately, rejects a concurrent edit and checks exact JavaScript read-back. Legacy redirects preserve journal numbers, report filters and petty-cash sheet references. `deploy-review.ps1` is for the temporary native QA route; unpublish that route when the canonical page is verified.

Do not commit `source`, `dist`, release backups, payroll records, bank statements or live QA snapshots. A rollback restores the immediately preceding Web Page backup only; accounting records remain intact.

Live read/form checks do not constitute acceptance of genuine financial posting. Use explicitly selected transactions for end-to-end acceptance tests.
