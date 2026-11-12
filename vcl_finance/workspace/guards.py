"""Record and journal guards apply to Desk, imports and RPC alike."""
import json
from contextlib import contextmanager
from contextvars import ContextVar

import frappe

from vcl_finance.workspace import core

_record_write = ContextVar("finance_record_write", default=False)
_release_write = ContextVar("finance_release_write", default=False)
_management_gl = ContextVar("finance_management_gl", default=False)


@contextmanager
def allow_management_gl():
    token = _management_gl.set(True)
    try:
        yield
    finally:
        _management_gl.reset(token)


def _planner_only_gl(user):
    roles = set(frappe.get_roles(user))
    if "VCL Financial Planner" not in roles:
        return False
    # Adding the planner role never removes rights granted by an existing role.
    return not any(p.role in roles and p.role != "VCL Financial Planner" and p.get("read") and not p.get("permlevel")
                   for p in frappe.get_meta("GL Entry").permissions)


def gl_query(user=None):
    return "1=0" if not _management_gl.get() and _planner_only_gl(user or frappe.session.user) else ""


def gl_has_permission(doc, user=None, ptype=None, permission_type=None):
    return not (_planner_only_gl(user or frappe.session.user) and not _management_gl.get())


@contextmanager
def allow_record_write():
    token = _record_write.set(True)
    try:
        yield
    finally:
        _record_write.reset(token)


@contextmanager
def allow_release_write():
    token = _release_write.set(True)
    try:
        yield
    finally:
        _release_write.reset(token)


def release_write_allowed():
    return _release_write.get()


def record_validate(doc, method=None):
    if not _record_write.get():
        frappe.throw("Finance records are maintained through the Finance workspace actions.", frappe.PermissionError)
    if doc.kind not in core.KINDS:
        frappe.throw("Unknown Finance record kind.")
    json.loads(doc.payload_json or "{}")


def record_delete(doc, method=None):
    frappe.throw("Finance evidence records cannot be deleted. Retain their audit trail.", frappe.PermissionError)


def journal_validate(doc, method=None):
    if doc.is_new() or not frappe.db.exists("DocType", "VCL Finance Record"):
        return
    name = frappe.db.get_value("VCL Finance Record", {"kind": "Review", "company": doc.company, "source_document": doc.name}, "name")
    if not name:
        return
    frappe.db.sql("SELECT name FROM `tabVCL Finance Record` WHERE name=%s FOR UPDATE", (name,))
    review = frappe.get_doc("VCL Finance Record", name)
    data = json.loads(review.payload_json)
    if review.status in {"Approved", "In review"} and data.get("document_hash") != core.fingerprint(doc.as_dict()):
        review.status = "Changes requested"
        data.setdefault("history", []).append({"event": "Journal changed; approval invalidated", "by": frappe.session.user, "at": frappe.utils.now()})
        review.payload_json = frappe.as_json(data)
        review.revision += 1
        with allow_record_write():
            review.save(ignore_permissions=True)


def journal_before_submit(doc, method=None):
    if not frappe.db.exists("DocType", "VCL Finance Workspace Settings"):
        return
    settings = frappe.get_single("VCL Finance Workspace Settings")
    if not settings.phase_2 or not settings.journal_review_required:
        return
    name = frappe.db.get_value("VCL Finance Record", {"kind": "Review", "company": doc.company, "source_document": doc.name}, "name")
    if not name:
        frappe.throw("Request Financial Control review before submitting this journal.")
    frappe.db.sql("SELECT name FROM `tabVCL Finance Record` WHERE name=%s FOR UPDATE", (name,))
    review = frappe.get_doc("VCL Finance Record", name)
    data = json.loads(review.payload_json)
    if review.status != "Approved" or data.get("document_hash") != core.fingerprint(doc.as_dict()):
        frappe.throw("An unchanged, approved Finance review is required before submission.")
    if data.get("reviewed_by") in set(data.get("preparers", [data.get("preparer")])):
        frappe.throw("The preparer cannot approve their own journal.")


def journal_on_submit(doc, method=None):
    if not frappe.db.exists("DocType", "VCL Finance Record"):
        return
    name = frappe.db.get_value("VCL Finance Record", {"kind": "Review", "company": doc.company, "source_document": doc.name, "status": "Approved"}, "name")
    if name:
        review = frappe.get_doc("VCL Finance Record", name)
        data = json.loads(review.payload_json)
        data.setdefault("history", []).append({"event": "Submitted to ERPNext", "by": frappe.session.user, "at": frappe.utils.now()})
        data["posting"] = {"erpnext": "Submitted", "quickbooks": "Not verified"}
        review.status, review.payload_json = "Posted", frappe.as_json(data)
        review.revision += 1
        with allow_record_write():
            review.save(ignore_permissions=True)


def journal_on_cancel(doc, method=None):
    if not frappe.db.exists("DocType", "VCL Finance Record"):
        return
    name = frappe.db.get_value("VCL Finance Record", {"kind": "Review", "company": doc.company, "source_document": doc.name}, "name")
    if not name:
        return
    frappe.db.sql("SELECT name FROM `tabVCL Finance Record` WHERE name=%s FOR UPDATE", (name,))
    review = frappe.get_doc("VCL Finance Record", name)
    data = json.loads(review.payload_json)
    data.setdefault("history", []).append({"event": "Cancelled in ERPNext; verify QuickBooks separately", "by": frappe.session.user, "at": frappe.utils.now()})
    data["posting"] = {"erpnext": "Cancelled", "quickbooks": "Reconciliation required"}
    review.status, review.payload_json = "Cancelled", frappe.as_json(data)
    review.revision += 1
    with allow_record_write():
        review.save(ignore_permissions=True)


def record_has_permission(doc, user=None, permission_type=None, ptype=None):
    from vcl_finance.workspace.api import _caps
    user = user or frappe.session.user
    permission_type = ptype or permission_type
    c = _caps(user)
    allowed = (core.READ_FC if c["fc"] else set()) | (core.READ_BPA if c["bpa"] else set()) | (core.READ_OPS if c["ops"] else set())
    if permission_type not in {None, "read", "select", "print", "export"} or doc.kind not in allowed:
        return False
    permitted = frappe.permissions.get_user_permissions(user).get("Company", [])
    if permitted and doc.company not in {p.get("doc") for p in permitted}:
        return False
    return bool(c["fc"] or c["bpa"] or doc.owner == user)


def record_query(user=None):
    from vcl_finance.workspace.api import _caps
    user = user or frappe.session.user
    c = _caps(user)
    kinds = (core.READ_FC if c["fc"] else set()) | (core.READ_BPA if c["bpa"] else set()) | (core.READ_OPS if c["ops"] else set())
    if not kinds:
        return "1=0"
    sql = "`tabVCL Finance Record`.kind IN (" + ",".join(frappe.db.escape(k) for k in kinds) + ")"
    permitted = frappe.permissions.get_user_permissions(user).get("Company", [])
    if permitted:
        sql += " AND `tabVCL Finance Record`.company IN (" + ",".join(frappe.db.escape(p["doc"]) for p in permitted) + ")"
    if not c["fc"] and not c["bpa"]:
        sql += " AND `tabVCL Finance Record`.owner=" + frappe.db.escape(user)
    return sql
