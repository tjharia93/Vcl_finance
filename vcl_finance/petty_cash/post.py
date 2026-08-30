"""Posting a week of petty cash into ERPNext.

What a "post" is here: one **draft** Journal Entry per float per week per company,
carrying one line per approved entry — not one line per account. Tanuj asked for
separate entries deliberately, and the reason holds up: a grouped journal shows
`5216 Travel 41,300` and nothing else, so the only way to answer "what was that"
is to come back to this app. A journal with the lines on it answers the question
where the accountant is already standing.

**Nothing is ever submitted.** ``docstatus=0``, always. A person opens the journal
in ERPNext, reads it, and submits it. That is the VCL standing rule and it is also
the honest division of labour: this code knows which account a line was coded to,
and it does not know whether the week is right.

**Posting is partial by design.** A week is not a gate you pass once. Lines become
postable as they are approved and as the map fills in, so the run posts whatever is
ready now and reports what is held and why. Coming back next week and posting the
rest is the ordinary case, not an exception — which is why the state lives on the
LINE (``journal_entry`` set or not), and the week's state is derived from its lines
rather than stored. A stored week flag would go stale the moment somebody approved
one more line.

**Re-posting.** A draft journal for a week is rewritten in place, so approving three
more lines and posting again gives one journal, not four. Once a journal is
SUBMITTED it is never touched again — later lines go into a fresh journal for the
same week (``-2``, ``-3``). Amending somebody's submitted journal behind their back
is the one thing this must never do.

QuickBooks is not here. It goes through the existing QBO push queue, which already
knows how to talk to Intuit and already holds the token lock; a second path to the
same books is how the two disagree.
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from vcl_finance.petty_cash import resolve as R
from vcl_finance.petty_cash.api import PETTY_PRIV

# Where the cash sits, per COMPANY per float. One tin serves four companies, so a
# float alone cannot name the account: crediting BVL spend to "1110 - Cash - VCL"
# would put Bahati's cash movement in Vimit's books, and the journal would still
# balance — which is exactly why it has to be keyed on both.
FLOAT_CASH_ACCOUNT = {
    ("Vimit Converters Limited", "Cash"): "1110 - Cash - VCL",
    ("Vimit Converters Limited", "Hauz-Pay"): "Hauz-Pay Wallet - VCL",
    # One tin, but the money belongs to whoever the line was for. A Bahati line
    # credits Bahati's cash, and that is the whole reason this is keyed on the
    # pair — crediting it to Vimit's would still balance, which is what makes it
    # the dangerous failure rather than a loud one.
    ("BAHATI VENTURES LIMITED", "Cash"): "Cash - BVL",
}


def cash_account(company, float_name):
    """The float's account in this company's books, or None if there isn't one.

    None is a refusal, not a default. A company nobody has set up a petty cash
    account for must stop the run and say so; falling back to another company's
    cash account is the one failure here that produces a balanced, plausible,
    wrong journal.
    """
    return FLOAT_CASH_ACCOUNT.get((company or R.COMPANY, float_name))

# A line's own state in the run.
POSTED = "posted"      # already in a journal
READY = "ready"        # approved, mapped, will go in this run
BLOCKED = "blocked"    # something a person can fix — the useful bucket
EXCLUDED = "excluded"  # void, or a route deliberately marked never_post


def _guard():
    if not (set(frappe.get_roles()) & PETTY_PRIV):
        frappe.throw(_("Only Accounts Managers can post petty cash."), frappe.PermissionError)


def _subject(e):
    """A few words so the accountant can tell the lines apart in the journal.

    The memo leads when there is one. It is the same text QuickBooks gets, so the
    two books read alike rather than describing the same payment two ways.
    """
    for f in ("memo", "recipient", "notes", "source_key"):
        v = (e.get(f) or "").strip()
        if v:
            return v[:80]
    return e.get("source_type") or "Petty cash"


def _classify(e):
    """One entry → (state, account, reason). Never raises.

    The order matters. An account a person put on the line at approval is FINAL —
    it beats the map, including a map row nobody has approved yet. The map's own
    approval gate decides whether a choice becomes the standing rule for future
    lines; it has no business blocking the line the person was looking at when
    they made it. Getting this backwards would have made the picker unusable:
    teaching the map writes the row unapproved by design.
    """
    if e.get("journal_entry"):
        return POSTED, e.get("posting_account"), None
    if e.get("cancelled") or e.get("status") == "Void":
        return EXCLUDED, None, "Voided"
    if e.get("status") != "Approved":
        return BLOCKED, None, "Not approved yet"
    if not flt(e.get("amount")):
        return EXCLUDED, None, "Zero value"

    chosen = e.get("posting_account")
    if chosen:
        return READY, chosen, None

    r = R.resolve(e)
    if r["outcome"] == R.NEVER:
        return EXCLUDED, None, r["reason"]
    if r["outcome"] != R.POSTS:
        return BLOCKED, None, r["reason"]
    if not r["erp_account"]:
        return BLOCKED, None, "No account on the line and none in the map"
    return READY, r["erp_account"], None


def _entries(week_ending, float_name):
    filters = {"week_ending": week_ending}
    if float_name:
        filters["float"] = float_name
    return frappe.get_all(
        "Petty Cash Entry", filters=filters,
        fields=["name", "txn_date", "float", "company", "source_type", "source_key",
                "recipient", "notes", "memo", "amount", "cash_in", "status", "cancelled",
                "posting_account", "journal_entry", "posted_on"],
        order_by="txn_date asc, creation asc", limit_page_length=0,
    )


def _survey(week_ending, float_name):
    """Everything both the preview and the run need, computed once."""
    rows = _entries(week_ending, float_name)
    out = {POSTED: [], READY: [], BLOCKED: [], EXCLUDED: []}
    for e in rows:
        state, account, reason = _classify(e)
        e["_account"], e["_reason"] = account, reason
        out[state].append(e)
    return rows, out


def _state_of(buckets):
    if buckets[READY] and not buckets[POSTED]:
        return "not_posted"
    if buckets[READY] and buckets[POSTED]:
        return "part_posted"
    if buckets[POSTED] and buckets[BLOCKED]:
        return "part_posted"
    if buckets[POSTED]:
        return "posted"
    if buckets[BLOCKED]:
        return "blocked"
    return "nothing_to_post"


def _totals(rows):
    out = sum(flt(e["amount"]) for e in rows if not e.get("cash_in"))
    cin = sum(flt(e["amount"]) for e in rows if e.get("cash_in"))
    return round(out, 2), round(cin, 2)


@frappe.whitelist()
def preview_week(week_ending, float_name=None, **kwargs):
    """What posting this week would do. Reads only — safe on every render."""
    float_name = float_name or kwargs.get("float")
    rows, b = _survey(week_ending, float_name)

    held = {}
    for e in b[BLOCKED]:
        k = e["_reason"] or "Held"
        h = held.setdefault(k, {"reason": k, "lines": 0, "value": 0.0})
        h["lines"] += 1
        h["value"] = round(h["value"] + flt(e["amount"]), 2)

    ready_out, ready_in = _totals(b[READY])
    posted_out, posted_in = _totals(b[POSTED])

    journals = sorted({e["journal_entry"] for e in b[POSTED] if e.get("journal_entry")})
    docstatus = {j: frappe.db.get_value("Journal Entry", j, "docstatus") for j in journals}

    return {
        "week_ending": week_ending, "float": float_name,
        "state": _state_of(b),
        "counts": {k: len(v) for k, v in b.items()},
        "entries": len(rows),
        "ready": {"lines": len(b[READY]), "out": ready_out, "in": ready_in},
        "posted": {"lines": len(b[POSTED]), "out": posted_out, "in": posted_in},
        "held": sorted(held.values(), key=lambda h: -h["value"]),
        "journals": [{"name": j, "submitted": docstatus.get(j) == 1} for j in journals],
        "companies": sorted({e.get("company") or R.COMPANY for e in b[READY]}),
        "cash_account_missing": sorted(
            {f"{e.get('company') or R.COMPANY} · {e['float']}" for e in b[READY]
             if not cash_account(e.get("company"), e["float"])}),
    }


def _open_journal(tag_base, company, posting_date):
    """The draft journal for this week, or the next free tag after a submitted one."""
    seq, tag = 1, tag_base
    while True:
        existing = frappe.db.get_value(
            "Journal Entry", {"cheque_no": tag, "company": company},
            ["name", "docstatus"], as_dict=True)
        if not existing:
            return frappe.new_doc("Journal Entry"), tag
        if existing.docstatus == 0:
            je = frappe.get_doc("Journal Entry", existing.name)
            je.set("accounts", [])
            return je, tag
        # Submitted, or cancelled. Either way it is somebody else's document now.
        seq += 1
        tag = f"{tag_base}-{seq}"
        if seq > 20:
            frappe.throw(_("Too many journals already exist for {0}.").format(tag_base))


@frappe.whitelist()
def post_week(week_ending, float_name=None, **kwargs):
    """Post everything that is ready. Creates DRAFT journals only."""
    _guard()
    float_name = float_name or kwargs.get("float")
    _rows, b = _survey(week_ending, float_name)

    if not b[READY]:
        frappe.throw(_("Nothing is ready to post in this week. "
                       "{0} line(s) are held and {1} already posted.")
                     .format(len(b[BLOCKED]), len(b[POSTED])))

    by_company = {}
    for e in b[READY]:
        by_company.setdefault(e.get("company") or R.COMPANY, []).append(e)

    made, stamped = [], 0
    for company, lines in sorted(by_company.items()):
        missing = sorted({e["float"] for e in lines if not cash_account(company, e["float"])})
        if missing:
            frappe.throw(_("No petty cash account is set up for {0} — float {1}. "
                           "Add one before posting to this company.")
                         .format(company, ", ".join(missing)))

        tag_base = f"PC-{float_name or 'ALL'}-{week_ending}"
        je, tag = _open_journal(tag_base, company, week_ending)

        # Reusing a draft means rebuilding it from scratch, so the lines already in
        # it must be carried over — rebuilding from the ready lines alone would
        # quietly empty out everything posted on the earlier run.
        if not je.is_new():
            lines = [e for e in b[POSTED] if e.get("journal_entry") == je.name] + lines

        je.voucher_type = "Journal Entry"
        je.company = company
        je.posting_date = week_ending
        je.cheque_no = tag
        je.cheque_date = week_ending
        je.user_remark = _("Petty cash {0}, week ending {1} — {2} line(s). "
                           "Raised as a draft by the petty cash app; review and submit.").format(
                               float_name or "all floats", week_ending, len(lines))

        out_total, in_total = {}, {}
        for e in lines:
            cash = cash_account(company, e["float"])
            amt = flt(e["amount"], 2)
            remark = f"{e['txn_date'] or ''} {_subject(e)} [{e['name']}]".strip()
            if e.get("cash_in"):
                # Money into the tin: the float is debited, the source credited.
                je.append("accounts", {"account": e["_account"], "debit_in_account_currency": 0,
                                       "credit_in_account_currency": amt, "user_remark": remark})
                in_total[cash] = in_total.get(cash, 0) + amt
            else:
                je.append("accounts", {"account": e["_account"], "debit_in_account_currency": amt,
                                       "credit_in_account_currency": 0, "user_remark": remark})
                out_total[cash] = out_total.get(cash, 0) + amt

        # One cash line per float per direction, so the journal reads the way the
        # tin behaves: many expenses against one movement of cash.
        for cash, amt in sorted(out_total.items()):
            je.append("accounts", {"account": cash, "debit_in_account_currency": 0,
                                   "credit_in_account_currency": flt(amt, 2),
                                   "user_remark": _("Petty cash paid out")})
        for cash, amt in sorted(in_total.items()):
            je.append("accounts", {"account": cash, "debit_in_account_currency": flt(amt, 2),
                                   "credit_in_account_currency": 0,
                                   "user_remark": _("Petty cash received")})

        je.flags.ignore_permissions = True
        je.save()                       # DRAFT. Never submitted.

        stamp = now_datetime()
        for e in lines:
            if e.get("journal_entry") == je.name:
                continue          # carried over — its posted_on is the truth
            # Record the account it actually went to, not only that it went. A line
            # that took the map's account had none of its own; leaving it blank
            # would make the entry unable to answer "where did this post" once the
            # map row changed underneath it.
            frappe.db.set_value("Petty Cash Entry", e["name"],
                                {"journal_entry": je.name, "posted_on": stamp,
                                 "posting_account": e["_account"]},
                                update_modified=False)
            stamped += 1

        made.append({"journal": je.name, "company": company, "lines": len(lines),
                     "out": round(sum(out_total.values()), 2),
                     "in": round(sum(in_total.values()), 2)})

    frappe.db.commit()
    result = preview_week(week_ending, float_name)
    result["created"] = made
    result["posted_now"] = stamped
    return result


@frappe.whitelist()
def unpost_week(week_ending, float_name=None, **kwargs):
    """Undo a posting run — only while every journal is still a draft.

    Practising is only safe if it is reversible. A submitted journal is in the
    books, so this refuses rather than deleting it; unwinding that is a Journal
    Entry cancellation and belongs to whoever submitted it.
    """
    _guard()
    float_name = float_name or kwargs.get("float")
    _rows, b = _survey(week_ending, float_name)
    if not b[POSTED]:
        frappe.throw(_("Nothing has been posted for this week."))

    journals = sorted({e["journal_entry"] for e in b[POSTED] if e.get("journal_entry")})
    submitted = [j for j in journals
                 if frappe.db.get_value("Journal Entry", j, "docstatus") == 1]
    if submitted:
        frappe.throw(_("{0} is already submitted — cancel it in ERPNext first.")
                     .format(", ".join(submitted)))

    for e in b[POSTED]:
        frappe.db.set_value("Petty Cash Entry", e["name"],
                            {"journal_entry": None, "posted_on": None},
                            update_modified=False)
    for j in journals:
        if frappe.db.exists("Journal Entry", j):
            frappe.delete_doc("Journal Entry", j, force=1, ignore_permissions=True)

    frappe.db.commit()
    result = preview_week(week_ending, float_name)
    result["removed"] = journals
    return result
