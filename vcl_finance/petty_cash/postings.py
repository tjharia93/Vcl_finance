"""What is signed and still has not reached a journal — grouped the way it is fixed.

The approval screen answers "does this line have my signature". This answers a
different question: "what is stopping the signed lines from posting", and the
answer is almost never about a line. It is about a ROUTE.

That distinction is the whole point of this module. There are 1,194 lines in the
ledger and 22 routes. Presenting the blockage per line invites solving it per
line — 192 identical decisions for Wages Entry · Wage — when one map row clears
all of them at once. So everything here is keyed on (company, source_type,
source_key) and a line count is a consequence, never the unit of work.

Read-only. Nothing here maps, approves or posts; it says what needs doing and the
existing whitelisted methods do it.
"""

import frappe
from frappe.utils import flt

from vcl_finance.petty_cash import resolve as R
from vcl_finance.petty_cash.post import cash_account


def _blocker(entry, route, has_map, map_approved):
    """Why this line cannot post, in the order a person would fix it."""
    if entry.get("journal_entry"):
        return None
    if entry.get("cancelled") or entry.get("status") == "Void":
        return None
    if entry.get("status") != "Approved":
        return "unsigned"
    if entry.get("posting_account"):
        return None                       # coded on the line; nothing blocks it
    if not has_map:
        return "no_map"
    if not map_approved:
        return "map_unapproved"
    if not route.get("erp_account"):
        return "map_has_no_account"
    return None


@frappe.whitelist()
def pending_postings(**kwargs):
    """Every route with signed lines that have not posted, and what is holding them.

    Deliberately spans ALL weeks. A route is mapped once and clears everywhere, so
    scoping this to one week would hide the fact that the same decision was already
    waiting five weeks ago.
    """
    entries = frappe.get_all(
        "Petty Cash Entry",
        filters={"cancelled": 0},
        fields=["name", "week_ending", "float", "company", "source_type", "source_key",
                "amount", "cash_in", "status", "posting_account", "qbo_account",
                "journal_entry"],
        limit_page_length=0,
    )

    maps = {}
    for m in frappe.get_all(
        "Posting Map",
        fields=["company", "source_type", "source_key", "is_default", "erp_account",
                "qbo_account", "approved", "never_post"],
        limit_page_length=0,
    ):
        maps[(m["company"], m["source_type"], m["source_key"] or "")] = m

    groups = {}
    for e in entries:
        company = e.get("company") or R.COMPANY
        st, sk = e.get("source_type") or "?", (e.get("source_key") or "").strip()
        row = maps.get((company, st, sk)) or maps.get((company, st, ""))
        has_map = bool(row)
        route = row or {}

        key = (company, st, sk)
        g = groups.setdefault(key, {
            "company": company, "source_type": st, "source_key": sk,
            "lines": 0, "value": 0.0, "signed": 0, "posted": 0, "unsigned": 0,
            "coded_on_line": 0, "weeks": set(), "blockers": {},
            "has_map": has_map, "map_approved": bool(route.get("approved")),
            "map_is_default": bool(route.get("is_default")),
            "never_post": bool(route.get("never_post")),
            "erp_account": route.get("erp_account"),
            "qbo_account": route.get("qbo_account"),
            "posts_to_qbo": R.posts_to_qbo(company),
            "cash_account_missing": False,
        })
        g["lines"] += 1
        g["value"] = round(g["value"] + flt(e.get("amount")), 2)
        g["weeks"].add(e.get("week_ending"))
        if e.get("journal_entry"):
            g["posted"] += 1
        if e.get("status") == "Approved":
            g["signed"] += 1
        else:
            g["unsigned"] += 1
        if e.get("posting_account"):
            g["coded_on_line"] += 1
        if not cash_account(company, e.get("float")):
            g["cash_account_missing"] = True

        why = _blocker(e, route, has_map, bool(route.get("approved")))
        if why:
            b = g["blockers"].setdefault(why, {"lines": 0, "value": 0.0})
            b["lines"] += 1
            b["value"] = round(b["value"] + flt(e.get("amount")), 2)

    out = []
    for g in groups.values():
        g["weeks"] = len(g["weeks"])
        # The single thing to do next, so the screen does not make you work it out.
        if g["never_post"]:
            g["next"] = "nothing — deliberately kept out of the books"
        elif not g["has_map"]:
            g["next"] = "map this route"
        elif not g["erp_account"]:
            g["next"] = "the map row has no account"
        elif not g["map_approved"]:
            g["next"] = "approve the mapping"
        elif g["posts_to_qbo"] and not g["qbo_account"]:
            g["next"] = "no QuickBooks account on the mapping"
        elif g["cash_account_missing"]:
            g["next"] = "no petty cash account for this company"
        elif g["blockers"].get("unsigned"):
            g["next"] = "sign the remaining lines"
        elif g["posted"] < g["lines"]:
            g["next"] = "ready — post the week"
        else:
            g["next"] = "done"
        out.append(g)

    out.sort(key=lambda g: (g["next"] == "done", -sum(
        b["value"] for b in g["blockers"].values()) or -g["value"]))

    return {
        "routes": out,
        "totals": {
            "routes": len(out),
            "lines": sum(g["lines"] for g in out),
            "value": round(sum(g["value"] for g in out), 2),
            "posted": sum(g["posted"] for g in out),
            "unsigned": sum(g["unsigned"] for g in out),
            "blocked_value": round(sum(
                b["value"] for g in out for b in g["blockers"].values()), 2),
        },
    }
