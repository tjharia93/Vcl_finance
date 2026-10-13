"""Whitelisted actions that record VAT on a Petty Cash Entry.

Same rule as ``entry_api``: the role check lives IN each method, because permlevel does
not protect the RPC path. VAT is a Finance judgement made at review, so it uses the
same role set as approving an entry.

VAT is set on the ENTRY only. It is not copied from the sheet and the mirror never
writes it (the mirror writes a fixed set of fields, none of them these), so a sheet
save cannot wipe it. Setting VAT on an already-approved entry does not undo the
approval: the approval is about the payment, VAT is about how it is booked.

Once an entry is in a journal it is frozen. A VAT change after posting would leave the
books saying one thing and the entry another, so it is refused and the message says
where the change belongs.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from vcl_finance.petty_cash import weekpost_core as C
from vcl_finance.petty_cash.api import PETTY_APPROVERS


def _assert_read():
    if not (set(frappe.get_roles()) & (PETTY_APPROVERS | {"Accounts User"})):
        frappe.throw(_("Petty cash VAT is for Finance."), frappe.PermissionError)


def _assert_finance():
    if not (set(frappe.get_roles()) & PETTY_APPROVERS):
        frappe.throw(_("Only Finance can set VAT on a petty cash entry."), frappe.PermissionError)


def _apply(doc, vat_treatment, vat_amount=None, supplier_ref=None):
    """Set VAT on one loaded entry. Raises with a message written for a person."""
    if doc.journal_entry:
        frappe.throw(_("{0} is already in journal {1}. Change VAT before posting, or undo the posting first.")
                     .format(doc.name, doc.journal_entry), title=_("Already posted"))
    if doc.cancelled or doc.status == "Void":
        frappe.throw(_("{0} is voided.").format(doc.name))
    if doc.source_type in C.AUTO_NOT_APPLICABLE:
        frappe.throw(_("{0} is a {1} entry. Those never carry VAT, so there is nothing to set.")
                     .format(doc.name, doc.source_type))
    if (vat_treatment or "") not in C.TREATMENTS:
        frappe.throw(_("Choose a VAT treatment for {0}.").format(doc.name))
    try:
        net, vat = C.split_vat(doc.amount, vat_treatment, vat_amount)
    except C.VatError as exc:
        frappe.throw(_("{0}: {1}").format(doc.name, str(exc)))
    if vat and not C.has_evidence(doc.as_dict()):
        frappe.throw(_("{0} has no receipt, so VAT cannot be claimed on it. Attach the receipt or mark it received first.")
                     .format(doc.name), title=_("No receipt"))
    doc.vat_treatment = vat_treatment
    doc.vat_amount = vat
    doc.net_amount = net
    doc.vat_basis = C.vat_basis(doc.amount, vat_treatment)
    if supplier_ref is not None:
        doc.supplier_ref = (supplier_ref or "").strip()[:140]
    doc.vat_set_by = frappe.session.user
    doc.vat_set_on = now_datetime()
    doc.save()
    return {"entry": doc.name, "vat_treatment": vat_treatment, "vat_amount": vat, "net_amount": net}


@frappe.whitelist(methods=["POST"])
def set_vat(entry, vat_treatment, vat_amount=None, supplier_ref=None):
    """Set the VAT treatment (and optionally the VAT shown on the receipt) for one entry."""
    _assert_finance()
    doc = frappe.get_doc("Petty Cash Entry", entry)
    if vat_amount in ("", None):
        vat_amount = None
    return _apply(doc, vat_treatment, vat_amount, supplier_ref)


@frappe.whitelist(methods=["POST"])
def set_vat_bulk(updates):
    """Apply VAT to many entries. ``updates`` is a list of
    ``{entry, vat_treatment, vat_amount?, supplier_ref?}``.

    Every entry is attempted on its own; one that is refused does not stop the rest, and
    the answer lists which saved and which did not, with the reason.
    """
    _assert_finance()
    if isinstance(updates, str):
        updates = json.loads(updates)
    saved, failed = [], []
    for u in updates or []:
        # A savepoint per entry: one refused entry undoes only itself, never the ones
        # already saved before it.
        frappe.db.savepoint("vat_entry")
        try:
            doc = frappe.get_doc("Petty Cash Entry", u["entry"])
            va = u.get("vat_amount")
            saved.append(_apply(doc, u.get("vat_treatment"), None if va in ("", None) else va, u.get("supplier_ref")))
        except Exception as exc:  # noqa: BLE001 - reported per entry, never swallowed
            failed.append({"entry": u.get("entry"), "error": str(getattr(exc, "message", None) or exc)})
            frappe.db.rollback(save_point="vat_entry")
    frappe.db.commit()
    return {"saved": saved, "failed": failed}


@frappe.whitelist()
def vat_week(week_ending, float_name=None, **kwargs):
    """Every live entry in a week with its VAT state, for the review screen."""
    _assert_read()
    float_name = float_name or kwargs.get("float")
    filters = {"week_ending": week_ending, "cancelled": 0, "sync_state": ["!=", "Orphaned"]}
    if float_name:
        filters["float"] = float_name
    rows = frappe.get_all(
        "Petty Cash Entry", filters=filters,
        fields=["name", "txn_date", "float", "company", "source_type", "source_key", "recipient", "notes", "memo",
                "amount", "cash_in", "status", "receipt", "receipt_etr", "pc_received", "etr_received",
                "vat_treatment", "vat_amount", "net_amount", "vat_basis", "supplier_ref", "journal_entry",
                "exclude_from_posting"],
        order_by="txn_date asc, creation asc", limit_page_length=0)
    out = []
    for e in rows:
        t, net, vat, problem = C.effective_vat(e)
        out.append(dict(e, effective_treatment=t, effective_net=net, effective_vat=vat, problem=problem,
                        evidence=C.has_evidence(e), auto=(e["source_type"] in C.AUTO_NOT_APPLICABLE),
                        receipt=bool(e.get("receipt")), receipt_etr=bool(e.get("receipt_etr"))))
    return {"week_ending": week_ending, "float": float_name, "treatments": C.TREATMENT_ORDER,
            "entries": out,
            "unreviewed": sum(1 for x in out if x["effective_treatment"] is None and not cint(x["cash_in"])
                              and x["status"] != "Void" and x["amount"] and not cint(x["exclude_from_posting"]))}
