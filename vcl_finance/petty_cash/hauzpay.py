"""Hauz Pay statement import — the Frappe layer over ``hauzpay_core``.

Someone downloads the weekly Hauz Pay statement (.xlsx), uploads it (a private File),
and calls:

* ``preview_statement(file_url)`` — what WOULD happen, per Sun-Sat week. Writes nothing.
* ``import_statement(file_url, week_ending=None)`` — writes the new lines as ordinary
  rows on that week's Hauz-Pay ``Petty Cash Sheet`` (created if missing, opening
  carried forward by ``create_for_week``). The sheet save mirrors them into
  ``Petty Cash Entry`` as Unapproved, so they land in the normal approval queue.

Nothing here posts to ERPNext or QuickBooks; posting stays with ``weekpost``.

Every imported row carries its Hauz Pay transaction id in ``src_ref`` (the one lumped
charges row per week carries ``HZP-CHARGES`` plus the id of every charge folded into
it). An id found on ANY Hauz-Pay sheet, cancelled rows included, is never imported
again, so re-uploading an overlapping statement is safe. Closed / Submitted /
Approved weeks are never touched.

Rules (roster, known payees, keywords) live in ``hauzpay_rules.json`` beside this file
— a file rather than a doctype for now: they change rarely and go through review with
the code. On top of the roster, a phone that was already paid on the WAGES register of
a Hauz-Pay sheet (recipient written ``254XXXXXXXXX - NAME``) is routed as a wage again.

The whole import is one request, so one transaction: if any week fails, nothing is
written.
"""
import io
import json
import os
import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, getdate

from vcl_finance.petty_cash import hauzpay_core as C
from vcl_finance.petty_cash.api import PETTY_APPROVERS, _first_blank, _next_idx, _summary, _voucher_has_data
from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    _prior_close, create_for_week, week_saturday,
)

FLOAT = "Hauz-Pay"
RULES_PATH = os.path.join(os.path.dirname(__file__), "hauzpay_rules.json")
_PHONE_IN_TEXT = re.compile(r"(?:\+?254|\b0)(\d{9})\b")
_WAGE_RECIPIENT = re.compile(r"^\s*\+?254(\d{9})\s*-\s*(.+?)\s*$")


def load_rules():
    with open(RULES_PATH) as f:
        return json.load(f)


def _assert_finance():
    """Same role set as approving a line (``api.PETTY_APPROVERS``), checked in the body
    because a whitelisted method is reachable whatever the doctype permissions say."""
    if not (set(frappe.get_roles()) & PETTY_APPROVERS):
        frappe.throw(_("Only Finance can import a Hauz Pay statement."), frappe.PermissionError)


# ------------------------------------------------------------------ reading
def _read_statement(file_url):
    if not file_url:
        frappe.throw(_("Upload the Hauz Pay statement first."))
    name = frappe.db.get_value("File", {"file_url": file_url}, "name", order_by="creation desc")
    if not name:
        frappe.throw(_("Uploaded file not found: {0}").format(file_url))
    content = frappe.get_doc("File", name).get_content()
    if isinstance(content, str):
        content = content.encode("latin-1")
    from openpyxl import load_workbook
    try:
        # NOT read_only: the Hauz Pay export carries a wrong <dimension> tag and read_only
        # mode trusts it, returning only the first cell.
        wb = load_workbook(io.BytesIO(content), data_only=True)
    except Exception:
        frappe.throw(_("That file is not an Excel (.xlsx) workbook. Download the statement "
                       "from Hauz Pay as Excel and upload it unchanged."))
    ws = wb["Statement"] if "Statement" in wb.sheetnames else wb[wb.sheetnames[0]]
    try:
        return C.parse_rows(ws.iter_rows(values_only=True))
    except ValueError as e:
        frappe.throw(str(e))


def _phone_of(*texts):
    for t in texts:
        m = _PHONE_IN_TEXT.search(str(t or ""))
        if m:
            return m.group(1)
    return ""


def _is_charges_row(r):
    """A lumped-charges voucher keyed before this import existed. Three spellings live:
    voucher_no CHARGES (Jul-Aug backfill), recipient '... transaction charges' (Gen-1),
    recipient 'M-Pesa' with 'Transaction charges (lumped ...)' in the notes (June)."""
    return ((r.voucher_no or "").strip().upper() == "CHARGES"
            or "charges" in (r.recipient or "").lower()
            or "transaction charges" in (r.notes or "").lower())


def _sheet_rows(sheet_name):
    """The sheet's money rows as plain dicts for the core's legacy match."""
    out = []
    for r in frappe.get_all("Petty Cash Voucher", filters={"parent": sheet_name, "parenttype": "Petty Cash Sheet"},
                            fields=["txn_date", "amount", "cash_in", "voucher_no", "recipient",
                                    "notes", "cancelled", "src_ref"]):
        if r.cancelled or not r.txn_date or not flt(r.amount):
            continue
        out.append({"date": getdate(r.txn_date), "amount": flt(r.amount, 2), "cash_in": bool(r.cash_in),
                    "phone": _phone_of(r.voucher_no, r.recipient),
                    "is_charges": _is_charges_row(r),
                    "src_ref": r.src_ref, "label": r.recipient})
    for r in frappe.get_all("Petty Cash Wages Entry", filters={"parent": sheet_name, "parenttype": "Petty Cash Sheet"},
                            fields=["txn_date", "amount", "recipient", "cancelled", "src_ref"]):
        if r.cancelled or not r.txn_date or not flt(r.amount):
            continue
        out.append({"date": getdate(r.txn_date), "amount": flt(r.amount, 2), "cash_in": False,
                    "phone": _phone_of(r.recipient), "is_charges": False,
                    "src_ref": r.src_ref, "label": r.recipient})
    return out


def _live_state(weeks):
    """(sheets by Saturday, known src_refs, learned wage roster, default custodian)."""
    sheets = {}
    all_sheets = frappe.get_all("Petty Cash Sheet", filters={"float": FLOAT, "docstatus": ("<", 2)},
                                fields=["name", "week_ending", "status", "opening_balance", "custodian_name"],
                                order_by="week_ending desc")
    for s in all_sheets:
        sat = week_saturday(getdate(s.week_ending))
        if sat in weeks and sat not in sheets:   # newest wins if a legacy Fri sheet shares the week
            sheets[sat] = {"name": s.name, "status": s.status or "Draft",
                           "opening_balance": flt(s.opening_balance, 2), "rows": _sheet_rows(s.name)}

    known = {}
    for tbl in ("Petty Cash Voucher", "Petty Cash Wages Entry"):
        for parent, ref in frappe.db.sql(f"""
                select c.parent, c.src_ref from `tab{tbl}` c
                join `tabPetty Cash Sheet` s on s.name = c.parent
                where c.parenttype = 'Petty Cash Sheet' and s.float = %s and s.docstatus < 2
                  and ifnull(c.src_ref, '') != ''""", (FLOAT,)):
            for tok in re.split(r"[\s,]+", ref or ""):
                if tok and not tok.startswith(C.CHARGES_REF):
                    known[tok] = parent

    learned = {}
    for (rec,) in frappe.db.sql("""
            select w.recipient from `tabPetty Cash Wages Entry` w
            join `tabPetty Cash Sheet` s on s.name = w.parent
            where w.parenttype = 'Petty Cash Sheet' and s.float = %s and s.docstatus < 2
              and ifnull(w.cancelled, 0) = 0 and w.recipient like '%%-%%'
            order by s.week_ending asc""", (FLOAT,)):
        m = _WAGE_RECIPIENT.match(rec or "")
        if m:
            learned["254" + m.group(1)] = m.group(2)

    custodian = all_sheets[0].custodian_name if all_sheets else None
    return sheets, known, learned, custodian or "Shiro"


def _build_plan(file_url):
    parsed = _read_statement(file_url)
    weeks = {week_saturday(l["date"]) for l in parsed["lines"]}
    sheets, known, learned, custodian = _live_state(weeks)
    p = C.plan(parsed, load_rules(), sheets, learned_roster=learned, known_refs=known)
    _predict_openings(p)
    return p, custodian


def _predict_openings(p):
    """For a week the import will CREATE, say what opening the new sheet will get.

    ``create_for_week`` carries the previous Hauz-Pay sheet's close forward, whatever the
    statement says. When the week before is in this statement too, the import fills it
    first, so the new sheet opens at that week's statement close. Otherwise it is the
    last sheet on file — and a gap (weeks never loaded) shows up here as a difference.
    """
    prev = None
    for w in p["weeks"]:
        if w["action"] == "create":
            if prev and prev["action"] != "skip" and \
                    getdate(prev["week_ending"]) == getdate(w["week_ending"]) - timedelta(days=7) \
                    and prev["statement_closing"] is not None:
                w["sheet_opening"], w["opening_basis"] = prev["statement_closing"], "previous week in this statement"
            else:
                info = _prior_close(FLOAT, w["week_ending"])
                if info:
                    w["sheet_opening"] = info["balance"]
                    w["opening_basis"] = "carried from " + info["prior_name"]
            if w["sheet_opening"] is not None and w["statement_opening"] is not None:
                w["opening_difference"] = round(flt(w["sheet_opening"]) - w["statement_opening"], 2)
        elif w["sheet"]:
            w["opening_basis"] = "sheet " + w["sheet"]
        prev = w


# ------------------------------------------------------------------ API
@frappe.whitelist()
def preview_statement(file_url):
    """Parse the uploaded statement and say, per week, what an import would do. Writes nothing."""
    _assert_finance()
    p, _custodian = _build_plan(file_url)
    p["file_url"] = file_url
    return p


@frappe.whitelist(methods=["POST"])
def import_statement(file_url, week_ending=None):
    """Write the statement's NEW lines onto the Hauz-Pay sheets as ordinary draft rows.

    The plan is rebuilt here from the live data — the preview the browser holds is never
    trusted. ``week_ending`` (any date in the week) limits the import to that one week.
    """
    _assert_finance()
    p, custodian = _build_plan(file_url)
    only = week_saturday(getdate(week_ending)).isoformat() if week_ending else None

    done = []
    for wk in p["weeks"]:
        if only and wk["week_ending"] != only:
            continue
        new = [l for l in wk["lines"] if l["state"] == "new"]
        res = {"week_ending": wk["week_ending"], "sheet": wk["sheet"], "created": False,
               "vouchers": 0, "wages": 0, "charges": 0, "charges_amount": 0.0, "flagged": 0,
               "skipped": len(wk["lines"]) - len(new), "status": wk["status"]}
        if wk["action"] == "skip" or not new:
            res["note"] = (_("Week is {0} — left untouched.").format(wk["status"])
                           if wk["action"] == "skip" else _("Nothing new for this week."))
            done.append(res)
            continue
        res.update(_write_week(wk, new, custodian))
        done.append(res)

    totals = {k: sum(d.get(k) or 0 for d in done) for k in ("vouchers", "wages", "charges", "flagged", "skipped")}
    return {"weeks": done, "totals": totals, "balance": p["balance"]}


def _write_week(wk, new, custodian):
    we = getdate(wk["week_ending"])
    created = False
    name = wk["sheet"]
    if not name:
        name = create_for_week(week_ending=we, float_name=FLOAT, custodian_name=custodian,
                               opening_balance=wk["statement_opening"] or 0)
        created = True
    doc = frappe.get_doc("Petty Cash Sheet", name)
    if not frappe.has_permission("Petty Cash Sheet", "write", doc=doc):
        frappe.throw(_("You may not write to the petty cash sheet {0}.").format(name), frappe.PermissionError)
    if doc.docstatus != 0 or doc.status in C.LOCKED_STATUSES:
        frappe.throw(_("The Hauz-Pay week ending {0} is {1}; nothing was imported.").format(
            we.strftime("%d/%m/%Y"), doc.status))

    vouchers = sorted(doc.vouchers, key=lambda x: x.row_idx or 0)
    wages = sorted(doc.wages_entries, key=lambda x: x.row_idx or 0)

    def voucher_row():
        r = _first_blank(vouchers, lambda v: not _voucher_has_data(v))
        if r is None:
            r = doc.append("vouchers", {"row_idx": _next_idx(doc.vouchers)})
            vouchers.append(r)
        return r

    def wage_row():
        r = _first_blank(wages, lambda w: not (w.recipient or "").strip() and not flt(w.amount)
                         and not w.txn_date and not (w.reason or "").strip() and not (w.staff_id or "").strip())
        if r is None:
            r = doc.append("wages_entries", {"row_idx": _next_idx(doc.wages_entries)})
            wages.append(r)
        return r

    out = {"vouchers": 0, "wages": 0, "charges": 0, "charges_amount": 0.0, "flagged": 0}
    charges = [l for l in new if l["route"] == C.CHARGE]
    for l in new:
        if l["route"] == C.CHARGE:
            continue
        d = getdate(l["date"])
        if l["kind"] == "wage":
            r = wage_row()
            r.txn_date, r.entry_type, r.recipient = d, "Wage", l["recipient"][:140]
            r.reason, r.amount, r.src_ref = (l["reason"] or "")[:140], l["amount"], l["txid"]
            out["wages"] += 1
            continue
        r = voucher_row()
        notes = [l["narration"]] if l["narration"] else []
        if l["expense_category"]:
            notes.append("Hauz Pay category: " + l["expense_category"])
        if l["route"] == C.FLAGGED:
            notes.insert(0, "FLAG: needs category")
            out["flagged"] += 1
        r.txn_date, r.recipient, r.amount = d, l["recipient"][:140], l["amount"]
        r.voucher_no = (l["txid"] or "")[:20]
        r.cash_in = 1 if l["route"] == C.CASH_IN else 0
        r.category = "" if r.cash_in else (l["category"] or "OT")
        r.notes = " · ".join(notes)
        r.src_ref = l["txid"]
        out["vouchers"] += 1

    if charges:
        amt = round(sum(l["amount"] for l in charges), 2)
        refs = [l["txid"] for l in charges if l["txid"]]
        last = max(getdate(l["date"]) for l in charges)
        row = next((v for v in vouchers if (v.src_ref or "").startswith(C.CHARGES_REF)
                    and not v.locked and not v.cancelled), None)
        if row is None:
            row = voucher_row()
            row.recipient, row.category, row.cash_in, row.amount = "Hauz Pay transaction charges", "OT", 0, 0
            row.voucher_no, row.src_ref = "CHARGES", C.CHARGES_REF
        row.amount = round(flt(row.amount) + amt, 2)
        row.txn_date = max(getdate(row.txn_date), last) if row.txn_date else last
        row.src_ref = "\n".join([row.src_ref] + refs)
        n = len([t for t in row.src_ref.split("\n")[1:] if t])
        row.notes = f"Lumped Hauz Pay transaction charges for the week ({n} charges)"
        out["charges"], out["charges_amount"] = len(charges), amt

    doc.save()
    out.update({"sheet": doc.name, "created": created, "summary": _summary(doc)})
    return out
