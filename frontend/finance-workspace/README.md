# VCL Finance workspace

The `finance-dev` Web Page hosts the shared FC, Planning & Analysis and Operations interface. New views use native components. Existing receipt, cheque, petty-cash, statement and journal tools remain embedded while their handlers are retained.

`scripts/` contains the authored shell, services, native screens, VCL styles, journal adaptation, routing, verification and deployment code. Financial records and original page snapshots are **not** committed here. The original snapshots remain in the protected local `C:\Second_Brain\repos\vcl-finance-workspace\source` directory. Keep that directory and release backups outside Git.

Build and verify with Node 22:

```powershell
$env:VCL_FINANCE_SOURCE='C:\Second_Brain\repos\vcl-finance-workspace\source'
node frontend/finance-workspace/scripts/build.mjs
node frontend/finance-workspace/scripts/verify.mjs
node frontend/finance-workspace/scripts/verify-services.mjs
node frontend/finance-workspace/scripts/verify-tools.mjs
```

The source snapshot format is `{route, modified, name, main_section_html, javascript}` from the original Web Pages. Do not regenerate it from the redirected pages or consolidated bundle. The builder checks adaptation boundaries and JavaScript syntax. Deployment requires the established FRAPPE_URL, FRAPPE_API_KEY and FRAPPE_API_SECRET environment variables; never copy their values into files.

Run `scripts/deploy.ps1 -Phase Workspace` from this directory after verification. It backs up the current Web Page, rejects an unexpected build, writes the interface, and verifies the returned JavaScript. The local deployment directory also retains the original immutable source snapshots. `-Phase Redirects` preserves legacy queries, journal numbers, report filters and petty-cash sheet references. Original redirects do not need changing for this release.

See [the backend rollout instructions](../../docs/finance-workspace-rollout.md) before enabling shared workflows. A visible tab never grants server permissions.

`finance-tools.js` is the catalogue and host for existing Compass and native ERPNext tools. Operations → Bank & cash offers bank receipts, payments, internal transfers, Bank Entry adjustments, payment/statement registers, statement import and reconciliation. FC → Journals & approvals → Audit groups all four current Compass Audit screens. Customer cheques open inside Finance, with their existing capture, banking and posting handlers. Supplier invoice, petty-cash, treasury, reporting, asset and dedicated payroll tools retain their original access checks.

The host adds presentation and route compatibility; it does not call save, submit, approve or posting endpoints. Native new-entry forms open as unsaved documents with the selected company and payment direction. Compass screens keep their own module/role gates; native Desk screens keep native permissions and controller rules. Existing Compass URLs and the legacy Finance routes remain valid. The selected company scopes the native form defaults; the form's own company, bank and currency fields remain visible. Compass tools are restricted to the supported Vimit company in this interface.

No Compass repository changes, native schema changes, role assignments or transaction postings are part of this frontend release. Genuine posting acceptance still requires explicitly selected transactions. Live screen checks under the explicitly authorised Administrator session verified the four bank-entry defaults, reconciliation company, blank customer-cheque capture, supplier payment-run controls and all four Audit screens. No accounting entries were saved or submitted. The 390px bank form has no horizontal page overflow and retains the company selector with a larger form viewport. Genuine posting and multi-user approval acceptance remain separate pilot work. Unit checks cover routing, safe invoice deep links, original payroll gates, correct native bank-entry defaults and delegation without save/submit calls.
