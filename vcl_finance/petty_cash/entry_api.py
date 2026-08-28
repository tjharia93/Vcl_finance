"""Whitelisted actions on a Petty Cash Entry — the approval surface.

Every method here re-checks the caller's role in its own body. That is not belt and
braces, it is the actual control: **permlevel does not protect the RPC path.** The
approval fields sit at permlevel 1 so the ordinary save path cannot touch them, but a
whitelisted method runs whatever code it contains, and this app already exposes 23 of
them. If the check is not in the body, it is not enforced.

The role set is imported from ``api.PETTY_PRIV`` rather than redeclared. One
definition of "who may approve", or the two drift and the weaker one wins.

WHAT THESE MAY AND MAY NOT TOUCH
--------------------------------
Approval is safe to write on the entry because the mirror never writes those fields —
``status``, ``approved_*``, ``withdrawn_*``, ``receipt_asked_*`` exist only on this
side and no source row has an opinion about them.

``txn_date`` and ``cancelled`` are different: the mirror OWNS them. It copies both
from the child row on every sheet save, so setting them here survives exactly until
the next time anyone touches that sheet, and then reverts silently. While an entry is
``Mirrored``, the sheet is the truth. ``set_entry_date`` and ``void_entry`` therefore
refuse on mirrored entries and say where the change belongs, rather than accepting a
write that will quietly undo itself. After the phase-4 flip, when entries become the
capture path and are ``Native``, both work normally.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from vcl_finance.petty_cash.api import PETTY_PRIV

ASK_REASONS = ("No slip attached", "Need the ETR", "Photo unreadable")


def _assert_finance():
    """The single signature. Checked here because permlevel cannot reach this path."""
    if not (set(frappe.get_roles()) & PETTY_PRIV):
        frappe.throw(
            _("Only Finance can approve or withdraw a petty cash entry."),
            frappe.PermissionError,
        )


def _get(entry):
    doc = frappe.get_doc("Petty Cash Entry", entry)
    if doc.cancelled and doc.status != "Void":
        # Defensive: a voided row should never be approvable.
        frappe.throw(_("This entry is voided."), title=_("Voided"))
    return doc


def _refuse_if_mirrored(doc, what, where):
    if doc.sync_state == "Mirrored":
        frappe.throw(
            _("{0} is copied from the sheet, so changing it here would be undone the "
              "next time {1} is saved. Change it on {1} instead — {2}.")
            .format(what, doc.origin_sheet or _("the source sheet"), where),
            title=_("The sheet owns this"),
        )


@frappe.whitelist(methods=["POST"])
def approve_entry(entry):
    """Finance approves one entry. Per entry, never per week (PC-002)."""
    _assert_finance()
    doc = _get(entry)
    if doc.status == "Approved":
        return {"entry": doc.name, "status": doc.status, "already": True}

    doc.status = "Approved"
    doc.approved_by = frappe.session.user
    doc.approved_on = now_datetime()
    doc.withdrawn_by = None
    doc.withdrawn_on = None
    doc.withdrawal_reason = None
    # A fresh approval is a decision made on what the row says NOW, so the
    # "changed since approval" flag starts clean again.
    doc.changed_after_approval = 0
    doc.save()
    return {"entry": doc.name, "status": doc.status,
            "approved_by": doc.approved_by, "approved_on": str(doc.approved_on)}


@frappe.whitelist(methods=["POST"])
def withdraw_entry(entry, reason=None):
    """Reverse an approval. Reversible by design (PC-002) — but never silently.

    The reason is required by the controller as well as here: a withdrawal with no
    reason is indistinguishable from a mistake, and the person who finds it three
    weeks later has no way to tell which it was.
    """
    _assert_finance()
    if not (reason or "").strip():
        frappe.throw(_("Say why you are withdrawing this approval."),
                     title=_("Reason required"))
    doc = _get(entry)
    doc.status = "Withdrawn"
    doc.withdrawal_reason = reason.strip()
    doc.withdrawn_by = frappe.session.user
    doc.withdrawn_on = now_datetime()
    doc.save()
    return {"entry": doc.name, "status": doc.status,
            "withdrawn_by": doc.withdrawn_by, "reason": doc.withdrawal_reason}


@frappe.whitelist(methods=["POST"])
def ask_for_receipt(entry, reason=None, ask_from=None):
    """Record that a receipt was chased. Records the ASK, not the delivery.

    The WhatsApp message is sent by the person, from their own phone, via a wa.me
    link the screen opens. We cannot observe whether it was sent or read, so this
    stamps the moment the ask was made and nothing more — claiming delivery would be
    inventing a fact.

    ``ask_from`` defaults to the entry's owner: for a mirrored entry that is whoever
    saved the sheet, i.e. the custodian who typed the row, which is who can actually
    find the slip. Not the payee.
    """
    _assert_finance()
    if reason and reason not in ASK_REASONS:
        frappe.throw(_("Unknown reason: {0}").format(reason))
    doc = _get(entry)
    doc.receipt_asked_from = ask_from or doc.owner
    doc.receipt_asked_on = now_datetime()
    doc.receipt_ask_reason = reason or "No slip attached"
    doc.receipt_answered_on = None
    doc.save()
    return {"entry": doc.name, "asked_from": doc.receipt_asked_from,
            "asked_on": str(doc.receipt_asked_on), "reason": doc.receipt_ask_reason}


@frappe.whitelist(methods=["POST"])
def set_entry_date(entry, txn_date):
    """Re-file an entry into a different week by correcting its date.

    ``week_ending`` is derived in ``before_save``, so setting the date IS the re-file;
    there is no second field to keep in step.

    Refuses while the entry is mirrored — see the module docstring. The sheet owns
    ``txn_date`` and would overwrite this on its next save.
    """
    _assert_finance()
    if not txn_date:
        frappe.throw(_("A date is required."))
    doc = _get(entry)
    _refuse_if_mirrored(doc, _("The transaction date"),
                        _("the row's date there is the one that counts"))
    before = doc.week_ending
    doc.txn_date = txn_date
    doc.save()
    return {"entry": doc.name, "txn_date": str(doc.txn_date),
            "week_ending": str(doc.week_ending), "moved_from": str(before)}


@frappe.whitelist(methods=["POST"])
def void_entry(entry, remark=None):
    """Void an entry — ``status = Void`` ALONGSIDE ``cancelled``, never instead of it.

    Totals filter on ``cancelled``; clearing it would put voided money back into a
    reconciled week. Refuses while mirrored: voiding belongs on the child row, where
    ``api.cancel_entry`` already does it properly and the sheet's totals recompute.
    """
    _assert_finance()
    doc = _get(entry)
    _refuse_if_mirrored(doc, _("Voiding"),
                        _("cancel the row on the sheet and the entry follows"))
    doc.cancelled = 1
    doc.cancel_remark = (remark or "").strip() or None
    doc.status = "Void"
    doc.save()
    return {"entry": doc.name, "status": doc.status, "cancelled": doc.cancelled}
