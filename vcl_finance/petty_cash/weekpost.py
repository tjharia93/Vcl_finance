"""Posting a whole petty cash week: the preview, the ERPNext journal and the QuickBooks expense.

This is the layer the two buttons on the petty cash posting screen call. It sits on top of
``post.py`` (classification, the draft-journal reuse, the stamping of entries) and
``qbo.py`` (the staging queue, the approval, the account crosswalk) and adds three things
they did not have: VAT, summarised lines, and a single set of gates.

THE GATES (``preview`` reports every one; posting refuses unless they pass)
---------------------------------------------------------------------------
1. The week's sheet is **Approved**.
2. Every live entry in the week is **Approved**. (``api.approve_week`` now enforces the
   same thing, so a week cannot be approved with entries still open. The gate is checked
   here again because weeks were approved before that rule existed.)
3. Every line has an **account** (the entry's own, or an approved Posting Map row).
4. Every line has a **VAT treatment** reviewed, and VAT is only claimed on a line with a
   receipt. Wages and Loan lines need none.
5. The float has a **cash account** for the company.
6. No cash-IN line is trying to post (money into the tin is posted at the bank).

Posting is refused if any line is held, unless ``allow_partial`` is set: half a week in
the books looks complete, which is worse than absent. Held lines can be posted later.

TWO LEGS, TWO SIGN-OFFS
-----------------------
ERPNext leg  ``post_erp``   builds a DRAFT journal and links every entry to it. Nothing is
                            submitted: Tanuj or an Accounts Manager opens it and submits.
QuickBooks   ``stage_qbo``  builds the tax-inclusive Expense from what ERPNext holds and
                            puts it in the existing queue, UNAPPROVED. ``qbo.approve_push``
                            is the sign-off; the runner on the CommandCentre box pushes it
                            by hand. FC never calls Intuit.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from vcl_finance.petty_cash import post as P
from vcl_finance.petty_cash import qbo as Q
from vcl_finance.petty_cash import resolve as R
from vcl_finance.petty_cash import weekpost_core as C
from vcl_finance.petty_cash.api import PETTY_APPROVERS

SHEET = "Petty Cash Sheet"
RUNNER = ("cd /opt/vcl/CommandCentre && source .venv/bin/activate && export PYTHONPATH=/opt/vcl/CommandCentre && "
          "python projects/petty_cash_qbo/push_petty_journal.py")


def _assert_read():
    if not (set(frappe.get_roles()) & (PETTY_APPROVERS | {"Accounts User"})):
        frappe.throw(_("Petty cash posting is for Finance."), frappe.PermissionError)


def _sheet(week_ending, float_name):
    if not float_name:
        frappe.throw(_("Say which float: Cash or Hauz-Pay."))
    name = frappe.db.get_value(SHEET, {"week_ending": week_ending, "float": float_name}, "name")
    if not name:
        frappe.throw(_("There is no {0} sheet for the week ending {1}.").format(float_name, week_ending))
    return frappe.db.get_value(SHEET, name, ["name", "status", "company", "journal_entry", "qbo_queue"], as_dict=True)


ENTRY_FIELDS = ["name", "txn_date", "float", "company", "source_type", "source_key", "recipient", "notes", "memo",
                "amount", "cash_in", "status", "cancelled", "posting_account", "qbo_account", "journal_entry",
                "posted_on", "receipt", "receipt_etr", "pc_received", "etr_received", "vat_treatment",
                "vat_amount", "net_amount", "vat_basis", "qbo_queue", "sync_state", "exclude_from_posting",
                "exclude_reason", "supplier_ref"]


def _entries(week_ending, float_name):
    # One shared lookup (weeks.py): the sheet's mirrored lines, its native lines, and
    # any unlinked entry dated into the week — each exactly once.
    from vcl_finance.petty_cash import weeks
    rows = weeks.entries_for_week(week_ending, float_name, fields=ENTRY_FIELDS, include_orphaned=True)[0]
    # An entry whose sheet row was deleted is Orphaned and stops counting, EXCEPT while it
    # is still linked to a journal: dropping it from view would make a re-post rebuild the
    # draft without it while the entry still points at that draft.
    return [e for e in rows if e.get("sync_state") != "Orphaned" or e.get("journal_entry")]


def _survey(week_ending, float_name):
    rows = _entries(week_ending, float_name)
    out = {P.POSTED: [], P.READY: [], P.BLOCKED: [], P.EXCLUDED: []}
    for e in rows:
        if (not e.get("journal_entry") and not e.get("cancelled") and e.get("status") != "Void"
                and not flt(e.get("amount"))):
            state, account, reason = P.EXCLUDED, None, "Zero value"   # before 'not approved': nothing to approve
        else:
            state, account, reason = P._classify(e)
        e["_account"], e["_reason"] = account, reason
        t, net, vat, problem = C.effective_vat(e)
        e["_treatment"], e["_net"], e["_vat"], e["_vat_problem"] = t, net, vat, problem
        out[state].append(e)
    return rows, out


def _kind(e):
    """The plain-words name of what an entry was, for the QuickBooks line: the category's
    label for a voucher ("Spares / Engineering", never "SE"), else the entry type."""
    st, sk = e.get("source_type") or "", (e.get("source_key") or "").strip()
    if st == "Voucher Category" and sk:
        return frappe.get_cached_value("Petty Cash Category", sk, "label") or sk
    if st == "Wages Entry" and sk:
        return f"Wages: {sk}"
    return st or "Petty cash"


def _line(e):
    return {"name": e["name"], "account": e["_account"], "gross": flt(e["amount"], 2), "net": e["_net"],
            "vat": e["_vat"], "subject": P._subject(e), "txn_date": e.get("txn_date"),
            "kind": _kind(e), "notes": e.get("notes"), "supplier_ref": e.get("supplier_ref"),
            "treatment": e["_treatment"], "source_type": e["source_type"], "source_key": e.get("source_key"),
            "company": e.get("company") or R.COMPANY, "chosen_qbo": e.get("qbo_account"),
            "cash_in": cint(e.get("cash_in"))}


def _carry(b):
    """Lines already sitting in a DRAFT journal. A re-post rebuilds that draft, so they come along."""
    drafts = {n for n in {e["journal_entry"] for e in b[P.POSTED]}
              if n and frappe.db.get_value("Journal Entry", n, "docstatus") == 0}
    return [e for e in b[P.POSTED] if e["journal_entry"] in drafts]


def _gates(sheet, rows, b, carry=()):
    live = [e for e in rows if not e.get("cancelled") and e.get("status") != "Void" and flt(e.get("amount"))]
    unapproved = [e for e in live if e.get("status") != "Approved"]
    other_blocked = [e for e in b[P.BLOCKED] if e["_reason"] != "Not approved yet"]
    vat_bad = [e for e in list(b[P.READY]) + list(carry) if e["_vat_problem"]]
    # A Hauz-Pay top-up with its bank chosen posts (Dr wallet / Cr bank); any other cash-in does not.
    cash_in = [e for e in list(b[P.READY]) + list(carry) if cint(e.get("cash_in")) and not C.is_topup(e)]
    no_cash = sorted({f"{e.get('company') or R.COMPANY} · {e['float']}" for e in b[P.READY]
                      if not P.cash_account(e.get("company"), e["float"])})
    no_vat_acct = sorted({e.get("company") or R.COMPANY for e in b[P.READY]
                          if e["_vat"] and not C.VAT_ACCOUNT.get(e.get("company") or R.COMPANY)})

    def g(key, label, ok, detail=""):
        return {"key": key, "label": label, "ok": bool(ok), "detail": detail}

    return [
        g("week_approved", "The week is approved", sheet["status"] == "Approved",
          "" if sheet["status"] == "Approved" else f"The sheet is {sheet['status']}."),
        g("entries_approved", "Every entry is approved", not unapproved,
          f"{len(unapproved)} entr{'y' if len(unapproved) == 1 else 'ies'} not approved ({flt(sum(flt(e['amount']) for e in unapproved)):,.2f})" if unapproved else ""),
        g("accounts", "Every line has an account", not other_blocked,
          f"{len(other_blocked)} line(s) held: " + "; ".join(sorted({e['_reason'] for e in other_blocked})[:6]) if other_blocked else ""),
        g("vat", "VAT reviewed on every line", not vat_bad,
          (f"{len(vat_bad)} line(s): " + "; ".join(sorted({e['_vat_problem'] for e in vat_bad}))
           + ("" if not any(e in carry for e in vat_bad) else ". Some are already in a draft journal: undo that posting, set the VAT, then post again.")) if vat_bad else ""),
        g("cash_account", "The float has a cash account", not no_cash and not no_vat_acct,
          ", ".join(no_cash) or ("No VAT account for " + ", ".join(no_vat_acct) if no_vat_acct else "")),
        g("no_cash_in", "No cash-in line is posting", not cash_in,
          f"{len(cash_in)} cash-in line(s) are ready or already in the draft journal; their route should be marked never-post, and a draft that holds one has to be undone." if cash_in else ""),
    ]


def _erp_preview(ready, posted_carry, mode, week_ending, float_name):
    """Journal rows per company for the lines that would go in (carried draft lines included)."""
    by_company = {}
    for e in posted_carry + ready:
        by_company.setdefault(e.get("company") or R.COMPANY, []).append(e)
    journals = []
    for company, lines in sorted(by_company.items()):
        cash = P.cash_account(company, float_name)
        vat_acct = C.VAT_ACCOUNT.get(company)
        try:
            rows, totals = C.build_erp_lines([_line(x) for x in lines], cash or "(no cash account)",
                                             vat_acct or "(no VAT account)", mode)
        except C.VatError as exc:
            journals.append({"company": company, "error": str(exc), "rows": [], "totals": {}})
            continue
        journals.append({"company": company, "cash_account": cash, "vat_account": vat_acct, "rows": rows,
                         "totals": totals, "lines": len(lines)})
    return journals


def _not_for_qbo(entries):
    """Posted lines that are deliberately NOT sent to QuickBooks, by reason.

    ``_qbo_lines`` leaves these out by design. They are stated here so the posting result
    accounts for every posted line: staged, held with a reason, or not for QuickBooks.
    """
    out = {}
    for e in entries:
        company = e.get("company") or R.COMPANY
        if not R.posts_to_qbo(company):
            out.setdefault(f"{company} has no books in QuickBooks", []).append(e)
        elif cint(e.get("cash_in")):
            out.setdefault("Cash in is not an expense", []).append(e)
    return [{"reason": k, "lines": len(v), "amount": round(sum(flt(x["amount"]) for x in v), 2),
             "entries": [x["name"] for x in v]} for k, v in sorted(out.items())]


def _qbo_lines(entries):
    """(lines, blocked). Only companies with books in QuickBooks; spend only."""
    lines, blocked = [], {}
    for e in entries:
        company = e.get("company") or R.COMPANY
        if not R.posts_to_qbo(company) or cint(e.get("cash_in")):
            continue
        if not e["_treatment"]:
            blocked.setdefault("VAT not reviewed", []).append(e)
            continue
        acct, why = Q._qbo_account(e["_account"], e["source_type"], e.get("source_key"), company, e.get("qbo_account"))
        if not acct:
            blocked.setdefault(why, []).append(e)
            continue
        ln = _line(e)
        ln["qbo_account"] = acct
        ln["label"] = (e.get("source_key") or e["source_type"] or "Petty cash")
        lines.append(ln)
    return lines, blocked


def _queue_status(week_ending, float_name):
    name = Q._row_name(week_ending, float_name)
    if not frappe.db.exists(Q.QUEUE, name):
        return None
    r = frappe.db.get_value(Q.QUEUE, name, ["name", "approved", "approved_by", "approved_at", "qbo_journal_id",
                                            "pushed_at", "error_message", "payload_hash", "block_reason",
                                            "doc_number", "entry_count", "total_out", "attempts"], as_dict=True)
    r["pushed"] = bool(r.get("qbo_journal_id"))
    return r


@frappe.whitelist()
def preview(week_ending, float_name=None, line_mode="grouped", **kwargs):
    """Everything the posting screen needs for one week. Read-only; safe on every render."""
    float_name = float_name or kwargs.get("float")
    sheet = _sheet(week_ending, float_name)
    rows, b = _survey(week_ending, float_name)
    _assert_read()
    carry = _carry(b)
    gates = _gates(sheet, rows, b, carry)
    ok = all(g["ok"] for g in gates)
    held = {}
    for e in b[P.BLOCKED]:
        h = held.setdefault(e["_reason"] or "Held", {"reason": e["_reason"] or "Held", "lines": 0, "value": 0.0})
        h["lines"] += 1
        h["value"] = round(h["value"] + flt(e["amount"]), 2)

    # Lines already sitting in a DRAFT journal are carried into a re-post, so the preview
    # includes them; a submitted journal is never touched.
    erp = _erp_preview(b[P.READY], carry, line_mode, week_ending, float_name)

    # QuickBooks is built from what ERPNext holds: posted lines, plus ready ones as a preview.
    qlines, qblocked = _qbo_lines(b[P.POSTED] + b[P.READY])
    qgroups = C.build_qbo_lines(qlines)
    doc_number = Q.doc_number(float_name, week_ending)
    payload = C.build_qbo_payload(C.build_qbo_lines(qlines, per_entry=True), Q.QBO_PETTY_CASH, week_ending, doc_number,
                                  f"Petty cash {float_name}, week ending {week_ending}. Raised from the petty cash app.")
    journals = sorted({e["journal_entry"] for e in b[P.POSTED] if e.get("journal_entry")})
    not_posted_yet = [e for e in b[P.READY]]
    return {
        "week_ending": week_ending, "float": float_name, "sheet": sheet, "gates": gates,
        "can_post_erp": ok and bool(b[P.READY]),
        "companies_filter": True,   # post_erp accepts ``companies``; the page checks this before offering a choice
        # Top-ups never go to QuickBooks, so a week whose only posted line is a top-up has nothing to stage.
        "can_stage_qbo": (sheet["status"] == "Approved" and any(not cint(e.get("cash_in")) for e in b[P.POSTED])
                          and not qblocked),
        "counts": {k: len(v) for k, v in b.items()}, "entries": len(rows),
        "held": sorted(held.values(), key=lambda h: -h["value"]),
        "ready": {"lines": len(b[P.READY]),
                  "gross": round(sum(flt(e["amount"]) for e in b[P.READY] if not cint(e.get("cash_in"))), 2),
                  "topups": len([e for e in b[P.READY] if C.is_topup(e)]),
                  "topup": round(sum(flt(e["amount"]) for e in b[P.READY] if C.is_topup(e)), 2)},
        "posted": {"lines": len(b[P.POSTED]), "gross": round(sum(flt(e["amount"]) for e in b[P.POSTED]), 2),
                   "journals": [{"name": n, "submitted": frappe.db.get_value("Journal Entry", n, "docstatus") == 1}
                                for n in journals]},
        "erp": {"mode": line_mode, "journals": erp},
        "qbo": {"groups": qgroups, "payload": payload, "doc_number": doc_number,
                "blocked": [{"reason": k, "lines": len(v), "value": round(sum(flt(x["amount"]) for x in v), 2)}
                            for k, v in qblocked.items()],
                "unposted_lines": len(not_posted_yet), "queue": _queue_status(week_ending, float_name),
                "overlap": Q.overlap_warning(week_ending, float_name)["message"], "runner": RUNNER},
    }


def _only_companies(rows, b, companies):
    """Narrow a survey to the companies being posted. ``companies`` is a list, a JSON
    list or a comma string; empty means every company, as before.

    Some companies' petty cash is not booked in ERPNext at all (Bahati Ventures), so a
    week has to be postable for one company while another's lines stay where they are.
    Lines of the companies left out are untouched: not linked, not stamped, still ready.
    """
    if not companies:
        return rows, b
    if isinstance(companies, str):
        companies = json.loads(companies) if companies.strip().startswith("[") else companies.split(",")
    keep = {c.strip() for c in companies if c and c.strip()}
    if not keep:
        frappe.throw(_("Pick at least one company to post."))
    co = lambda e: e.get("company") or R.COMPANY   # noqa: E731
    return [e for e in rows if co(e) in keep], {k: [e for e in v if co(e) in keep] for k, v in b.items()}


def _require_gates(sheet, rows, b, allow_partial):
    gates = _gates(sheet, rows, b, _carry(b))
    bad = [g for g in gates if not g["ok"]]
    if bad:
        frappe.throw("<br>".join(f"<b>{g['label']}</b>: {g['detail'] or 'not met'}" for g in bad),
                     title=_("The week is not ready to post"))
    if b[P.BLOCKED] and not cint(allow_partial):
        frappe.throw(_("{0} line(s) are held, so posting now would put only part of the week in the books. "
                       "Fix them first, or post what is ready on purpose.").format(len(b[P.BLOCKED])),
                     title=_("Part of the week is held"))


def _lock_week(sheet_name):
    """The same lock a line change, a close and an approve take, taken first."""
    from vcl_finance.petty_cash import native
    return native.lock_sheet(sheet_name)


def _retrying(fn):
    """Each of these actions is the whole of its request. It runs in a fresh transaction
    that takes the week lock before it reads anything, and is run again if another session
    got in first. See ``native.exclusive_call``."""
    import functools

    @functools.wraps(fn)
    def run(*args, **kwargs):
        from vcl_finance.petty_cash.native import exclusive_call
        return exclusive_call(fn, args, kwargs)
    return run


def _stored_postings(sheet_name):
    try:
        return json.loads(frappe.db.get_value(SHEET, sheet_name, "posting_results") or "[]")
    except ValueError:
        return []


def _record_postings(sheet_name, made, stamp):
    """One record per company on the week, each with ITS journal. Replaced by company, so a
    repeat run updates the record it made instead of adding a second."""
    by_company = {r["company"]: r for r in _stored_postings(sheet_name)}
    for m in made:
        by_company[m["company"]] = {
            "company": m["company"], "journal_entry": m["journal"], "lines": m["lines"], "gross": m["gross"],
            "vat": m["vat"], "posted_on": str(stamp), "posted_by": frappe.session.user}
    frappe.db.set_value(SHEET, sheet_name, "posting_results",
                        json.dumps([by_company[c] for c in sorted(by_company)], indent=1), update_modified=False)


def posting_results(sheet_name, week_ending=None, float_name=None):
    """What has been posted for a week, by company, read fresh — nothing hidden.

    Each company's journal with its CURRENT state (draft, submitted, cancelled or gone),
    and the week's QuickBooks staging row with its state. ``companies_without_journal``
    lists any company that has posted or ready lines but no journal recorded.
    """
    from vcl_finance.petty_cash import weeks
    head = frappe.db.get_value(SHEET, sheet_name, ["week_ending", "float", "qbo_queue", "last_posting_error",
                                                   "last_posting_on"], as_dict=True)
    state = {0: "Draft", 1: "Submitted", 2: "Cancelled"}
    rows = []
    for r in _stored_postings(sheet_name):
        j = r.get("journal_entry")
        ds = frappe.db.get_value("Journal Entry", j, "docstatus") if j else None
        rows.append(dict(r, journal_state=state.get(ds, "Missing") if j else "None",
                         url=f"/app/journal-entry/{j}" if j else None))
    lines = weeks.week_entries(sheet_name, fields=["company", "journal_entry", "cancelled", "status", "amount"],
                               include_orphaned=True)
    on_journal = {}
    for e in lines:
        if e.get("journal_entry"):
            on_journal.setdefault(e.get("company") or R.COMPANY, set()).add(e["journal_entry"])
    recorded = {r["company"]: r["journal_entry"] for r in rows}
    hidden = sorted(c for c, js in on_journal.items() if not js <= {recorded.get(c)})
    queue = _queue_status(str(head.week_ending), head.float) if head.qbo_queue or True else None
    qbo = None
    if queue:
        qbo = {"row": queue["name"], "state": "Pushed" if queue["pushed"] else ("Approved" if queue.get("approved") else "Staged"),
               "doc_number": queue.get("doc_number"), "qbo_id": queue.get("qbo_journal_id"),
               "entries": queue.get("entry_count"), "amount": queue.get("total_out")}
    posted = [e for e in _survey(str(head.week_ending), head.float)[1][P.POSTED]]
    return {"companies": rows, "companies_without_record": hidden, "quickbooks": qbo,
            "not_for_quickbooks": _not_for_qbo(posted),
            "last_error": head.last_posting_error, "last_attempt": head.last_posting_on}


@frappe.whitelist(methods=["POST"])
@_retrying
def post_erp(week_ending, float_name=None, line_mode="grouped", allow_partial=0, companies=None, **kwargs):
    """Raise the week's DRAFT ERPNext journal and link every entry to it. Never submits.

    ``companies`` limits the run to those companies; the others' lines are left as they are.

    One draft journal per company, found again by its tag on a repeat, so two clicks, a
    retry or two requests at once never make a second. All of it or none of it: if any
    company's journal fails, nothing from this run is left behind and the week records
    the error.
    """
    P._guard()
    float_name = float_name or kwargs.get("float")
    sheet = _sheet(week_ending, float_name)
    _lock_week(sheet["name"])
    sheet = _sheet(week_ending, float_name)             # re-read under the lock
    rows, b = _survey(week_ending, float_name)
    rows, b = _only_companies(rows, b, companies)
    _require_gates(sheet, rows, b, allow_partial)
    if not b[P.READY]:
        if b[P.POSTED] and cint(frappe.db.get_value(SHEET, sheet["name"], "native_lines")):
            # A repeat request on a week that is already posted: say what exists, make nothing.
            out = preview(week_ending, float_name, line_mode)
            out["created"], out["already_posted"] = [], True
            return out
        frappe.throw(_("Nothing is ready to post in this week."))
    if line_mode not in ("grouped", "each"):
        frappe.throw(_("Unknown line mode {0}.").format(line_mode))

    by_company = {}
    for e in b[P.READY]:
        by_company.setdefault(e.get("company") or R.COMPANY, []).append(e)

    made = []
    stamp = now_datetime()
    frappe.db.savepoint("pc_post_erp")
    try:
        _post_companies(by_company, b, float_name, week_ending, line_mode, made, stamp)
        if len(made) == 1:
            frappe.db.set_value(SHEET, sheet["name"], "journal_entry", made[0]["journal"], update_modified=False)
        _record_postings(sheet["name"], made, stamp)
        frappe.db.set_value(SHEET, sheet["name"], {"last_posting_error": None, "last_posting_on": stamp},
                            update_modified=False)
    except Exception as e:      # noqa: BLE001
        if (isinstance(e, (frappe.QueryDeadlockError, frappe.QueryTimeoutError))
                or frappe.db.is_deadlocked(e) or frappe.db.is_timedout(e)):
            raise                                   # another session got in first: retried from the top
        # Nothing from this run stays: not one company's journal without the other's.
        frappe.db.rollback(save_point="pc_post_erp")
        frappe.db.set_value(SHEET, sheet["name"], {
            "last_posting_error": (str(e) or type(e).__name__)[:500], "last_posting_on": stamp},
            update_modified=False)
        frappe.db.commit()
        raise
    frappe.db.commit()
    out = preview(week_ending, float_name, line_mode)
    out["created"] = made
    return out


def _post_companies(by_company, b, float_name, week_ending, line_mode, made, stamp):
    for company, new_lines in sorted(by_company.items()):
        cash = P.cash_account(company, float_name)
        vat_acct = C.VAT_ACCOUNT.get(company)
        for acct in {x for x in (cash, vat_acct) if x}:
            if not frappe.db.exists("Account", acct):
                frappe.throw(_("Account {0} does not exist.").format(acct))
        tag_base = f"PC-{float_name}-{week_ending}"
        je, tag = P._open_journal(tag_base, company, week_ending)
        lines = list(new_lines)
        if not je.is_new():
            carried = [e for e in b[P.POSTED] if e.get("journal_entry") == je.name]
            lines = carried + lines
        try:
            rows_, totals = C.build_erp_lines([_line(x) for x in lines], cash, vat_acct, line_mode)
        except C.VatError as exc:
            frappe.throw(str(exc))

        je.voucher_type = "Journal Entry"
        je.company = company
        je.posting_date = week_ending
        je.cheque_no = tag
        je.cheque_date = week_ending
        je.user_remark = _("Petty cash {0}, week ending {1}: {2} entr{3}, gross {4:,.2f}, VAT {5:,.2f}{6}. "
                           "Raised as a draft by the petty cash app; review and submit.").format(
            float_name, week_ending, len(lines), "y" if len(lines) == 1 else "ies", totals["gross"], totals["vat"],
            _(", top-up {0:,.2f} from the bank").format(totals["topup"]) if totals.get("topup") else "")
        for r in rows_:
            je.append("accounts", {"account": r["account"], "debit_in_account_currency": r["debit"],
                                   "credit_in_account_currency": r["credit"], "user_remark": r["remark"]})
        je.flags.ignore_permissions = True
        je.save()                                            # DRAFT. Never submitted.

        for e in new_lines:
            frappe.db.set_value("Petty Cash Entry", e["name"], {
                "journal_entry": je.name, "posted_on": stamp, "posting_account": e["_account"],
                "vat_treatment": e["_treatment"], "vat_amount": e["_vat"], "net_amount": e["_net"],
            }, update_modified=False)
        made.append({"journal": je.name, "company": company, "lines": len(lines), "journal_rows": len(rows_),
                     "gross": totals["gross"], "vat": totals["vat"], "url": f"/app/journal-entry/{je.name}"})


@frappe.whitelist(methods=["POST"])
@_retrying
def stage_qbo(week_ending, float_name=None, **kwargs):
    """Put the week's tax-inclusive QuickBooks Expense in the queue, UNAPPROVED.

    Built from what ERPNext already holds, so ERPNext must be posted first. Approval is
    ``qbo.approve_push`` and the push is the runner, by hand. A week already pushed is
    refused: change it in QuickBooks.
    """
    Q._guard()
    float_name = float_name or kwargs.get("float")
    sheet = _sheet(week_ending, float_name)
    _lock_week(sheet["name"])
    sheet = _sheet(week_ending, float_name)             # re-read under the lock
    if sheet["status"] != "Approved":
        frappe.throw(_("The week must be approved before it goes to QuickBooks."))
    rows, b = _survey(week_ending, float_name)
    if not b[P.POSTED]:
        frappe.throw(_("Post the week to ERPNext first. The QuickBooks expense is built from what ERPNext holds."))
    bad = [e for e in b[P.POSTED] if e["_vat_problem"]]
    if bad:
        frappe.throw(_("{0} posted line(s) have a VAT problem: {1}").format(
            len(bad), "; ".join(sorted({e['_vat_problem'] for e in bad}))))

    lines, blocked = _qbo_lines(b[P.POSTED])
    groups = C.build_qbo_lines(lines)
    if not groups:
        frappe.throw(_("There is nothing to send to QuickBooks for this week."))
    doc_number = Q.doc_number(float_name, week_ending)
    journals = sorted({e["journal_entry"] for e in b[P.POSTED] if e.get("journal_entry")})
    # Sent one line per entry so each QuickBooks line carries its date, purpose and ETR.
    payload = C.build_qbo_payload(C.build_qbo_lines(lines, per_entry=True), Q.QBO_PETTY_CASH, week_ending, doc_number,
                                  f"Petty cash {float_name}, week ending {week_ending}. Raised from the petty cash app; "
                                  f"mirrors ERPNext {', '.join(journals)}.")

    name = Q._row_name(week_ending, float_name)
    if frappe.db.exists(Q.QUEUE, name):
        row = frappe.get_doc(Q.QUEUE, name)
        if row.qbo_journal_id:
            frappe.throw(_("This week is already in QuickBooks as {0}. Change it there.").format(row.qbo_journal_id))
    else:
        row = frappe.new_doc(Q.QUEUE)
        row.week_ending, row.float = week_ending, float_name

    row.company = R.COMPANY
    row.doc_number = doc_number
    row.erp_journals = ", ".join(journals)
    row.entry_count = len([e for e in b[P.POSTED] if e["name"] in {n for g in groups for n in g["entries"]}])
    row.total_out, row.total_in = round(sum(g["amount"] for g in groups), 2), 0
    row.payload_json = json.dumps(payload, indent=2)
    row.payload_hash = Q._hash(payload)
    row.block_reason = "\n".join(f"{len(v)} line(s), KES {sum(flt(x['amount']) for x in v):,.0f}: {k}"
                                 for k, v in blocked.items()) or None
    row.approved, row.approved_by, row.approved_at = 0, None, None      # a re-stage needs a fresh sign-off
    row.flags.ignore_permissions = True
    row.save()

    staged = {n for g in groups for n in g["entries"]}
    for n in staged:
        frappe.db.set_value("Petty Cash Entry", n, "qbo_queue", row.name, update_modified=False)
    frappe.db.set_value(SHEET, sheet["name"], "qbo_queue", row.name, update_modified=False)
    frappe.db.commit()
    return {"queue_row": row.name, "doc_number": doc_number, "lines": len(groups), "entries": len(staged),
            "not_for_quickbooks": _not_for_qbo(b[P.POSTED]),
            "held": [{"reason": k, "lines": len(v), "entries": [x["name"] for x in v]} for k, v in blocked.items()],
            "amount": row.total_out, "blocked": row.block_reason, "approved": False, "payload": payload,
            "warning": Q.overlap_warning(week_ending, float_name)["message"], "runner": RUNNER}


def discard_drafts(sheet_name):
    """Controlled regeneration: take a week's DRAFT posting apart so its lines can change.

    Called by reopening the week, under the week lock. Two states, two ways back:

    * a **submitted** journal is in the ledger. Refused: it is cancelled or reversed in
      ERPNext, by whoever owns that decision.
    * a QuickBooks expense that has been **pushed** is in QuickBooks. Refused: it is
      changed there.
    * otherwise every company's **draft** journal is deleted, the unpushed QuickBooks
      staging row is deleted, and the links on the lines, on the week and in the
      per-company posting record are cleared. Approving and posting again builds fresh
      drafts from the lines as they then are.

    Returns what was discarded.
    """
    from vcl_finance.petty_cash import weeks
    head = frappe.db.get_value(SHEET, sheet_name, ["week_ending", "float"], as_dict=True)
    lines = weeks.week_entries(sheet_name, fields=["name", "journal_entry", "qbo_queue"], include_orphaned=True)
    journals = sorted({e["journal_entry"] for e in lines if e.get("journal_entry")}
                      | {r["journal_entry"] for r in _stored_postings(sheet_name) if r.get("journal_entry")})
    state = {j: frappe.db.get_value("Journal Entry", j, "docstatus") for j in journals}
    submitted = sorted(j for j, ds in state.items() if ds == 1)
    if submitted:
        frappe.throw(
            _("This week is on submitted journal {0}. Cancel or reverse it in ERPNext before reopening the week.")
            .format(", ".join(submitted)), title=_("Journal is submitted"))
    queue_name = Q._row_name(str(head.week_ending), head.float)
    queue = frappe.db.get_value(Q.QUEUE, queue_name, ["name", "qbo_journal_id"], as_dict=True) if frappe.db.exists(Q.QUEUE, queue_name) else None
    if queue and queue.qbo_journal_id:
        frappe.throw(
            _("This week is already in QuickBooks as {0}. Change it there; the week cannot be reopened here.")
            .format(queue.qbo_journal_id), title=_("Already in QuickBooks"))

    drafts = sorted(j for j, ds in state.items() if ds == 0)
    for e in lines:
        if e.get("journal_entry") or e.get("qbo_queue"):
            frappe.db.set_value("Petty Cash Entry", e["name"],
                                {"journal_entry": None, "posted_on": None, "qbo_queue": None}, update_modified=False)
    for j in drafts:
        frappe.delete_doc("Journal Entry", j, force=1, ignore_permissions=True)
    if queue:
        frappe.delete_doc(Q.QUEUE, queue.name, force=1, ignore_permissions=True)
    frappe.db.set_value(SHEET, sheet_name, {"journal_entry": None, "qbo_queue": None, "posting_results": None},
                        update_modified=False)
    if drafts or queue:
        frappe.get_doc(SHEET, sheet_name).add_comment("Info", _(
            "Week reopened: discarded draft journal(s) {0}{1}. Approve and post again to rebuild them.").format(
                ", ".join(drafts) or "none", _(" and the QuickBooks staging row {0}").format(queue.name) if queue else ""))
    return {"journals": drafts, "quickbooks_row": queue.name if queue else None}
