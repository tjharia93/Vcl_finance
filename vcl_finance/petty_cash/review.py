"""One-screen Accounts review of a petty cash week (decision, Tanuj, 16/11/2026).

Before this, Accounts went through the same lines five times on different screens:
the Wednesday tick on the grid, the VAT screen, the receipt number in Desk, the postings
screen, then posting line by line. This module serves one screen that does all of it:

    week(...)        every line of a week, with what Accounts needs to decide and where
                     each line has already gone in BOTH books (ERPNext journal and the
                     QuickBooks staging row / pushed journal).
    save_line(...)   one action per line: sign it if not yet signed, receipt number,
                     VAT, ERPNext and QuickBooks accounts, and (Finance) the tick.
    post_done(...)   put the lines that are DONE into the week's draft journal now,
                     without waiting for the rest. A later batch joins the same draft
                     while it is still a draft (post.py _open_journal finds it by tag).

Nothing here posts anything the existing rules would refuse: every change still goes
through the entry's change rules (native.enforce_change_rules), VAT through vat._apply,
accounts through entry_api.set_line_account, and posting through weekpost._post_companies.
Journals are DRAFTS. Nothing is submitted and nothing is pushed to QuickBooks.

"Done" means: signed, VAT reviewed (or not applicable), an account to post to, and the
Finance tick. Only Finance (Accounts Manager) ticks; an Accounts User's Done saves the
coding and leaves the line waiting for the Finance tick.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

from vcl_finance.petty_cash import post as P
from vcl_finance.petty_cash import qbo as Q
from vcl_finance.petty_cash import resolve as R
from vcl_finance.petty_cash import weekpost as W
from vcl_finance.petty_cash import weekpost_core as C
from vcl_finance.petty_cash.api import PETTY_APPROVERS, PETTY_PRIV

ENTRY = "Petty Cash Entry"
SHEET = "Petty Cash Sheet"
EXTRA = ["reconciled", "reconciled_by", "receipt", "receipt_etr", "voucher_no", "etr_cu_invoice_no",
         "source_type", "source_key", "category", "recipient", "txn_date", "entry_kind", "week_sheet"]


def _who():
    roles = set(frappe.get_roles())
    return bool(roles & PETTY_PRIV), bool(roles & PETTY_APPROVERS)


def _require_reviewer():
    finance, signer = _who()
    if not signer:
        frappe.throw(_("The petty cash review is for Accounts."), frappe.PermissionError)
    return finance


def _qbo_label(qbo_id):
    if not qbo_id:
        return None
    return frappe.db.get_value("QBO Account", qbo_id, "fully_qualified_name") or qbo_id


def _books(e):
    """Where this line is in each book."""
    out = {"erp": None, "qbo": None}
    je = e.get("journal_entry")
    if je:
        ds = frappe.db.get_value("Journal Entry", je, "docstatus")
        out["erp"] = {"journal": je, "state": {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(ds, "Missing"),
                      "url": f"/app/journal-entry/{je}"}
    q = e.get("qbo_queue")
    if q:
        row = frappe.db.get_value(Q.QUEUE, q, ["qbo_journal_id", "pushed_at", "approved"], as_dict=True) or {}
        out["qbo"] = {"staging": q, "journal": row.get("qbo_journal_id"), "pushed_at": row.get("pushed_at"),
                      "state": "In QuickBooks" if row.get("qbo_journal_id") else
                               ("Approved to push" if row.get("approved") else "Staged")}
    return out


def _deadline(week_ending):
    """The Wednesday after the week (Saturday) ends: the review is due BY then, not ON it."""
    return add_days(getdate(week_ending), 4)


@frappe.whitelist()
def weeks(float_name="Cash"):
    """Recent weeks of a float, newest first, with how much is left to review."""
    _require_reviewer()
    rows = frappe.get_all(SHEET, filters={"float": float_name, "docstatus": ("<", 2)},
                          fields=["name", "week_ending", "status"], order_by="week_ending desc",
                          limit_page_length=10)
    return [{"sheet": r.name, "week_ending": str(r.week_ending), "status": r.status,
             "due": str(_deadline(r.week_ending))} for r in rows]


@frappe.whitelist()
def week(week_ending, float_name="Cash"):
    """Every live line of one week, ready for the review screen."""
    finance, signer = _who()
    _require_reviewer()
    sheet = W._sheet(week_ending, float_name)
    rows, b = W._survey(week_ending, float_name)
    names = [e["name"] for e in rows]
    extra = {r.name: r for r in frappe.get_all(ENTRY, filters={"name": ("in", names or [""])},
                                                fields=["name"] + EXTRA, limit_page_length=0)}
    state_of = {e["name"]: s for s, lst in b.items() for e in lst}
    lines = []
    for e in rows:
        if e.get("cancelled") or e.get("status") == "Void" or e.get("sync_state") == "Orphaned":
            continue
        x = extra.get(e["name"]) or {}
        e.update({k: x.get(k) for k in EXTRA})
        state = state_of.get(e["name"])
        company = e.get("company") or R.COMPANY
        res = R.resolve(e)
        erp = e.get("posting_account") or e.get("_account") or res.get("erp_account")
        in_qbo = R.posts_to_qbo(company)
        qbo_id, qbo_why = (Q._qbo_account(erp, e.get("source_type"), e.get("source_key"), company,
                                          e.get("qbo_account")) if in_qbo else (None, None))
        vat_fixed = e.get("source_type") in C.AUTO_NOT_APPLICABLE or cint(e.get("cash_in"))
        signed = e.get("status") == "Approved"
        ticked = cint(e.get("reconciled")) == 1
        problems = []
        if not signed:
            problems.append("Not signed yet")
        if e.get("_vat_problem"):
            problems.append(e["_vat_problem"])
        if state == P.BLOCKED and e.get("_reason") and e["_reason"] != "Not approved yet":
            problems.append(e["_reason"])
        if not (e.get("receipt") or e.get("receipt_etr")) and not cint(e.get("cash_in")):
            problems.append("No photo")
        lines.append({
            "name": e["name"], "date": str(e.get("txn_date") or ""), "kind": W._kind(e),
            "entry_kind": e.get("entry_kind"), "recipient": e.get("recipient"), "notes": e.get("notes"),
            "amount": flt(e.get("amount"), 2), "cash_in": cint(e.get("cash_in")), "company": company,
            "photo": e.get("receipt"), "photo_etr": e.get("receipt_etr"),
            "pc": cint(e.get("pc_received")), "etr": cint(e.get("etr_received")),
            "slip_no": e.get("voucher_no"), "receipt_no": e.get("supplier_ref") or e.get("etr_cu_invoice_no") or "",
            "vat_treatment": e.get("_treatment") or "", "vat_amount": flt(e.get("_vat"), 2), "vat_fixed": bool(vat_fixed),
            "erp_account": erp, "erp_from_map": bool(res.get("erp_account")) and erp == res.get("erp_account"),
            "erp_proposed": res.get("erp_account"),
            "in_qbo": in_qbo, "qbo_account": qbo_id, "qbo_label": _qbo_label(qbo_id), "qbo_why": qbo_why,
            "signed": signed, "ticked": ticked, "ticked_by": e.get("reconciled_by"),
            "posted": bool(e.get("journal_entry")), "state": state,
            # A cash-in that is not a wallet top-up never posts: signed and ticked is all it needs.
            "done": (signed and ticked) if (cint(e.get("cash_in")) and not C.is_topup(e)) else
                    (signed and ticked and not e.get("_vat_problem") and bool(erp) and state in (P.READY, P.POSTED)),
            "never_posts": bool(cint(e.get("cash_in")) and not C.is_topup(e)),
            "problems": problems, "books": _books(e),
        })
    done = sum(1 for x in lines if x["done"])
    return {"sheet": sheet["name"], "week_ending": str(week_ending), "float": float_name,
            "status": sheet["status"], "due": str(_deadline(week_ending)),
            "lines": lines, "count": len(lines), "done": done,
            "to_post": sum(1 for x in lines if x["done"] and not x["posted"] and not x["cash_in"]),
            "finance": finance, "signer": signer,
            "vat_treatments": list(C.TREATMENTS)}


@frappe.whitelist(methods=["POST"])
def save_line(entry, receipt_no=None, vat_treatment=None, vat_amount=None, erp_account=None,
              qbo_account=None, reason=None, done=1):
    """Everything Accounts decides about one line, in one action.

    Order matters: coding first, the tick last, because any change to a ticked line clears
    the tick. Each step uses the rule-checked path that already exists.
    """
    finance = _require_reviewer()
    from vcl_finance.petty_cash import entry_api, vat as V
    doc = frappe.get_doc(ENTRY, entry)
    if doc.journal_entry:
        frappe.throw(_("{0} is already in journal {1}. Undo that posting before changing it.")
                     .format(doc.name, doc.journal_entry), title=_("Already posted"))
    done = cint(done)

    if done and doc.status != "Approved":
        entry_api.approve_entry(entry)                              # sign it (Accounts)
        doc.reload()

    if receipt_no is not None and (receipt_no or "").strip() != (doc.supplier_ref or ""):
        if doc.source_type in C.AUTO_NOT_APPLICABLE or not vat_treatment:
            doc.supplier_ref = (receipt_no or "").strip()[:140]
            doc.save()
            doc.reload()

    if vat_treatment and doc.source_type not in C.AUTO_NOT_APPLICABLE and not cint(doc.cash_in):
        va = None if vat_amount in (None, "") else flt(vat_amount)
        if vat_treatment != (doc.vat_treatment or "") or va is not None or (receipt_no or "") != (doc.supplier_ref or ""):
            V._apply(doc, vat_treatment, va, receipt_no if receipt_no is not None else None)
            doc.reload()

    if erp_account and erp_account != (doc.posting_account or ""):
        entry_api.set_line_account(entry, erp_account, reason=reason,
                                   qbo_account=qbo_account or None)
        doc.reload()
    elif qbo_account and qbo_account != (doc.qbo_account or ""):
        if not frappe.db.exists("QBO Account", qbo_account):
            frappe.throw(_("No such QuickBooks account: {0}").format(qbo_account))
        doc.qbo_account = qbo_account
        doc.save()
        doc.reload()

    ticked = cint(doc.reconciled)
    if finance and done and not ticked:
        doc.reconciled = 1
        doc.save()
        ticked = 1
    elif finance and not done and ticked:
        doc.reconciled = 0
        doc.save()
        ticked = 0
    frappe.db.commit()
    msg = None if (ticked or not done) else _("Saved. The Finance tick is still to do.")
    return {"entry": entry, "ticked": bool(ticked), "message": msg}


@frappe.whitelist(methods=["POST"])
@W._retrying
def post_done(week_ending, float_name="Cash"):
    """Put the DONE lines of a week into its draft journal(s) now. Never submits.

    Lines already in a draft are carried into the rebuilt draft (one draft per company per
    week, found again by its tag). Lines not done are left exactly as they are.
    """
    P._guard()
    sheet = W._sheet(week_ending, float_name)
    W._lock_week(sheet["name"])
    rows, b = W._survey(week_ending, float_name)
    ready = b[P.READY]
    ticked = {r for r in frappe.get_all(ENTRY, filters={"name": ("in", [e["name"] for e in ready] or [""]),
                                                              "reconciled": 1}, pluck="name", limit_page_length=0)} \
        if ready else set()
    done = [e for e in ready if e["name"] in ticked and not e["_vat_problem"]
            and not (cint(e.get("cash_in")) and not C.is_topup(e))]
    if not done:
        frappe.throw(_("No done lines are waiting to post in this week."))
    for co in {e.get("company") or R.COMPANY for e in done}:
        if not P.cash_account(co, float_name):
            frappe.throw(_("{0} has no cash account for the {1} float.").format(co, float_name))

    by_company = {}
    for e in done:
        by_company.setdefault(e.get("company") or R.COMPANY, []).append(e)
    from frappe.utils import now_datetime
    made, stamp = [], now_datetime()
    frappe.db.savepoint("pc_post_done")
    try:
        W._post_companies(by_company, b, float_name, week_ending, "grouped", made, stamp)
        W._record_postings(sheet["name"], made, stamp)
    except Exception:
        frappe.db.rollback(save_point="pc_post_done")
        raise
    frappe.db.commit()
    return {"posted": len(done), "journals": made}
