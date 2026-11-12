# Native Finance rebuild

The user rejected the embedded Compass/Desk approach. `finance-dev` now builds its own Finance views and forms. The runtime contains no frames, copied Compass components, legacy Web Page bundles, `srcdoc` or Desk form startup. The existing authenticated server handlers remain responsible for accounting and approvals.

## Workflow coverage

| Area | Native Finance interface | Existing authority |
|---|---|---|
| FC | Work queue, close tasks/evidence/history, independent review, exceptions, period-lock requests | `vcl_finance.workspace.api` |
| BP&A | Performance, approved budget versions, rolling forecast, 13-week cash plan, profitability qualifications, management packs | `vcl_finance.workspace.api` |
| Bank entries | Receive, Pay, Internal Transfer, Bank Entry journals; searchable account links, invoice-to-payment builder, currency/rate lookup, draft save and separate submission | ERPNext controllers and native permissions |
| Bank statements | Transaction register, file attachment, import preview/mapping, import status/error exports and explicit start; voucher matching | ERPNext bank import and reconciliation methods |
| Payment allocation | Customer/supplier candidates, selected payment/invoice allocation, server preview, separate confirmation | ERPNext Payment Reconciliation |
| Customer cheques | Capture, hold, invoice selection/CUIN verification, banking stage, GL impact, post, unstage, bounce, re-present, return | `vcl_compass.api.cheques` |
| Supplier cheques | Runs, real leaf allocation only on capture, bill allocation, FX/own-account purposes, approval, release, posting preview/evidence, presentation, stop/void, books/gaps/spoiled leaves, unpresented ageing | `vcl_compass.api.supplier_cheques` |
| Receipts | Cash/M-Pesa entry, allocation, draft, server preview and idempotent post request, history/cancel/amend, M-Pesa transfers/checks, customer duplicate review/completion | Existing `vcl_receipts_v1` action API |
| Petty cash | Weekly sheets, append entry, evidence, void/reinstate/move, controls, close/approve/reopen, entry approval/receipt/VAT/account review, posting previews, ERP draft journals, QBO staging, Hauz-Pay import | Existing `vcl_finance.petty_cash` APIs |
| Audit | Invoice review/corrections/order allocations/CUIN/stock-close decision, credit-note decisions, price decisions, sync-failure investigation/CSRA repair | Existing gated Compass APIs |
| Supplier accounting | Paged invoices, bill from PO with calculated totals, edits, ERP submission, attachments, KRA/date evidence, cancellation preview, QBO review/prepare/post/retry/vendor mapping | `vcl_procurement.api.pi` and protected QBO integration |
| Reporting | Native report filters/tables/exports, financial statements, AR/coverage/as-at/customer journey, sales, petty cash, treasury, import commitments, VAT and book comparison | Source reports/APIs; no duplicate ledger calculations |
| Imports | Shipment list/detail, voyage edits and explicit milestones; finance cost evidence | `vcl_imports.api.shipments` |
| Restricted payroll | Monthly input changes, exceptions and controlled transitions; staff loan book/movements; payroll PDF control register/comparison; payslips/additional salary | Existing payroll/HR APIs and dedicated module permissions |
| Setup/assets | Account/cost-centre/item accounting and asset record forms; child rows, attachments, immutable fields, scoped links; restricted rename | Native permissions/controllers |

The catalogue is a list of workflows, not a claim that this replaces every ERPNext module. Procurement's sales/production/order-raising applications are outside this Finance rebuild. Asset disposal/depreciation posting extensions are not introduced by this frontend release.

## Controls and disclosed limitations

- Workspace switching never grants permissions. Original Compass module/role gates, payroll restrictions, native document permissions and existing Finance release capabilities remain in force.
- Generic document drafts use native `save`; submitted documents and metadata-read-only child tables cannot be edited through the draft UI. Existing documents retain `modified` and native concurrent-save checks. Submission requires a saved draft and a fresh timestamp check.
- Existing approval/posting APIs retain their own transaction/idempotency rules. No automatic retries are added to financial mutations. Ambiguous network failures require checking the relevant register before retrying.
- Sources with a fixed Vimit company are explicitly restricted to that company. Audit invoice candidates are intersected with permission-aware, company-scoped native results. Document deep links verify the record company.
- Payment registers show both account currencies and amounts plus company-base values. Receive, Pay and transfers remain separate. Missing values stay unavailable.
- **KES customer cheque → USD bank is blocked in Finance.** The existing cheque handler sets equal paid/received amounts and 1:1 rates, while the configured destination is USD. Its backend needs currency-aware support before this path can be enabled. This frontend guard does not change the existing Compass endpoint.
- Q4 prototype assumptions are a labelled, read-only browser-local reference for authorised setup users. Original dated source snapshots remain in the private archive. No values are imported into approved plans.
- Payroll PDF transcription and QuickBooks pushes continue through their existing protected processes. The UI does not hold credentials or pretend a queued response is a completed posting.

## Verification and release

Node checks cover the 21 legacy routes, all catalogue controllers, role isolation, monetary/date formatting, escaped content, CSV safety, read-only field/table behavior, the incompatible cheque currency path, pagination beyond 500 records and absence of frames/legacy bundles. The unchanged finance/controls backend suite has 129 passing tests.

Browser checks use Administrator as explicitly requested. They open native views and record details, run a source Trial Balance, exercise local unsaved-work protection and validate a 390px phone layout. Wide statements scroll within their container; the page itself does not overflow. No test saves, submissions, approvals, cheque issues or postings are performed against genuine records. Genuine posting, multi-user approvals, file imports and integration completion still need explicitly selected acceptance transactions.

The release is frontend-only; it does not require a Python/schema migration. Publish using `deploy.ps1 -Phase Workspace -ExpectedModified <reviewed timestamp>`. The script saves a private backup, checks the version and verifies exact JavaScript read-back. Existing redirects remain unchanged. Unpublish the temporary `finance-native-review` page after checking the canonical Finance route.

Rollback restores only the `finance-dev` Web Page HTML/JavaScript from the immediately preceding private release backup. It does not roll back transactions or Finance records. Do not use an old page snapshot as an accounting rollback.
