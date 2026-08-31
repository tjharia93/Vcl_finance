"""The QuickBooks side of a posted petty cash week.

One QBO JournalEntry per float per week, mirroring the ERPNext one line for line.

**It is built from what ERPNext already posted, not from what was approved.** The
line set is "every entry carrying this week's ``journal_entry``", so the two books
cannot drift: a line is in both or in neither. Building it from approval state
instead would let a line that failed to reach the ERP journal still reach QBO, and
the first anyone would know is a tie-out that will not close.

**Nothing here talks to Intuit.** It cannot: the refresh token lives in
``/opt/vcl/CommandCentre/config/settings.toml`` behind an ``fcntl`` lock, and two
processes refreshing the same token family is how Intuit revokes the lot — see
the 2026-05-18 and 2026-08-20 incidents. Frappe Cloud has no access to that file
and must never grow its own token. So this stages a payload and the runner on the
CommandCentre box pushes it through the shared helper, exactly as the Bill queue
already works.

**Where a QBO account comes from.** The shared ``QBO Account Map`` crosswalk first
(ERP account → QBO account, approved), then ``Posting Map.qbo_account`` as a
petty-cash-specific override. That order is deliberate: filling the crosswalk
helps every pipeline that reads it, whereas an override helps only this one. It
also means coding a line once gives you both books — the two-book model is the
same chart mapped once, not two charts kept in step by hand.

**The double-posting hazard, stated plainly.** ``vat-recon post-petty-cash``
already pushes some petty cash into QBO as Purchase objects, driven from the VAT
workbook. That is a different trigger and a different object type, and nothing in
either system knows about the other. Until that overlap is settled, a week pushed
from here can land twice. `overlap_warning()` is what surfaces it; it is not a
guard, because only a person can tell which of the two is the one that should
stand.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from vcl_finance.petty_cash import resolve as R

# Cash and Cash Equivalents:Petty Cash — the float itself in QuickBooks.
QBO_PETTY_CASH = "95"

# QBO caps JournalEntry.DocNumber at 21 characters and rejects anything longer.
DOCNUMBER_MAX = 21


def doc_number(float_name, week_ending):
    """A stable, human-readable key, short enough for QuickBooks to accept.

    Stable matters more than pretty: it is how a re-push finds the journal it
    already made instead of creating a second one.
    """
    tag = "".join(c for c in (float_name or "ALL") if c.isalnum())
    base = f"PC-{tag}-{week_ending}"
    if len(base) <= DOCNUMBER_MAX:
        return base
    keep = DOCNUMBER_MAX - len(f"PC--{week_ending}")
    return f"PC-{tag[:max(keep, 1)]}-{week_ending}"


def _qbo_account(erp_account, source_type, source_key, company, chosen=None):
    """(qbo_id, why_not). The line's own choice first, then the shared crosswalk.

    The order mirrors the ERPNext side exactly, and for the same reason: an account
    a person picked on the line at approval is the decision, and re-deriving it at
    posting time would quietly discard it. The crosswalk is the fallback that makes
    the common case need no choice at all.
    """
    if chosen:
        return chosen, None
    if not erp_account:
        return None, "no ERP account on the line"

    row = frappe.get_all(
        "QBO Account Map", filters={"erp_account": erp_account, "approved": 1},
        fields=["qbo_account"], limit_page_length=1)
    if row and row[0].get("qbo_account"):
        return row[0]["qbo_account"], None

    m = R._map_row(company, source_type, source_key)
    if m and m.get("qbo_account"):
        return m["qbo_account"], None

    return None, f"{erp_account} is not mapped to a QuickBooks account"


def _lines(week_ending, float_name):
    """The entries ERPNext has already posted for this week."""
    filters = {"week_ending": week_ending, "journal_entry": ("is", "set"), "cancelled": 0}
    if float_name:
        filters["float"] = float_name
    return frappe.get_all(
        "Petty Cash Entry", filters=filters,
        fields=["name", "txn_date", "float", "company", "source_type", "source_key",
                "recipient", "notes", "memo", "amount", "cash_in", "posting_account",
                "qbo_account", "journal_entry"],
        order_by="txn_date asc, creation asc", limit_page_length=0,
    )


def _describe(e):
    """What the accountant reads against this line in QuickBooks.

    A memo, when somebody wrote one, beats the derived text — "Trizah" says who was
    paid and nothing about what for, and the person coding the line is the only one
    who still knows. The entry id stays on either way: it is the only thread back
    from a journal line to the voucher.
    """
    memo = (e.get("memo") or "").strip()
    if memo:
        return f"{memo} [{e['name']}]"
    who = (e.get("recipient") or e.get("notes") or e.get("source_key")
           or e.get("source_type") or "Petty cash")
    return f"{e.get('txn_date') or ''} {str(who)[:60]} [{e['name']}]".strip()


@frappe.whitelist()
def preview_qbo_journal(week_ending, float_name=None, **kwargs):
    """The exact JournalEntry that would be sent, plus what is stopping it.

    Read-only. Nothing is staged and nothing is pushed by looking.
    """
    float_name = float_name or kwargs.get("float")
    rows = _lines(week_ending, float_name)

    companies = sorted({e.get("company") or R.COMPANY for e in rows})
    off_book = [c for c in companies if not R.posts_to_qbo(c)]

    lines, blocked, out_total = [], {}, 0.0
    for e in rows:
        company = e.get("company") or R.COMPANY
        if not R.posts_to_qbo(company):
            continue                    # a real line with no home in QBO, not an error
        if e.get("cash_in"):
            # Money into the tin is not an expense and is posted at the bank by
            # somebody else. If one reaches here its route is missing never_post,
            # which is a mapping problem, not something to silently push.
            continue
        acct, why = _qbo_account(e.get("posting_account"), e.get("source_type"),
                                 e.get("source_key"), company, e.get("qbo_account"))
        amt = flt(e["amount"], 2)
        if not acct:
            b = blocked.setdefault(why, {"reason": why, "lines": 0, "value": 0.0})
            b["lines"] += 1
            b["value"] = round(b["value"] + amt, 2)
            continue
        lines.append({
            "DetailType": "AccountBasedExpenseLineDetail",
            "Amount": amt,
            "Description": _describe(e)[:4000],
            "AccountBasedExpenseLineDetail": {"AccountRef": {"value": acct}},
        })
        out_total += amt

    # No balancing lines. On an Expense the funding account is the header's
    # AccountRef, so the object balances by construction.
    #
    # Cash IN never appears here at all: money into the tin is posted physically
    # by whoever moves it at the bank, and pushing it from here would double-count
    # 2.6m. Those routes carry never_post, and resolve() excludes them before this
    # function ever sees them.

    payload = {
        # An Expense, not a journal. Petty cash spend IS an expense paid in cash,
        # and QuickBooks treats it as one: it appears in the Expenses list and it
        # reconciles against Petty Cash, which is a bank-type account. A journal
        # against a bank account sits outside both.
        #
        # AccountRef at the header is the funding account, so no balancing line is
        # needed — the object balances by construction rather than by our
        # arithmetic, which is one fewer thing to get wrong.
        "PaymentType": "Cash",
        "AccountRef": {"value": QBO_PETTY_CASH},
        "TxnDate": str(week_ending),
        "DocNumber": doc_number(float_name, week_ending),
        "PrivateNote": (f"Petty cash {float_name or 'all floats'}, week ending "
                        f"{week_ending}. Raised from the petty cash app; mirrors "
                        f"ERPNext {sorted({e['journal_entry'] for e in rows})}."),
        "Line": lines,
    } if lines else None

    return {
        "week_ending": week_ending, "float": float_name,
        "erp_posted_lines": len(rows),
        "qbo_lines": len(lines),
        "out": round(out_total, 2), "in": 0.0,
        "blocked": sorted(blocked.values(), key=lambda b: -b["value"]),
        "off_book_companies": off_book,
        "erp_journals": sorted({e["journal_entry"] for e in rows}),
        "doc_number": doc_number(float_name, week_ending),
        "payload": payload,
        "ready": bool(payload) and not blocked,
    }


@frappe.whitelist()
def overlap_warning(week_ending, float_name=None, **kwargs):
    """Whether this week may already be in QuickBooks by the other route.

    Reports; never blocks. `vat-recon post-petty-cash` creates QBO Purchase
    objects from the VAT workbook, and neither system records what the other
    sent, so the honest answer here is "a person has to look" rather than a
    confident all-clear that would be wrong the one time it mattered.
    """
    return {
        "week_ending": week_ending,
        "other_route": "vat-recon post-petty-cash (QBO Purchase, PaymentType Cash, "
                       "AccountRef Petty Cash 95)",
        "checked": False,
        "message": _(
            "Petty cash also reaches QuickBooks from the VAT workbook, as Purchase "
            "objects rather than a journal. Nothing records what that route has "
            "already sent, so check QuickBooks for this week before pushing — "
            "otherwise the same spend lands twice."
        ),
    }


# ── staging ───────────────────────────────────────────────────────────────────
# Frappe Cloud stages; the CommandCentre runner pushes. Nothing below talks to
# Intuit. This is the same shape stage_pi_to_queue.py uses for bills, with one
# simplification: every lookup the payload needs — Posting Map, QBO Account Map —
# already lives in Frappe, so the payload is built here rather than on the box,
# and the runner only has to POST what it is given.

import hashlib

from frappe.utils import now_datetime

from vcl_finance.petty_cash.api import PETTY_PRIV

QUEUE = "QBO Petty Cash Journal"


def _guard():
    if not (set(frappe.get_roles()) & PETTY_PRIV):
        frappe.throw(_("Only Accounts Managers can stage a QuickBooks journal."),
                     frappe.PermissionError)


def _hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]


def _row_name(week_ending, float_name):
    return f"QPCJ-{week_ending}-{float_name or 'ALL'}"


@frappe.whitelist()
def stage_week(week_ending, float_name=None, **kwargs):
    """Put this week's QBO journal in the queue, UNAPPROVED.

    Re-staging an already-pushed week is refused rather than quietly rebuilt: the
    journal is in QuickBooks, and the place to change it is there.
    """
    _guard()
    float_name = float_name or kwargs.get("float")
    p = preview_qbo_journal(week_ending, float_name)

    if not p["payload"]:
        frappe.throw(_("There is nothing to send. Post the week to ERPNext first — "
                       "the QuickBooks journal is built from what ERPNext posted."))

    name = _row_name(week_ending, float_name)
    if frappe.db.exists(QUEUE, name):
        row = frappe.get_doc(QUEUE, name)
        if row.qbo_journal_id:
            frappe.throw(_("This week is already in QuickBooks as journal {0}. "
                           "Change it there.").format(row.qbo_journal_id))
    else:
        row = frappe.new_doc(QUEUE)
        row.week_ending, row.float = week_ending, float_name

    row.company = R.COMPANY
    row.doc_number = p["doc_number"]
    row.erp_journals = ", ".join(p["erp_journals"])
    row.entry_count = p["qbo_lines"]
    row.total_out, row.total_in = p["out"], p["in"]
    row.payload_json = json.dumps(p["payload"], indent=2)
    row.payload_hash = _hash(p["payload"])
    row.block_reason = "\n".join(
        f"{b['lines']} line(s), KES {b['value']:,.0f} — {b['reason']}"
        for b in p["blocked"]) or None
    row.flags.ignore_permissions = True
    row.save()
    frappe.db.commit()

    return {"queue_row": row.name, "approved": bool(row.approved),
            "ready": p["ready"], "blocked": p["blocked"],
            "doc_number": row.doc_number, "lines": row.entry_count,
            "out": row.total_out, "in": row.total_in,
            "warning": overlap_warning(week_ending, float_name)["message"]}


@frappe.whitelist()
def approve_push(queue_row):
    """Agree that this payload may go to QuickBooks.

    Separate from staging on purpose. Staging says "here is what it would be";
    this says "send it". A row that still has a block_reason cannot be approved —
    approving an incomplete journal is how a week reaches QBO short, and short is
    worse than absent because it looks complete.
    """
    _guard()
    row = frappe.get_doc(QUEUE, queue_row)
    if row.qbo_journal_id:
        frappe.throw(_("Already pushed as {0}.").format(row.qbo_journal_id))
    if row.block_reason:
        frappe.throw(_("Some lines have no QuickBooks account:\n{0}")
                     .format(row.block_reason))
    row.approved = 1
    row.approved_by = frappe.session.user
    row.approved_at = now_datetime()
    row.flags.ignore_permissions = True
    row.save()
    frappe.db.commit()
    return {"queue_row": row.name, "approved": True}


@frappe.whitelist()
def pending_push():
    """What the runner should pick up. Approved, not yet pushed."""
    return frappe.get_all(
        QUEUE, filters={"approved": 1, "qbo_journal_id": ("is", "not set")},
        fields=["name", "week_ending", "float", "company", "doc_number",
                "entry_count", "total_out", "total_in", "payload_json",
                "attempts", "error_message"],
        order_by="week_ending asc", limit_page_length=0,
    )


@frappe.whitelist()
def mark_pushed(queue_row, qbo_journal_id=None, qbo_sync_token=None, error=None):
    """The runner's callback. Records the outcome, good or bad.

    A failure is recorded on the row rather than raised, because the runner is a
    batch: one journal QuickBooks rejects must not stop the rest, and the error
    has to survive somewhere a person will actually see it.
    """
    row = frappe.get_doc(QUEUE, queue_row)
    row.attempts = (row.attempts or 0) + 1
    row.last_attempt_at = now_datetime()
    if error:
        row.error_message = str(error)[:2000]
    else:
        row.qbo_journal_id = qbo_journal_id
        row.qbo_sync_token = qbo_sync_token
        row.pushed_at = now_datetime()
        row.error_message = None
    row.flags.ignore_permissions = True
    row.save()
    frappe.db.commit()
    return {"queue_row": row.name, "qbo_journal_id": row.qbo_journal_id}


@frappe.whitelist()
def suggest_qbo_account(erp_account=None, source_type=None, source_key=None, company=None):
    """What the QuickBooks side would resolve to for this ERP account, and why.

    Lets the approval screen show both legs at once instead of making somebody
    discover at push time that half the week has nowhere to go in QBO. Read-only.
    """
    company = company or COMPANY
    if not posts_to_qbo(company):
        return {"qbo": False, "account": None, "label": None,
                "reason": f"{company} is not kept in QuickBooks — ERPNext only."}
    acct, why = _qbo_account(erp_account, source_type, source_key, company)
    return {
        "qbo": True, "account": acct,
        "label": frappe.db.get_value("QBO Account", acct, "fully_qualified_name") if acct else None,
        "reason": why,
    }
