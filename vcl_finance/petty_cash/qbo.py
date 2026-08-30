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


def _qbo_account(erp_account, source_type, source_key, company):
    """(qbo_id, why_not). The shared crosswalk first, the local override second."""
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
                "recipient", "notes", "amount", "cash_in", "posting_account",
                "journal_entry"],
        order_by="txn_date asc, creation asc", limit_page_length=0,
    )


def _describe(e):
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

    lines, blocked, out_total, in_total = [], {}, 0.0, 0.0
    for e in rows:
        company = e.get("company") or R.COMPANY
        if not R.posts_to_qbo(company):
            continue                    # a real line with no home in QBO, not an error
        acct, why = _qbo_account(e.get("posting_account"), e.get("source_type"),
                                 e.get("source_key"), company)
        amt = flt(e["amount"], 2)
        if not acct:
            b = blocked.setdefault(why, {"reason": why, "lines": 0, "value": 0.0})
            b["lines"] += 1
            b["value"] = round(b["value"] + amt, 2)
            continue
        lines.append({
            "DetailType": "JournalEntryLineDetail",
            "Amount": amt,
            "Description": _describe(e)[:4000],
            "JournalEntryLineDetail": {
                "PostingType": "Credit" if e.get("cash_in") else "Debit",
                "AccountRef": {"value": acct},
            },
        })
        if e.get("cash_in"):
            in_total += amt
        else:
            out_total += amt

    # The float's own side, one line per direction — the tin moves once.
    if out_total:
        lines.append({
            "DetailType": "JournalEntryLineDetail", "Amount": round(out_total, 2),
            "Description": f"Petty cash paid out — {float_name or 'all floats'}",
            "JournalEntryLineDetail": {"PostingType": "Credit",
                                       "AccountRef": {"value": QBO_PETTY_CASH}},
        })
    if in_total:
        lines.append({
            "DetailType": "JournalEntryLineDetail", "Amount": round(in_total, 2),
            "Description": f"Petty cash received — {float_name or 'all floats'}",
            "JournalEntryLineDetail": {"PostingType": "Debit",
                                       "AccountRef": {"value": QBO_PETTY_CASH}},
        })

    payload = {
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
        "qbo_lines": max(len(lines) - bool(out_total) - bool(in_total), 0),
        "out": round(out_total, 2), "in": round(in_total, 2),
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
