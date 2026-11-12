# VCL Finance workspace

The `finance-dev` Web Page hosts the shared FC, Planning & Analysis and Operations interface. New views use native components. Existing receipt, cheque, petty-cash, statement and journal tools remain embedded while their handlers are retained.

`scripts/` contains the authored shell, services, native screens, VCL styles, journal adaptation, routing, verification and deployment code. Financial records and original page snapshots are **not** committed here. The original snapshots remain in the protected local `C:\Second_Brain\repos\vcl-finance-workspace\source` directory. Keep that directory and release backups outside Git.

Build and verify with Node 22:

```powershell
$env:VCL_FINANCE_SOURCE='C:\Second_Brain\repos\vcl-finance-workspace\source'
node frontend/finance-workspace/scripts/build.mjs
node frontend/finance-workspace/scripts/verify.mjs
node frontend/finance-workspace/scripts/verify-services.mjs
```

The source snapshot format is `{route, modified, name, main_section_html, javascript}` from the original Web Pages. Do not regenerate it from the redirected pages or consolidated bundle. The builder checks adaptation boundaries and JavaScript syntax. Deployment requires the established FRAPPE_URL, FRAPPE_API_KEY and FRAPPE_API_SECRET environment variables; never copy their values into files.

Run `scripts/deploy.ps1 -Phase Workspace` from this directory after verification. It backs up the current Web Page, rejects an unexpected build, writes the interface, and verifies the returned JavaScript. The local deployment directory also retains the original immutable source snapshots. `-Phase Redirects` preserves legacy queries, journal numbers, report filters and petty-cash sheet references. Original redirects do not need changing for this release.

See [the backend rollout instructions](../../docs/finance-workspace-rollout.md) before enabling shared workflows. A visible tab never grants server permissions.
