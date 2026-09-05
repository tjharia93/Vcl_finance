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
``approved_*``, ``withdrawn_*`` and ``receipt_asked_*`` exist only on this side, so
they are safe to write here.

``status`` is shared. The sheet's per-row lock IS the per-line approval, so the
mirror seeds status from it and approving here writes the lock BACK — a single
narrow exception to the one-way rule, and a deliberate one: two records of the same
signature that cannot disagree are worth more than a purity we were only keeping
for its own sake. The exception is exactly one boolean and its stamps; nothing else
ever travels from entry to sheet.

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
from frappe.utils import flt, now_datetime

from vcl_finance.petty_cash import resolve as R
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


def _write_lock_back(doc, locked):
    """Tick (or untick) the sheet row this entry mirrors, so both views agree.

    The row lock IS the per-line approval — the mirror reads it and stamps the
    entry Approved. Approving here without setting it would leave two records of
    the same fact free to disagree: the sheet showing a line unsigned that Finance
    has signed, and a re-mirror later reading the sheet's silence as the truth.

    Written with ``db.set_value`` on the child row rather than by saving the sheet.
    Saving would re-run every sheet validation and fire ``on_update``, which runs
    the mirror again mid-approval; and a sheet that fails an unrelated validation
    would then block an approval that has nothing to do with it. The permission
    question was already answered by ``_assert_finance()`` above — this is the same
    person the sheet's own guard would have allowed to tick the box by hand.

    Native entries have no row to write to and are skipped.
    """
    if doc.sync_state != "Mirrored" or not doc.origin_doctype or not doc.origin_row:
        return
    try:
        frappe.db.set_value(doc.origin_doctype, doc.origin_row, {
            "locked": 1 if locked else 0,
            "locked_by": frappe.session.user if locked else None,
            "locked_on": now_datetime() if locked else None,
        }, update_modified=False)
    except Exception:
        # The approval itself stands. Say so loudly rather than failing the action:
        # the sweep will show the two out of step, which is a smaller problem than
        # refusing to record a decision Finance has made.
        frappe.log_error(
            title="Petty cash: could not write approval back to the sheet row",
            message=f"entry={doc.name} row={doc.origin_doctype}/{doc.origin_row}\n\n{frappe.get_traceback()}",
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
    _write_lock_back(doc, True)
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
    # Withdrawing has to untick too, or the sheet keeps asserting a signature that
    # has been taken back — and the next mirror run would read it and re-approve.
    _write_lock_back(doc, False)
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

@frappe.whitelist(methods=["POST"])
def set_line_account(entry, account, reason=None, apply_to_route=0, company=None,
                     qbo_account=None, memo=None):
    """Choose where one line posts — and optionally make that the rule.

    The map proposes and the approver disposes. Most lines are approved with the
    proposed account untouched; this is for the ones that are wrong, and for the
    ones the map has no answer for yet.

    ``apply_to_route`` is the part that makes the map improve rather than be
    bypassed: it writes the choice back to the ``Posting Map`` row for this line's
    (source_type, source_key) pair, so the next line of the same kind arrives
    already correct. The row is created UNAPPROVED — teaching the map is not the
    same act as agreeing the map, and the second one stays deliberate.
    """
    _assert_finance()
    if not account:
        frappe.throw(_("Pick an account."))
    if not frappe.db.exists("Account", account):
        frappe.throw(_("No such account: {0}").format(account))

    doc = _get(entry)

    # Company comes FIRST, because everything after it is keyed on company: the
    # Posting Map row, the account's own company, and which books this line even
    # reaches — only Vimit is in QuickBooks. One tin serves four companies and
    # neither the sheet nor Gen 1 ever had a per-row company, so this is the only
    # place the true one is ever stated.
    if company and company != doc.company:
        if not frappe.db.exists("Company", company):
            frappe.throw(_("No such company: {0}").format(company))
        doc.company = company

    if frappe.db.get_value("Account", account, "company") != doc.company:
        frappe.throw(
            _("{0} belongs to another company. Pick an account in {1}'s books, or "
              "change the company on this line first.").format(account, doc.company),
            title=_("Wrong company"))

    from vcl_finance.petty_cash.resolve import resolve
    proposed = resolve(doc.as_dict()).get("erp_account")

    if proposed and account != proposed and not (reason or "").strip():
        frappe.throw(
            _("This differs from the mapped account ({0}). Say why — an override "
              "nobody can review quietly becomes the rule.").format(proposed),
            title=_("Reason required"),
        )

    doc.mapped_account = proposed
    doc.posting_account = account
    doc.override_reason = (reason or "").strip() or None

    # Both legs in one act. Picking the ERPNext account and then discovering at
    # push time that the QuickBooks side has nowhere to go is two trips for one
    # decision, and the second trip happens days later when the context is gone.
    if qbo_account:
        if not frappe.db.exists("QBO Account", qbo_account):
            frappe.throw(_("No such QuickBooks account: {0}").format(qbo_account))
        doc.qbo_account = qbo_account

    # The memo rides to BOTH books, deliberately. The same payment described two
    # different ways in two ledgers is how a tie-out turns into an argument.
    # `is not None` rather than truthiness, so clearing a memo actually clears it.
    if memo is not None:
        doc.memo = (memo or "").strip()[:180] or None
    doc.save()

    promoted = None
    if int(apply_to_route or 0):
        promoted = _teach_map(doc, account, qbo_account)

    return {"entry": doc.name, "posting_account": account,
            "mapped_account": proposed, "taught": promoted}


def _teach_map(doc, account, qbo_account=None):
    """Write an approver's choice onto the route's map row, unapproved.

    Creates the row if the pair has none. Deliberately never flips ``approved``:
    a person choosing an account for one line is evidence, not a decision about
    every future line, and the map's own gate is what turns one into the other.
    """
    st, sk = doc.source_type, (doc.source_key or "")
    if not st:
        return None
    # The line's OWN company, not the default. A map is per company — teaching it
    # from a Bahati line used to write a Vimit row, which would then propose a
    # Vimit account for every future Bahati line of that kind.
    from vcl_finance.petty_cash.resolve import COMPANY
    company = doc.company or COMPANY
    name = frappe.db.get_value(
        "Posting Map", {"company": company, "source_type": st, "source_key": sk}, "name")
    if name:
        row = frappe.get_doc("Posting Map", name)
    else:
        row = frappe.new_doc("Posting Map")
        row.company, row.source_type, row.source_key = company, st, sk
        # A blank key is the family default — how Parking is mapped once rather
        # than once per number plate.
        row.is_default = 1 if not sk else 0
    row.erp_account = account
    if qbo_account:
        row.qbo_account = qbo_account
    row.approved = 0
    row.notes = ((row.notes or "") + f"\nSet from {doc.name} on "
                 f"{frappe.utils.nowdate()} by {frappe.session.user}").strip()
    row.flags.ignore_permissions = True
    row.save(ignore_permissions=True)
    return row.name


# ----------------------------------------------------------------------
# The queue, as lines. Read-only.
# ----------------------------------------------------------------------

@frappe.whitelist()
def pending_entries(limit=200, float_name=None, **kwargs):
    """Every line still waiting for a signature, oldest first.

    A whitelisted read rather than a ``get_list`` because the phone app never
    touches the DocType REST surface — the approval fields sit at permlevel 1 and
    a list call returns them stripped, so the client would have to infer status
    from an absence. This says what it means, and it says it once.

    Oldest first for the same reason the posting queue is: the weeks nobody has
    looked at are the ones that matter, and a newest-first queue hides them
    behind whatever was keyed this morning.
    """
    float_name = float_name or kwargs.get("float")
    _assert_finance()

    filters = {"cancelled": 0, "status": ("!=", "Approved")}
    if float_name:
        filters["float"] = float_name

    rows = frappe.get_all(
        "Petty Cash Entry", filters=filters,
        fields=["name", "txn_date", "week_ending", "float", "company", "source_type",
                "source_key", "category", "recipient", "notes", "memo", "amount",
                "cash_in", "status", "posting_account", "receipt", "pc_received",
                "etr_received", "receipt_asked_on", "receipt_ask_reason",
                "withdrawn_on", "withdrawal_reason", "sync_state"],
        order_by="week_ending asc, txn_date asc, creation asc",
        limit_page_length=int(limit or 200),
    )

    total = frappe.db.count("Petty Cash Entry",
                            {"cancelled": 0, "status": ("!=", "Approved")})

    out = []
    for e in rows:
        company = e.get("company") or R.COMPANY
        r = R.resolve(e)
        out.append({
            "name": e["name"],
            "txn_date": str(e["txn_date"]) if e["txn_date"] else None,
            "week_ending": str(e["week_ending"]) if e["week_ending"] else None,
            "float": e["float"], "company": company,
            "route": f"{e.get('source_type') or '?'} · {e.get('source_key') or '—'}",
            "source_type": e.get("source_type"),
            "source_key": e.get("source_key"),
            "category": e.get("category"),
            # Parking never has a payee — the money is attached to a VEHICLE and
            # the plate in source_key is the only thing identifying the line. All
            # 271 of them carry a null recipient, so without this every parking
            # row reads "No payee", which tells the approver nothing.
            "subject": (e.get("recipient")
                        or (e.get("source_key") if e.get("source_type") == "Parking" else None)
                        or e.get("memo") or e.get("notes") or "—"),
            "is_plate": not (e.get("recipient") or "").strip()
                        and e.get("source_type") == "Parking"
                        and bool((e.get("source_key") or "").strip()),
            "amount": e["amount"], "cash_in": e["cash_in"],
            "status": e.get("status"),
            "evidence": bool(e.get("receipt") or e.get("pc_received") or e.get("etr_received")),
            "receipt_asked": bool(e.get("receipt_asked_on")),
            "receipt_ask_reason": e.get("receipt_ask_reason"),
            "withdrawn": bool(e.get("withdrawn_on")),
            "withdrawal_reason": e.get("withdrawal_reason"),
            # What it would post to, shown BEFORE signing rather than after. The
            # same resolver the posting run uses, so the two cannot disagree.
            "would_post_to": e.get("posting_account") or r.get("erp_account"),
            "route_reason": None if r.get("outcome") == R.POSTS else r.get("reason"),
            # The sheet owns txn_date and cancelled while a line is mirrored, so
            # the phone must show re-dating and voiding as somewhere else to go
            # rather than as buttons that silently revert on the next sheet save.
            "mirrored": (e.get("sync_state") or "") == "Mirrored",
        })

    return {"lines": out, "shown": len(out), "total": total,
            "value": round(sum(flt(x["amount"]) for x in out), 2)}
