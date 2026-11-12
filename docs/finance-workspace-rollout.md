# Finance workspace release

## Scope and release state

`finance-dev` remains the single Finance application. The interface has three authorised workspaces, remembers the last allowed choice, and shares native tables, evidence panels, status chips, dates and monetary formatting. Retained operational tools keep their existing handlers. No named-user role assignments are migrated.

Phase 1 presents correctly separated transaction/base currencies, payment directions, permission-aware complete draft populations with paginated display, company scope, provisional ERPNext management actuals, and legacy-route compatibility. Retained tools support Vimit Converters Limited; selecting another company blocks those tools rather than reusing Vimit data. Native reports and shared records use the selected permitted company. The Q4 prototype is labelled reference-only and has no automatic assumption import.

The backend adds controlled, audited `VCL Finance Record` records for reviews, close records, reconciliation exceptions, plans, actuals snapshots and management packs. All writes use authenticated POST-only actions with role/company checks; raw record edits and deletion are denied. Review decisions use row locks, version checks and request IDs. Journal edits invalidate review, journal review cannot masquerade as ERPNext submission, and the optional submit guard requires unchanged, independent approval.

Close records preserve the original 38 steps, preparation evidence, separate reviewers, weekday targets and final policy gates. Period locking invokes native Accounting Period validation only after close sign-off, final authorisation and recorded sync compatibility. It never creates a Period Closing Voucher or an automatic first lock.

Plans keep fixed annual versions, current-month-plus-eleven forecasts, three scenarios and Monday–Sunday 13-week cash horizons. Unknown cash inputs propagate through closing cash. BP&A records contributor ownership with assumptions and submits versions; FC reviews; the final approver publishes. Published Base annual expense allocations are grouped by account/cost centre into native v16 Budgets. Other scenarios remain planning records. Revisions retain the approved predecessor and cancel/replace only its linked native Budgets in the same transaction, preserving their enforcement settings. New Budget records use Ignore actions and do not change existing spending controls.

ERPNext management actuals remain provisional. Revenue and posted costs remain usable independently of product-profitability readiness. Management packs pin actuals, budget and forecast versions and retain commentary and disclosed gaps. Explicit, audited dimension rules apply only to new invoices when phase 4 is enabled; historical corrections are outside this migration. The journal adapter preserves existing cost centres and requires deliberate income/expense dimensions.

## Deploy and migrate

1. Record the current app commit and create a Frappe Cloud backup before deployment. Keep the matching Web Page backup from `release-backups/` locally.
2. Build the established `hauzpay-import` release, with only `vcl_finance` advanced to this commit. Keep unrelated app revisions pinned. The repository's main and deployment branch are maintained together.
3. Run the normal site migration. The `before_migrate` hook creates three roles before model sync; the post-model-sync patch creates settings and additive read permissions. It assigns no users, leaves native submission permissions intact, and leaves phases 2, 3 and 4 disabled. The optional journal guard and sync-lock attestation default off.
4. Verify `vcl_finance.workspace.api.capabilities` for the pilot accounts and each permitted company. Confirm the two new DocTypes exist, old petty-cash hooks still run, the scheduler is resumed if Frappe Cloud paused it, and existing operational pages load.
5. Complete the acceptance checks below before enabling each phase. Never use genuine transactions for end-to-end posting tests unless explicitly selected by the user.

## Permissions and phase release

The new additive roles are `VCL Financial Controller`, `VCL Financial Planner`, and `VCL Finance Operator`. Assign them explicitly during the administrative pilot. Existing native transaction roles remain necessary for preparation, submission and payment decisions. Company User Permissions still apply. Planner permissions do not include journal submission, payment approval, account maintenance or payroll document access. Planner-only GL source access is restricted to the aggregate reporting gateway; raw GL document/list access is denied so the new role cannot expose individual payroll or bank rows. Existing permissions granted by other roles remain authoritative. Existing payroll restrictions and integration credentials are unchanged.

FC includes Accounts Manager and System Manager for compatibility; BP&A includes System Manager for administration. These administrative fallbacks are not the normal rollout model. Final approval is seeded to the existing configured business approver, `tanuj.haria@vimit.com`.

The final approver with System Manager access uses the **Apply release configuration** action in the Finance Workspace Settings form (authenticated POST `vcl_finance.workspace.api.configure_release`), supplying `company`, a `phases` object, and a written `attestation` identifying acceptance evidence. Optional `journal_review_required` and `sync_respects_locks` flags require separate acceptance evidence. Phase switches are independent. Shared record history remains readable when a workflow phase is disabled.

`dimension_rules` can contain reviewed entries such as:

```json
[{"company":"<permitted company>","doctype":"Sales Invoice","match_field":"item_group","match_value":"<configured item group>","cost_center":"<enabled company cost centre>"}]
```

Supported matches are item code, item group, income account and expense account. Existing deliberate dimensions and historical documents are preserved. Do not set `sync_respects_locks` until both the backdated integration path and native period restrictions have been verified. Native Budget publication checks the live account-level v16 schema; incompatible schemas fail without changing budgets. [ERPNext's native Budget implementation](https://github.com/frappe/erpnext/blob/version-16/erpnext/accounts/doctype/budget/budget.py) regenerates initial distributions, so this implementation saves and verifies approved monthly values before submission.

## Acceptance evidence

Local verification passes 129 Python tests across workspace policies/gateway doubles and existing petty-cash/book-alignment arithmetic. JavaScript verification covers 21 legacy routes, syntax, preserved journal handlers, iframe routing, missing values, mixed currencies, 1,501 draft entries, full pagination, payment directions, denied capabilities, negative formatting and planning horizons. Gateway tests are isolated doubles; they do not replace live Frappe integration tests.

Before phase 2: use an isolated test site to test two distinct preparer/reviewer accounts, a reviewer who edited the source, changed-journal invalidation through Desk/API/import, forbidden direct actions, stale revisions, lost-response retries, native approvals, task evidence, final close sign-off and locked-period posting. Verify payroll isolation and company permissions with actual pilot roles.

Before phase 3: verify two simultaneous users read the same persisted version; approved baselines remain unchanged; revisions preserve originals; scenarios remain separate; fiscal years match company configuration; rolling horizons are correct; uneven monthly amounts survive native Budget validation; and any pre-existing enforcement settings remain unchanged after revision. Record business-owner assumptions under their actual owner rather than silently importing Q4 browser storage. Review the existing VCL Cashflow panel and its source assumptions during the cash pilot; it is not an approved version of the new finance plan and must not be silently imported or treated as a second planning authority.

Before phase 4: reconcile source reports using identical company, period, currency and dimensions; validate snapshot consistency, prior-period comparisons and pack exports; verify future invoice mappings on the actual sync path. Product profitability stays provisional until FC separately resolves material cost and allocation gaps. No reviewed historical backfill is included.

Browser acceptance includes desktop, tablet and a 390px phone; keyboard focus, unsaved-work warnings, contained statement scrolling, exports and old journal/report/petty-cash deep links. Record designated FC, BP&A and Operations pilot users before enabling workflows.

## Monitoring and rollback

`vcl_finance_workspace.log` records failed endpoint names, actors and exception classes without financial payloads, evidence or credentials. Work queues expose unresolved exceptions, pending native decisions and accessible failed/pending QuickBooks Bill postings. Native queue permissions remain in force. Frontend failures remain visible to the user; retry never marks an accounting document as posted.

Disable the affected phase and optional journal gate through the release action before rolling back backend code. Leave evidence records and schema in place; do not delete audit history or reverse genuine posted transactions as part of a software rollback. Restore the immediately preceding Web Page backup with a modified/build check, or use the original-page restore only for a full interface rollback. Roll back to the recorded prior app commit through Frappe Cloud. Reconcile any actions taken between release and rollback separately.

The completion standard remains subject to migration, the named-user pilot, source-report reconciliation and user-selected posting acceptance. A disabled workflow is implemented code awaiting release evidence, not a claim that an end-to-end live process has passed.
