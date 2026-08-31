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

    # QuickBooks ids mean nothing on screen — "QuickBooks 233" is not readable by
    # anyone. Resolve the names once here rather than making the client fetch the
    # whole chart to label a handful of rows.
    wanted = {g["qbo_account"] for g in groups.values() if g.get("qbo_account")}
    labels = {}
    if wanted:
        labels = {a["name"]: a["fully_qualified_name"] for a in frappe.get_all(
            "QBO Account", filters={"name": ("in", list(wanted))},
            fields=["name", "fully_qualified_name"], limit_page_length=0)}

    out = []
    for g in groups.values():
        g["weeks"] = len(g["weeks"])
        g["qbo_label"] = labels.get(g.get("qbo_account"))
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

    # The other half of this screen: which WEEKS can be posted now. Routes say what
    # is blocked; weeks say what is ready to go. Both are needed, because the
    # workflow deliberately allows approving without posting — the backlog is
    # signed first and posted afterwards, in one reviewed pass.
    weeks = {}
    for e in entries:
        wk = (e.get("week_ending"), e.get("float"))
        if not wk[0]:
            continue
        w = weeks.setdefault(wk, {"week_ending": str(wk[0]), "float": wk[1],
                                  "ready": 0, "ready_value": 0.0, "posted": 0,
                                  "posted_value": 0.0, "held": 0, "unsigned": 0})
        company = e.get("company") or R.COMPANY
        st, sk = e.get("source_type") or "?", (e.get("source_key") or "").strip()
        row = maps.get((company, st, sk)) or maps.get((company, st, ""))
        if e.get("journal_entry"):
            w["posted"] += 1
            w["posted_value"] = round(w["posted_value"] + flt(e.get("amount")), 2)
            continue
        if row and row.get("never_post"):
            continue
        if e.get("status") != "Approved":
            w["unsigned"] += 1
            continue
        resolved = e.get("posting_account") or (
            row.get("erp_account") if row and row.get("approved") else None)
        if resolved and cash_account(company, e.get("float")):
            w["ready"] += 1
            w["ready_value"] = round(w["ready_value"] + flt(e.get("amount")), 2)
        else:
            w["held"] += 1

    week_list = sorted(weeks.values(), key=lambda w: w["week_ending"], reverse=True)

    return {
        "routes": out,
        "weeks": [w for w in week_list if w["ready"] or w["posted"] or w["held"]],
        "totals": {
            "routes": len(out),
            "lines": sum(g["lines"] for g in out),
            "value": round(sum(g["value"] for g in out), 2),
            "posted": sum(g["posted"] for g in out),
            "unsigned": sum(g["unsigned"] for g in out),
            "blocked_value": round(sum(
                b["value"] for g in out for b in g["blockers"].values()), 2),
            "ready": sum(w["ready"] for w in week_list),
            "ready_value": round(sum(w["ready_value"] for w in week_list), 2),
        },
    }


@frappe.whitelist(methods=["POST"])
def set_route_map(company, source_type, source_key=None, erp_account=None,
                  qbo_account=None, approved=None, never_post=None, reason=None):
    """Map a route once, for every line of it in every week.

    This is the write the postings screen makes, and it exists so the mapping is
    never done through the raw doctype again. The Desk let `source_key` be edited
    to something the entries do not carry, which produced a row that matched no
    line and failed silently — see the Parking incident.

    ``approved`` is passed explicitly and is never implied by setting an account.
    Choosing where a route posts and agreeing that it may post are two decisions,
    and collapsing them is how one person's guess becomes the standing rule.
    """
    from vcl_finance.petty_cash.api import PETTY_PRIV
    if not (set(frappe.get_roles()) & PETTY_PRIV):
        frappe.throw(frappe._("Only Accounts Managers can map a route."),
                     frappe.PermissionError)

    if not frappe.db.exists("Company", company):
        frappe.throw(frappe._("No such company: {0}").format(company))

    key = (source_key or "").strip()
    if erp_account:
        if not frappe.db.exists("Account", erp_account):
            frappe.throw(frappe._("No such account: {0}").format(erp_account))
        acct = frappe.db.get_value("Account", erp_account,
                                   ["company", "disabled", "is_group"], as_dict=True)
        if acct.company != company:
            frappe.throw(frappe._("{0} belongs to {1}, not {2}.")
                         .format(erp_account, acct.company, company))
        if acct.disabled:
            frappe.throw(frappe._("{0} is disabled.").format(erp_account))
        if acct.is_group:
            frappe.throw(frappe._("{0} is a group account — nothing can post to it.")
                         .format(erp_account))
    if qbo_account:
        if not R.posts_to_qbo(company):
            frappe.throw(frappe._("{0} is not kept in QuickBooks.").format(company))
        if not frappe.db.exists("QBO Account", qbo_account):
            frappe.throw(frappe._("No such QuickBooks account: {0}").format(qbo_account))

    name = frappe.db.get_value("Posting Map", {
        "company": company, "source_type": source_type, "source_key": key}, "name")
    doc = frappe.get_doc("Posting Map", name) if name else frappe.new_doc("Posting Map")
    if not name:
        doc.company, doc.source_type, doc.source_key = company, source_type, key
        # A blank key is the family default — one row for every number plate.
        doc.is_default = 1 if not key else 0

    if erp_account is not None:
        doc.erp_account = erp_account or None
    if qbo_account is not None:
        doc.qbo_account = qbo_account or None
    if never_post is not None:
        doc.never_post = 1 if int(never_post or 0) else 0
        # The reason is not optional on an exclusion, and this is the only place
        # the screen can supply one — without this the validator refused every
        # never_post row it was asked to save.
        if doc.never_post and reason:
            doc.never_post_reason = reason
        if doc.never_post and not (doc.never_post_reason or "").strip():
            frappe.throw(frappe._("Say why this route is kept out of the books."))
    if approved is not None:
        doc.approved = 1 if int(approved or 0) else 0
    if reason:
        doc.notes = ((doc.notes or "") + f"\n{frappe.utils.nowdate()} {frappe.session.user}: "
                     f"{reason}").strip()
    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()

    return {"map_row": doc.name, "approved": bool(doc.approved),
            "erp_account": doc.erp_account, "qbo_account": doc.qbo_account,
            "source_key": doc.source_key}


@frappe.whitelist()
def route_entries(company, source_type, source_key=None, limit=400):
    """The individual lines behind one route.

    The screen is deliberately organised by route, because that is how the work is
    done. But "192 lines, KES 1,154,881" is a number you have to trust, and nobody
    should have to approve a mapping for money they cannot look at. So a route
    opens to show what is actually inside it.

    Read-only, and ordered newest first — the recent weeks are the ones somebody
    still remembers.
    """
    key = (source_key or "").strip()
    filters = {"company": company, "source_type": source_type, "cancelled": 0}

    rows = frappe.get_all(
        "Petty Cash Entry", filters=filters,
        fields=["name", "txn_date", "week_ending", "float", "recipient", "notes",
                "memo", "amount", "cash_in", "status", "source_key",
                "posting_account", "journal_entry", "receipt", "pc_received",
                "etr_received"],
        order_by="txn_date desc, creation desc", limit_page_length=0,
    )

    # A family default (blank key) covers every key in the family, so it must show
    # every plate — filtering on the blank key itself would return nothing at all.
    if key:
        rows = [r for r in rows if (r.get("source_key") or "").strip() == key]

    total = len(rows)
    out = []
    for r in rows[: int(limit or 400)]:
        out.append({
            "name": r["name"],
            "txn_date": str(r["txn_date"]) if r["txn_date"] else None,
            "week_ending": str(r["week_ending"]) if r["week_ending"] else None,
            "float": r["float"],
            "source_key": r.get("source_key") or "",
            "subject": (r.get("memo") or r.get("recipient") or r.get("notes")
                        or r.get("source_key") or "—"),
            "amount": r["amount"],
            "cash_in": r["cash_in"],
            "status": r["status"],
            "posted": bool(r.get("journal_entry")),
            "journal_entry": r.get("journal_entry"),
            "coded": bool(r.get("posting_account")),
            "evidence": bool(r.get("receipt") or r.get("pc_received") or r.get("etr_received")),
        })
    return {"entries": out, "total": total, "shown": len(out),
            "value": round(sum(r["amount"] or 0 for r in rows), 2)}


@frappe.whitelist()
def unposted_lines(limit=500, **kwargs):
    """Every signed line that has not reached a journal, as LINES.

    Deliberately not grouped. The route view was the wrong shape for how this is
    actually worked: it asked somebody to reason about a category when what they
    have in front of them is a voucher, and it made a two-step job out of a
    one-step one. A person coding petty cash is looking at a payment, so the
    screen shows payments.

    Oldest first — the backlog is worked forwards, and the weeks nobody has
    touched are the ones that matter.
    """
    rows = frappe.get_all(
        "Petty Cash Entry",
        filters={"cancelled": 0, "status": "Approved", "journal_entry": ("is", "not set")},
        fields=["name", "txn_date", "week_ending", "float", "company", "source_type",
                "source_key", "category", "recipient", "notes", "memo", "amount",
                "cash_in", "posting_account", "qbo_account", "receipt", "pc_received",
                "etr_received"],
        order_by="week_ending asc, txn_date asc, creation asc",
        limit_page_length=int(limit or 500),
    )

    total = frappe.db.count("Petty Cash Entry", {
        "cancelled": 0, "status": "Approved", "journal_entry": ("is", "not set")})

    out = []
    for e in rows:
        company = e.get("company") or R.COMPANY
        # What the map would propose, shown as a suggestion the person can take or
        # ignore. It is NOT applied — every line is coded deliberately.
        r = R.resolve(e)
        out.append({
            "name": e["name"],
            "txn_date": str(e["txn_date"]) if e["txn_date"] else None,
            "week_ending": str(e["week_ending"]) if e["week_ending"] else None,
            "float": e["float"], "company": company,
            "route": f"{e.get('source_type') or '?'} · {e.get('source_key') or '—'}",
            "subject": (e.get("memo") or e.get("recipient") or e.get("notes")
                        or e.get("source_key") or "—"),
            "amount": e["amount"], "cash_in": e["cash_in"],
            "erp_account": e.get("posting_account"),
            "qbo_account": e.get("qbo_account"),
            "suggested_erp": r.get("erp_account") if not e.get("posting_account") else None,
            "posts_to_qbo": R.posts_to_qbo(company),
            "evidence": bool(e.get("receipt") or e.get("pc_received") or e.get("etr_received")),
            "ready": bool(e.get("posting_account") or r.get("outcome") == R.POSTS),
        })

    return {"lines": out, "shown": len(out), "total": total,
            "value": round(sum(x["amount"] or 0 for x in out), 2)}
