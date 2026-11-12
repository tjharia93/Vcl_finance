"""Authenticated finance gateway. All public methods resolve roles AND company.

Records are append-audited application state, never an alternate accounting ledger.
Native journals, payments, budgets and period locks retain native validation.
"""
import json
import logging
from functools import wraps
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import frappe
from frappe.utils import getdate, now, nowdate

from vcl_finance.workspace import core

RECORD = "VCL Finance Record"
SETTINGS = "VCL Finance Workspace Settings"
FC_ROLES = {"VCL Financial Controller", "Accounts Manager", "System Manager"}
BPA_ROLES = {"VCL Financial Planner", "System Manager"}
OPS_ROLES = {"VCL Finance Operator", "Accounts User", "Petty Cash User", "Accounts Manager", "System Manager"}
PREFIX = "vcl_finance.workspace.api."



def _observed(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            # Metadata only: never log amounts, evidence, credentials or payloads.
            logger = frappe.logger("vcl_finance_workspace", allow_site=True) if hasattr(frappe, "logger") else logging.getLogger("vcl_finance_workspace")
            logger.warning("finance_request_failed method=%s user=%s error=%s", fn.__name__, frappe.session.user, type(exc).__name__)
            if type(exc) is PermissionError:
                frappe.throw(str(exc), frappe.PermissionError)
            if type(exc) is ValueError:
                frappe.throw(str(exc))
            raise
    return wrapped

def _roles(user=None):
    return set(frappe.get_roles(user or frappe.session.user))


def _caps(user=None):
    r = _roles(user)
    return {"fc": bool(r & FC_ROLES), "bpa": bool(r & BPA_ROLES), "ops": bool(r & OPS_ROLES), "setup": bool(r & {"Accounts Manager", "System Manager"})}


def _settings():
    return frappe.get_single(SETTINGS)


def _companies():
    return frappe.get_list("Company", fields=["name", "default_currency"], limit_page_length=0, order_by="name")


def _ctx(company=None, require=None, phase=1):
    if frappe.session.user == "Guest":
        frappe.throw("Sign in to Finance.", frappe.PermissionError)
    caps = _caps()
    if not any(caps.values()) or (require and not caps.get(require)):
        frappe.throw("This finance action is not open to your role.", frappe.PermissionError)
    settings = _settings()
    if phase is not None and not settings.get(f"phase_{phase}"):
        frappe.throw(f"Finance phase {phase} has not been released.", frappe.PermissionError)
    companies = _companies()
    allowed = {x.name: x.default_currency for x in companies}
    company = company or ("Vimit Converters Limited" if "Vimit Converters Limited" in allowed else next(iter(allowed), None))
    if company not in allowed:
        frappe.throw("This company is not open to your account.", frappe.PermissionError)
    return {"company": company, "currency": allowed[company], "user": frappe.session.user, "caps": caps,
            "final": settings.final_approver == frappe.session.user, "settings": settings}


def _json(value, default=None):
    return json.loads(value) if isinstance(value, str) else value if value is not None else default


def _evidence(value):
    items = _json(value, [])
    if not isinstance(items, list) or any(not isinstance(i, str) or not i.strip() for i in items):
        frappe.throw("Evidence must be a list of document or file references.")
    return list(dict.fromkeys(i.strip() for i in items))


def _allowed(kind, c):
    return kind in (core.READ_FC if c["caps"]["fc"] else set()) | (core.READ_BPA if c["caps"]["bpa"] else set()) | (core.READ_OPS if c["caps"]["ops"] else set())


def _public(doc):
    return {"name": doc.name, "kind": doc.kind, "title": doc.title, "company": doc.company, "status": doc.status,
            "revision": doc.revision, "modified": str(doc.modified), "owner": doc.owner,
            "data": _json(doc.payload_json, {})}


def _load(name, c, revision=None):
    # The lock serializes competing edits, review transitions and retries.
    frappe.db.sql(f"SELECT name FROM `tab{RECORD}` WHERE name=%s FOR UPDATE", (name,))
    d = frappe.get_doc(RECORD, name)
    if d.company != c["company"] or not _allowed(d.kind, c):
        frappe.throw("Record not open to this account or company.", frappe.PermissionError)
    if not c["caps"]["fc"] and not c["caps"]["bpa"] and d.owner != c["user"]:
        frappe.throw("Operations users may access only their own finance records.", frappe.PermissionError)
    if revision is not None and int(revision) != d.revision:
        frappe.throw("This record changed. Reload it before saving.", frappe.TimestampMismatchError)
    return d


def _store(c, kind, title, data, d=None, status=None, event="Saved"):
    if kind not in core.KINDS:
        frappe.throw("Unknown finance record type.")
    if not d:
        d = frappe.new_doc(RECORD)
        d.kind, d.company, d.title, d.status, d.revision = kind, c["company"], title, "Draft", 0
    data = dict(data)
    data.setdefault("history", []).append({"event": event, "by": c["user"], "at": now(), "revision": d.revision + 1})
    d.payload_json = frappe.as_json(data)
    d.source_document = data.get("document")
    d.source_doctype = data.get("doctype")
    if kind in {"Close", "Review"}:
        d.identity = kind + "|" + c["company"] + "|" + (data.get("document") or title)
    d.revision += 1
    if status:
        d.status = status
    from vcl_finance.workspace.guards import allow_record_write
    with allow_record_write():
        d.save(ignore_permissions=True)  # Only after the gateway's role/company/policy checks.
    return _public(d)


def _rows(doctype, filters, fields):
    """Stream every permitted row. No raw SQL totals or silently truncated lists."""
    start = 0
    while True:
        page = frappe.get_list(doctype, filters=filters, fields=fields, order_by="name", limit_start=start, limit_page_length=1000)
        yield from page
        if len(page) < 1000:
            break
        start += len(page)


def _basis(c, start=None, end=None):
    return {"company": c["company"], "currency": c["currency"], "period_start": start, "period_end": end,
            "source": "ERPNext", "read_at": now(), "review_status": "Provisional management",
            "notes": ["ERPNext-led management view. Costs and dimensions may be incomplete; QuickBooks statutory figures are separate."]}


@frappe.whitelist()
@_observed
def capabilities(company=None):
    c = _ctx(company, phase=None)
    return {"api_version": 1, "user": c["user"], "capabilities": c["caps"], "final_approver": c["final"],
            "companies": _companies(), "company": c["company"], "currency": c["currency"],
            "phases": {str(n): bool(c["settings"].get(f"phase_{n}")) for n in range(1, 5)},
            "basis": _basis(c)}


@frappe.whitelist()
@_observed
def records(company=None, kind=None, start=0, page_length=50):
    c = _ctx(company)
    kinds = [kind] if kind else sorted(k for k in core.KINDS if _allowed(k, c))
    if any(not _allowed(k, c) for k in kinds):
        frappe.throw("This record type is not open to your role.", frappe.PermissionError)
    filters = {"company": c["company"], "kind": ["in", kinds]}
    if not c["caps"]["fc"] and not c["caps"]["bpa"]:
        filters["owner"] = c["user"]
    docs = frappe.get_all(RECORD, filters=filters, fields=["name", "kind", "title", "status", "revision", "modified", "owner", "payload_json"],
                          order_by="modified desc", limit_start=max(0, int(start)), limit_page_length=min(100, max(1, int(page_length))))
    return {"rows": [_public(frappe._dict({**d, "company": c["company"]})) for d in docs], "total": frappe.db.count(RECORD, filters)}


@frappe.whitelist()
@_observed
def drafts(company=None, kind="je", start=0, page_length=50, query=""):
    c = _ctx(company)
    if not (c["caps"]["fc"] or c["caps"]["ops"]):
        frappe.throw("Draft transactions are not part of the BP&A permission set.", frappe.PermissionError)
    configs = {
        "je": ("Journal Entry", ["voucher_type", "user_remark", "total_debit"]),
        "pi": ("Purchase Invoice", ["supplier_name", "bill_no", "currency", "grand_total", "base_grand_total"]),
        "si": ("Sales Invoice", ["customer_name", "currency", "grand_total", "base_grand_total"]),
        "pe": ("Payment Entry", ["payment_type", "party_name", "paid_amount", "received_amount", "base_paid_amount", "base_received_amount", "paid_from_account_currency", "paid_to_account_currency"]),
    }
    if kind not in configs:
        frappe.throw("Unknown transaction type.")
    dt, fields = configs[kind]
    rows = list(_rows(dt, {"company": c["company"], "docstatus": 0}, ["name", "posting_date", "owner", "modified"] + fields))
    rows.sort(key=lambda x: (str(x.posting_date), x.name))
    if query:
        terms = str(query).lower().split()
        rows = [r for r in rows if all(t in " ".join(str(v) for v in r.values()).lower() for t in terms)]
    summary = core.summarize_drafts(rows, kind, c["currency"])
    offset, size = max(0, int(start)), min(100, max(1, int(page_length)))
    return {"rows": rows[offset:offset + size], "total": len(rows), "summary": summary, "basis": {**_basis(c), "review_status": "Operational"}}


@frappe.whitelist()
@_observed
def work_queue(company=None, own=0):
    c = _ctx(company)
    own = bool(int(own))
    out = []
    pending, offset = [], 0
    while True:
        page = records(c["company"], start=offset, page_length=100)
        pending.extend(page["rows"])
        offset += len(page["rows"])
        if offset >= page["total"] or not page["rows"]:
            break
    for r in pending:
        if r["status"] in {"Published", "Posted", "Resolved", "Closed"}:
            continue
        data = r["data"]
        if r["kind"] == "Close":
            for task in data.get("tasks", []):
                if task["status"] not in {"Reviewed", "Not applicable"} and ((c["caps"]["fc"] and not own) or task.get("owner") == c["user"]):
                    out.append({"name": r["name"], "kind": "Close task", "title": task["title"], "owner": task.get("owner"), "due_date": task.get("due_date"), "status": task["status"], "route": "fc/close?record=" + r["name"]})
        else:
            if own and c["user"] not in {r["owner"], data.get("reviewer"), data.get("owner")}:
                continue
            out.append({"name": r["name"], "kind": r["kind"], "title": r["title"], "owner": data.get("reviewer") or r["owner"], "due_date": data.get("due_date"), "status": r["status"], "route": "fc/approvals?record=" + r["name"]})
    # Native approvals remain native: links are read using the current user's permissions.
    for dt, fields, route in [("Petty Cash Sheet", ["name", "status", "owner", "week_ending"], "payments/petty"),
                               ("VCL Supplier Cheque", ["name", "status", "owner", "cheque_date"], "payments/cheques")]:
        if frappe.has_permission(dt, "read"):
            try:
                if not frappe.get_meta(dt).has_field("company"):
                    continue  # Never merge an unscoped queue into company totals.
                filters = {"company": c["company"]}
                for row in _rows(dt, filters, fields):
                    if row.status in {"Draft", "Submitted", "Drafted", "Approved", "Released"} and ((c["caps"]["fc"] and not own) or row.owner == c["user"]):
                        out.append({"name": row.name, "kind": dt, "title": row.name, "owner": row.owner, "due_date": str(row.get("week_ending") or row.get("cheque_date") or ""), "status": row.status, "route": route})
            except frappe.DoesNotExistError:
                pass
    if c["caps"]["fc"] and not own and frappe.db.exists("DocType", "QBO Bill Push Queue") and frappe.has_permission("QBO Bill Push Queue", "read"):
        meta = frappe.get_meta("QBO Bill Push Queue")
        invoice_field = next((f.fieldname for f in meta.fields if f.fieldtype == "Link" and f.options == "Purchase Invoice"), None)
        if meta.has_field("company") or invoice_field:
            fields = ["name", "push_status", "modified", "owner"] + ([invoice_field] if invoice_field else [])
            filters = {"company": c["company"]} if meta.has_field("company") else {}
            for row in _rows("QBO Bill Push Queue", filters, fields):
                if row.push_status not in {"Pending", "Failed", "Blocked"}:
                    continue
                if invoice_field:
                    invoice = frappe.get_doc("Purchase Invoice", row.get(invoice_field))
                    if invoice.company != c["company"] or not frappe.has_permission("Purchase Invoice", "read", invoice):
                        continue
                out.append({"name": row.name, "kind": "QuickBooks Bill posting", "title": row.name, "owner": row.owner,
                            "due_date": None, "status": row.push_status, "route": "close/reconciliation"})
    return {"rows": out, "basis": _basis(c)}


@frappe.whitelist()
@_observed
def actuals(company=None, period_start=None, period_end=None):
    c = _ctx(company)
    if not (c["caps"]["fc"] or c["caps"]["bpa"]):
        frappe.throw("Management reporting requires an FC or BP&A role.", frappe.PermissionError)
    start, end = getdate(period_start or nowdate()[:7] + "-01"), getdate(period_end or nowdate())
    if end < start or (end - start).days > 740:
        frappe.throw("Choose a valid reporting period of at most two years.")
    accounts = {a.name: a for a in _rows("Account", {"company": c["company"], "is_group": 0}, ["name", "account_name", "root_type"])}
    grouped = defaultdict(lambda: Decimal(0))
    tagged, main = 0, 0
    from vcl_finance.workspace.guards import allow_management_gl
    with allow_management_gl():
        for r in _rows("GL Entry", {"company": c["company"], "is_cancelled": 0, "voucher_type": ["!=", "Period Closing Voucher"], "posting_date": ["between", [start, end]]}, ["account", "cost_center", "posting_date", "debit", "credit"]):
            a = accounts.get(r.account)
            if not a or a.root_type not in {"Income", "Expense"}:
                continue
            if r.cost_center:
                tagged += 1
                if r.cost_center.startswith("Main -"):
                    main += 1
            amount = core.decimal_amount(r.credit) - core.decimal_amount(r.debit) if a.root_type == "Income" else core.decimal_amount(r.debit) - core.decimal_amount(r.credit)
            grouped[(str(r.posting_date)[:7], r.account, r.cost_center or "Unallocated", "Revenue" if a.root_type == "Income" else "Expense")] += amount
    rows = [{"period": k[0], "account": k[1], "cost_center": k[2], "category": k[3], "amount": float(v)} for k, v in sorted(grouped.items())]
    return {"basis": _basis(c, str(start), str(end)), "rows": rows,
            "totals": {k: float(sum((v for key, v in grouped.items() if key[3] == k), Decimal(0))) for k in ["Revenue", "Expense"]},
            "quality": {"tagged_lines": tagged, "main_lines": main, "product_profitability_ready": False,
                        "gaps": _json(c["settings"].reporting_gaps_json, ["Significant costs remain in QuickBooks; product allocations require review."])}}


@frappe.whitelist(methods=["POST"])
@_observed
def save_record(company, kind, title, data, name=None, revision=None):
    c = _ctx(company, phase=3 if kind == "Plan" else 4 if kind in {"Snapshot", "Pack"} else 2)
    p = _json(data, {})
    if kind == "Review":
        frappe.throw("Use submit_for_review for native accounting documents.")
    if not _allowed(kind, c) or kind in {"Close", "Snapshot", "Pack"}:
        frappe.throw("Use the dedicated creation action for this record type.", frappe.PermissionError)
    if name and revision is None:
        frappe.throw("A revision number is required when editing a record.")
    d = _load(name, c, revision) if name else None
    if d and d.kind != kind:
        frappe.throw("A record's type cannot be changed.")
    if d and d.status in {"Approved", "Published", "Posted", "Closed"}:
        frappe.throw("Approved versions are immutable. Create a revision instead.")
    if kind == "Plan":
        if not c["caps"]["bpa"]:
            frappe.throw("A Financial Planner prepares planning versions.", frappe.PermissionError)
        core.validate_plan(p)
        if p["plan_type"] == "Cash forecast":
            if any(l["category"] not in {"Opening cash", "Collection", "Payment", "Financing"} for l in p["lines"]):
                frappe.throw("Cash forecasts use opening cash, collections, payments and financing.")
            p["cash_outlook"] = core.cash_outlook(p)
        if not d and not p.get("revision_of") and p["plan_type"] == "Rolling forecast" and p["periods"][0]["start"] != nowdate()[:7] + "-01":
            frappe.throw("A new rolling forecast starts in the current month.")
        for line in p.get("lines", []):
            if line.get("account"):
                account = frappe.get_doc("Account", line["account"])
                account.check_permission("read")
                if account.company != c["company"] or account.is_group or account.disabled:
                    frappe.throw("Choose an enabled ledger account of this company.")
                if p["plan_type"] != "Cash forecast" and account.root_type != {"Revenue": "Income", "Expense": "Expense"}.get(line["category"]):
                    frappe.throw("Revenue and expense planning lines must match their ledger account type.")
            if line.get("cost_center"):
                cc = frappe.get_doc("Cost Center", line["cost_center"])
                cc.check_permission("read")
                if cc.company != c["company"] or cc.is_group or cc.disabled:
                    frappe.throw("Choose an enabled company ledger cost centre.")
        if p["plan_type"] == "Annual budget":
            fy = frappe.get_doc("Fiscal Year", p.get("fiscal_year"))
            fy.check_permission("read")
            fy_companies = frappe.get_all("Fiscal Year Company", filters={"parent": fy.name}, pluck="company")
            if fy_companies and c["company"] not in fy_companies:
                frappe.throw("This fiscal year is not configured for the selected company.")
            if str(fy.year_start_date) != p["periods"][0]["start"] or str(fy.year_end_date) != p["periods"][-1]["end"]:
                frappe.throw("Annual budget periods must match the configured fiscal year.")
        p["currency"] = c["currency"]
        if p.get("revision_of"):
            original = _load(p["revision_of"], c)
            if original.kind != "Plan" or original.status not in {"Approved", "Published"}:
                frappe.throw("Revise an approved or published planning version.")
            prior = _json(original.payload_json, {})
            if prior["plan_type"] != p["plan_type"] or prior["scenario"] != p["scenario"] or prior["periods"] != p["periods"]:
                frappe.throw("A revision retains the original type, scenario and periods.")
    elif kind == "Exception":
        if not c["caps"]["fc"]:
            frappe.throw("A Financial Controller maintains reconciliation exceptions.", frappe.PermissionError)
        if not p.get("owner") or not p.get("due_date") or not p.get("explanation"):
            frappe.throw("Record an owner, due date and explanation.")
        if not _caps(p["owner"])["fc"] or not frappe.db.get_value("User", p["owner"], "enabled"):
            frappe.throw("Assign an enabled Financial Controller as the accountable reconciliation owner.")
        p["evidence"] = _evidence(p.get("evidence"))
        getdate(p["due_date"])
        if p.get("amount") is not None:
            p["amount"] = float(core.money(p["amount"]))
        p["currency"] = c["currency"]
    previous_data = _json(d.payload_json, {}) if d else {}
    for field in ["history", "requests", "reviewed_by", "reviewed_at", "review_status", "native_budgets", "published_by", "published_at"]:
        p.pop(field, None)
    p["history"] = previous_data.get("history", [])
    p["preparer"] = previous_data.get("preparer", c["user"])
    p["preparers"] = sorted(set(previous_data.get("preparers", [p["preparer"]])) | {c["user"]})
    return _store(c, kind, title, p, d, "Draft", "Draft saved; any prior review invalidated")


@frappe.whitelist(methods=["POST"])
@_observed
def submit_for_review(company, journal, reviewer, purpose, evidence, revision=None):
    c = _ctx(company, phase=2)
    if not (c["caps"]["ops"] or c["caps"]["fc"]):
        frappe.throw("A finance preparer must request review.", frappe.PermissionError)
    doc = frappe.get_doc("Journal Entry", journal)
    doc.check_permission("write")
    if doc.company != c["company"] or doc.docstatus != 0:
        frappe.throw("Choose a draft journal in this company.")
    makers = {c["user"], doc.owner, doc.modified_by}
    if reviewer in makers or not _caps(reviewer)["fc"] or not frappe.db.get_value("User", reviewer, "enabled"):
        frappe.throw("Choose a different Financial Controller as reviewer.")
    evidence = _evidence(evidence)
    if not purpose.strip() or not evidence:
        frappe.throw("Explain the journal and attach supporting evidence.")
    previous = frappe.db.get_value(RECORD, {"company": c["company"], "kind": "Review", "source_document": journal}, "name")
    if previous and revision is None:
        frappe.throw("Reload the existing review and supply its revision before resubmitting.")
    d = _load(previous, c, revision) if previous else None
    prior = _json(d.payload_json, {}) if d else {}
    makers |= set(prior.get("preparers", []))
    if reviewer in makers:
        frappe.throw("A previous preparer cannot review this journal.", frappe.PermissionError)
    return _store(c, "Review", journal, {"doctype": "Journal Entry", "document": journal, "preparer": c["user"], "reviewer": reviewer,
        "preparers": sorted(makers), "history": prior.get("history", []), "purpose": purpose, "evidence": evidence, "document_hash": core.fingerprint(doc.as_dict())}, d, "In review", "Review requested")


@frappe.whitelist(methods=["POST"])
@_observed
def transition_record(company, name, revision, action, comment="", request_id=None):
    c = _ctx(company)
    d = _load(name, c)
    p = _json(d.payload_json, {})
    if action not in {"submit", "return", "approve", "publish"} or (d.kind == "Review" and action == "publish"):
        frappe.throw("Use the native accounting action to submit or post a journal.")
    if action == "submit" and c["user"] != p.get("preparer", d.owner):
        frappe.throw("Only the preparer may submit this version for review.", frappe.PermissionError)
    if action in {"approve", "return"} and c["user"] in p.get("preparers", []):
        frappe.throw("A journal preparer cannot review their own work.", frappe.PermissionError)
    if action == "publish" and not c["caps"]["fc"]:
        frappe.throw("FC review permission is required to publish.", frappe.PermissionError)
    # Same request after a lost response returns the committed result.
    if request_id and request_id in p.get("requests", {}):
        return _public(d)
    if int(revision) != d.revision:
        frappe.throw("This record changed. Reload before deciding.", frappe.TimestampMismatchError)
    if d.kind in {"Plan", "Pack", "Snapshot"}:
        _ctx(company, phase=3 if d.kind == "Plan" else 4)
    else:
        _ctx(company, phase=2)
    if action in {"return", "approve"} and not comment.strip():
        frappe.throw("Record your review conclusion.")
    if p.get("reviewer") and action in {"return", "approve"} and p["reviewer"] != c["user"]:
        frappe.throw("This review is assigned to a different reviewer.", frappe.PermissionError)
    if d.kind == "Review":
        native = frappe.get_doc(p["doctype"], p["document"])
        native.check_permission("read")
        if native.docstatus != 0 or core.fingerprint(native.as_dict()) != p["document_hash"]:
            frappe.throw("The journal changed or is no longer a draft. Request a fresh review.")
    if d.kind == "Exception" and action == "publish":
        if not c["caps"]["fc"] or not p.get("evidence"):
            frappe.throw("FC resolution requires evidence.")
    if d.kind == "Plan" and action in {"approve", "publish"}:
        incomplete = any(w["closing"] is None for w in core.cash_outlook(p)) if p["plan_type"] == "Cash forecast" else any(v is None for line in p.get("lines", []) for v in line["values"])
        if incomplete:
            frappe.throw("Resolve unknown plan values before approval or publication.")
        if p["plan_type"] != "Cash forecast" and any(l["category"] == "Expense" and not l.get("cost_center") for l in p["lines"]):
            frappe.throw("Assign expense plan cost centres before financial review.")
    status = core.transition(d.status, action, c["user"], p.get("preparer", d.owner), fc=c["caps"]["fc"], final=c["final"], final_required=d.kind in {"Plan", "Close"})
    if action == "publish" and d.kind == "Close":
        _assert_close_ready(c, p)
    if action == "publish" and d.kind == "Plan":
        if p["plan_type"] == "Annual budget" and p["scenario"] == "Base":
            p["native_budgets"] = _publish_budget(c, p, d.name)
    p["review_comment"] = comment
    if action in {"approve", "return"}:
        p["reviewed_by"], p["reviewed_at"] = c["user"], now()
    if action == "approve":
        p["review_status"] = "FC-reviewed"
    if action == "publish":
        p["published_by"], p["published_at"] = c["user"], now()
    if request_id:
        p.setdefault("requests", {})[request_id] = {"action": action, "at": now()}
    return _store(c, d.kind, d.title, p, d, "Resolved" if d.kind == "Exception" and status == "Published" else "Closed" if d.kind == "Close" and status == "Published" else status, action)


@frappe.whitelist(methods=["POST"])
@_observed
def open_close(company, month):
    c = _ctx(company, "fc", 2)
    ps = core.periods(str(month)[:7] + "-01", 1)[0]
    key = f"Close {ps['start'][:7]}"
    existing = frappe.db.get_value(RECORD, {"kind": "Close", "company": company, "title": key}, "name")
    if existing:
        return _public(_load(existing, c))
    return _store(c, "Close", key, {"period": ps, "preparer": c["user"], "tasks": core.close_tasks(ps["end"]), "evidence": []})


@frappe.whitelist(methods=["POST"])
@_observed
def save_close_task(company, name, revision, task_id, status, owner, evidence, comment=""):
    c = _ctx(company, "fc", 2)
    d = _load(name, c, revision)
    if d.kind != "Close" or d.status in {"Closed", "Published"}:
        frappe.throw("Choose an open close record.")
    p = _json(d.payload_json, {})
    task = next((t for t in p["tasks"] if t["id"] == str(task_id)), None)
    if not task or status not in {"Open", "Prepared", "Reviewed", "Not applicable"}:
        frappe.throw("Choose a valid task and status.")
    if not owner:
        frappe.throw("Assign an owner.")
    evidence = _evidence(evidence)
    if status in {"Prepared", "Reviewed", "Not applicable"} and (not evidence or not comment.strip()):
        frappe.throw("Record evidence and a conclusion.")
    if status in {"Reviewed", "Not applicable"} and c["user"] in {owner, task.get("prepared_by")}:
        frappe.throw("Someone other than the task owner must review it.", frappe.PermissionError)
    if status in {"Reviewed", "Not applicable"} and (task["status"] != "Prepared" or task.get("owner") != owner or task.get("evidence") != evidence):
        frappe.throw("Prepare the task and evidence first. Review cannot replace its preparer, owner or evidence.")
    if task.get("final_required") and status in {"Reviewed", "Not applicable"} and not c["final"]:
        frappe.throw("This policy decision requires Tanuj's authorisation.", frappe.PermissionError)
    task.update(status=status, owner=owner, evidence=evidence, comment=comment, reviewed_by=c["user"] if status == "Reviewed" else None, reviewed_at=now())
    if status == "Prepared":
        task["prepared_by"], task["prepared_at"] = c["user"], now()
    if status == "Not applicable":
        task["reviewed_by"] = c["user"]
    return _store(c, "Close", d.title, p, d, "Draft", "Close task updated; final approval invalidated")


def _assert_close_ready(c, p):
    if not all(t["status"] in {"Reviewed", "Not applicable"} and t.get("evidence") for t in p["tasks"]):
        frappe.throw("Every close task needs reviewed evidence or a documented not-applicable conclusion.")
    if frappe.db.exists(RECORD, {"company": c["company"], "kind": "Exception", "status": ["!=", "Resolved"]}):
        frappe.throw("Resolve or explain and review outstanding reconciliation exceptions first.")


@frappe.whitelist(methods=["POST"])
@_observed
def lock_period(company, close_record, revision, request_id):
    c = _ctx(company, "fc", 2)
    if not c["final"] or not c["settings"].sync_respects_locks:
        frappe.throw("Final authorisation and verified sync compatibility are required.", frappe.PermissionError)
    d = _load(close_record, c)
    p = _json(d.payload_json, {})
    if p.get("lock_request_id") == request_id and p.get("accounting_period"):
        return _public(d)
    if int(revision) != d.revision:
        frappe.throw("This close changed. Reload before locking.", frappe.TimestampMismatchError)
    if d.kind != "Close" or d.status != "Closed":
        frappe.throw("The close must be reviewed and finally signed off first.")
    if p.get("accounting_period"):
        return _public(d)
    _assert_close_ready(c, p)
    doc = frappe.get_doc({"doctype": "Accounting Period", "period_name": d.title + " · " + company,
                         "company": company, "start_date": p["period"]["start"], "end_date": p["period"]["end"],
                         "closed_documents": [{"document_type": t, "closed": 1} for t in ["Journal Entry", "Payment Entry", "Purchase Invoice", "Sales Invoice"]]})
    doc.insert()  # Native permission and period validation; never creates a Closing Voucher.
    p["accounting_period"] = doc.name
    p["lock_request_id"] = request_id
    return _store(c, "Close", d.title, p, d, "Closed", "Period locked: " + request_id)


def _publish_budget(c, p, plan_name):
    """Publish account/cost-centre totals through the verified v16 native schema.

    Native validation regenerates distributions on INSERT. Save the explicit
    monthly allocations again before submission, then verify the persisted rows.
    Revisions cancel only budgets linked to their approved predecessor; no other
    spending controls or unrelated Budget records are changed.
    """
    required = {"account", "budget_distribution", "from_fiscal_year", "to_fiscal_year", "distribution_frequency", "revision_of"}
    if any(not frappe.get_meta("Budget").has_field(f) for f in required):
        frappe.throw("Budget publication needs the verified v16 account-level distribution schema. No budget was changed.")
    grouped = {}
    for line in p["lines"]:
        if line["category"] != "Expense":
            continue
        if not line.get("cost_center") or any(core.money(v) < 0 for v in line["values"]):
            frappe.throw("Expense budgets require a cost centre and non-negative allocations.")
        values = grouped.setdefault((line["account"], line["cost_center"]), [Decimal(0)] * 12)
        for i, value in enumerate(line["values"]):
            values[i] += core.money(value)
    prior = {}
    if p.get("revision_of"):
        original = _load(p["revision_of"], c)
        for name in _json(original.payload_json, {}).get("native_budgets", []):
            doc = frappe.get_doc("Budget", name)
            if doc.company != c["company"]:
                frappe.throw("The predecessor budget belongs to another company.")
            prior[(doc.account, doc.cost_center)] = doc
    if set(prior) - set(grouped):
        frappe.throw("A revision must retain predecessor expense accounts and cost centres, with revised amounts.")
    flags = ["applicable_on_material_request", "applicable_on_purchase_order", "applicable_on_booking_actual_expenses", "applicable_on_cumulative_expense"]
    actions = [f.fieldname for f in frappe.get_meta("Budget").fields if f.fieldname.startswith("action_if_")]
    result = []
    for (account, cc), values in grouped.items():
        amount = sum(values, Decimal(0))
        old = prior.get((account, cc))
        if amount <= 0:
            frappe.throw("Native expense budgets require a positive annual allocation.")
        if old:
            old.check_permission("cancel")
            old.cancel()
        fields = {"doctype": "Budget", "company": c["company"], "budget_against": "Cost Center", "cost_center": cc,
                  "account": account, "from_fiscal_year": p["fiscal_year"], "to_fiscal_year": p["fiscal_year"],
                  "budget_start_date": p["periods"][0]["start"], "budget_end_date": p["periods"][-1]["end"],
                  "distribution_frequency": "Monthly", "budget_amount": float(amount), "distribute_equally": 0,
                  "revision_of": old.name if old else None}
        fields.update({f: old.get(f) if old else int(f == "applicable_on_booking_actual_expenses") for f in flags})
        fields.update({f: old.get(f) if old else "Ignore" for f in actions})
        doc = frappe.get_doc(fields)
        doc.insert()
        doc.set("budget_distribution", [{"start_date": period["start"], "end_date": period["end"],
                   "amount": float(value), "percent": float(value / amount * 100)} for period, value in zip(p["periods"], values)])
        doc.save()
        if [core.money(row.amount) for row in doc.budget_distribution] != values:
            frappe.throw("Native Budget changed the approved monthly allocations. Publication was rolled back.")
        doc.add_comment("Info", "Published from Finance plan " + plan_name)
        doc.submit()
        result.append(doc.name)
    return result


@frappe.whitelist(methods=["POST"])
@_observed
def capture_snapshot(company, period_start, period_end):
    c = _ctx(company, phase=4)
    if not (c["caps"]["fc"] or c["caps"]["bpa"]):
        frappe.throw("Reporting permission required.", frappe.PermissionError)
    data = actuals(company, period_start, period_end)
    data["preparer"] = c["user"]
    return _store(c, "Snapshot", f"Actuals {period_start} to {period_end}", data)


@frappe.whitelist(methods=["POST"])
@_observed
def create_pack(company, snapshot, budget=None, forecast=None, commentary=""):
    c = _ctx(company, phase=4)
    if not c["caps"]["bpa"]:
        frappe.throw("BP&A prepares management packs.", frappe.PermissionError)
    snap = _load(snapshot, c)
    if snap.kind != "Snapshot":
        frappe.throw("Choose an actuals snapshot.")
    linked = {}
    for label, name in [("budget", budget), ("forecast", forecast)]:
        if name:
            doc = _load(name, c)
            expected = "Annual budget" if label == "budget" else "Rolling forecast"
            if doc.kind != "Plan" or doc.status != "Published" or _json(doc.payload_json, {}).get("plan_type") != expected:
                frappe.throw("Packs use published planning versions.")
            linked[label] = {"name": name, "revision": doc.revision, "data": _json(doc.payload_json, {})}
    return _store(c, "Pack", "Management pack · " + str(nowdate()), {"snapshot": _public(snap), "plans": linked,
        "commentary": commentary, "preparer": c["user"], "basis": _json(snap.payload_json, {}).get("basis"), "review_status": "Provisional management"})


@frappe.whitelist(methods=["POST"])
@_observed
def configure_release(company, phases, journal_review_required=0, sync_respects_locks=0, attestation="", dimension_rules=None, reporting_gaps=None):
    c = _ctx(company, phase=None)
    if not c["final"] or "System Manager" not in _roles():
        frappe.throw("Only the final approver with release administration access may release phases.", frappe.PermissionError)
    if not attestation.strip():
        frappe.throw("Record the acceptance and compatibility evidence.")
    s = c["settings"]
    for key, value in _json(phases, {}).items():
        if str(key) not in {"1", "2", "3", "4"}:
            frappe.throw("Unknown release phase.")
        s.set("phase_" + str(key), int(bool(value)))
    s.journal_review_required = int(journal_review_required)
    s.sync_respects_locks = int(sync_respects_locks)
    s.release_attestation = attestation
    if reporting_gaps is not None:
        gaps = _json(reporting_gaps, [])
        if not isinstance(gaps, list) or any(not isinstance(g, str) or not g.strip() for g in gaps):
            frappe.throw("Reporting gaps must be a list of written explanations.")
        s.reporting_gaps_json = frappe.as_json(gaps)
    if dimension_rules is not None:
        from vcl_finance.workspace.dimensions import validate_rules
        rules = validate_rules(_json(dimension_rules, []))
        for rule in rules:
            _ctx(rule["company"], "fc")
        s.dimension_rules_json = frappe.as_json(rules)
    from vcl_finance.workspace.guards import allow_release_write
    with allow_release_write():
        s.save()
    return capabilities(company)


@frappe.whitelist()
@_observed
def reference_data(company=None):
    c = _ctx(company)
    accounts = list(_rows("Account", {"company": c["company"], "is_group": 0, "disabled": 0}, ["name", "root_type"])) if c["caps"]["fc"] or c["caps"]["bpa"] else []
    users = []
    for u in frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, fields=["name", "full_name"]):
        caps = _caps(u.name)
        permissions = frappe.permissions.get_user_permissions(u.name).get("Company", [])
        if any(caps.values()) and (not permissions or c["company"] in {p.get("doc") for p in permissions}):
            users.append({"name": u.name, "full_name": u.full_name, "fc": caps["fc"]})
    fiscal_years = []
    for fy in _rows("Fiscal Year", {"disabled": 0}, ["name", "year_start_date", "year_end_date"]):
        companies = frappe.get_all("Fiscal Year Company", filters={"parent": fy.name}, pluck="company")
        if not companies or c["company"] in companies:
            fiscal_years.append(fy)
    from erpnext.accounts.utils import get_fiscal_year
    default_fiscal_year = get_fiscal_year(nowdate(), company=c["company"])[0]
    return {"accounts": accounts, "cost_centres": list(_rows("Cost Center", {"company": c["company"], "is_group": 0}, ["name"])),
            "fiscal_years": fiscal_years, "default_fiscal_year": default_fiscal_year,
            "users": users, "reviewers": [u for u in users if u["fc"] and u["name"] != c["user"]]}
