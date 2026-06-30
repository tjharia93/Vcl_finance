"""Whitelisted JSON API for the keypad entry wizard (Phase 2).

``quick_entry`` appends ONE line to the right child table of an open sheet (the
grid is pre-seeded with blank rows by ``ensure_grid``, so we normally just fill
the first empty one). ``get_feed`` returns the running feed + live balance the
wizard renders. ``attach_receipt`` sets a voucher row's ``receipt`` (Attach Image)
from an already-uploaded Frappe File.

Ported from the prototype's ``routes/api.py`` (quick-entry) and
``routes/sheets.py`` (``_feed_items`` / ``_summary``), adapted to the Frappe field
names: voucher ``category`` Select + ``cash_in`` Check; wages ``entry_type``
Wage/Overtime/Piecework/Commission; loans ``amount_issued``/``amount_signed``;
parking ``day_idx`` Mon..Sat string; misc ``kind`` "Bike Fuel"/"Forklift".

Everything recomputes server-side — client amounts are never trusted for totals.
"""
import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, getdate, add_days

from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    VEHICLES, DAY_NAMES, CATEGORY_CODES,
)

# Wizard kind → display label + colour for the running feed (brand vars resolved
# client-side; here we just emit the CSS custom-property name).
TYPE_COLOR = {
    "voucher": "var(--vcl-blue, #1F5FBF)",
    "wage": "var(--vcl-sage, #6E8B7B)",
    "overtime": "var(--vcl-sage, #6E8B7B)",
    "commission": "#5B2C6F",
    "piecework": "#5B2C6F",
    "loan": "var(--vcl-amber, #B86B00)",
    "bike": "var(--vcl-blue-mid, #3E7BD6)",
    "forklift": "var(--vcl-navy, #14264A)",
    "parking": "var(--muted, #8A909E)",
}
CAT_COLOR = {
    "TG": "var(--vcl-blue, #1F5FBF)", "TE": "var(--vcl-blue-mid, #3E7BD6)",
    "SE": "var(--vcl-navy, #14264A)", "OA": "var(--vcl-sage, #6E8B7B)",
    "FD": "var(--vcl-amber, #B86B00)", "GP": "#5B2C6F", "OT": "var(--muted, #8A909E)",
    "IN": "var(--vcl-green, #1B7A45)",
}
CAT_LABEL = {
    "TG": "Transport-Goods", "TE": "Transport-Employee", "SE": "Spares / Eng",
    "OA": "Office / Admin", "FD": "Food", "GP": "Geeprint", "OT": "Other", "IN": "Cash-in",
}
WAGE_TYPE_LABEL = {
    "Wage": "Wage", "Overtime": "Overtime", "Piecework": "Piecework", "Commission": "Commission",
}


def _date_str(d):
    return str(d) if d else None


# ----------------------------------------------------------------------
# Summary + feed (ports _summary / _feed_items)
# ----------------------------------------------------------------------

def _summary(doc):
    # Cancelled (voided) rows are excluded from every figure here so cash-remaining,
    # expected close and variance all ignore them (they remain on the sheet for audit).
    cat = {c: 0.0 for c in CATEGORY_CODES}
    cat_in = 0.0
    for v in doc.vouchers:
        if v.cancelled:
            continue
        amt = flt(v.amount)
        if v.cash_in:
            cat_in += amt
        elif v.category in cat:
            cat[v.category] += amt

    parking_total = flt(sum(flt(p.amount) for p in doc.parking_entries if not p.cancelled))
    bike_total = flt(sum(flt(m.amount) for m in doc.misc_entries if m.kind == "Bike Fuel" and not m.cancelled))
    forklift_total = flt(sum(flt(m.amount) for m in doc.misc_entries if m.kind == "Forklift" and not m.cancelled))
    wages_total = flt(sum(flt(w.amount) for w in doc.wages_entries if not w.cancelled))
    loans_total = flt(sum(flt(l.amount_issued) for l in doc.loan_entries if not l.cancelled))
    voucher_out = flt(sum(cat.values()))

    total_out = voucher_out + parking_total + bike_total + forklift_total + wages_total + loans_total
    expected_close = flt((doc.opening_balance or 0) - total_out + cat_in, 2)
    variance = flt((doc.cash_count_end or 0) - expected_close, 2)

    return {
        "cat_out": cat,
        "cat_in": flt(cat_in, 2),
        "voucher_total_out": flt(voucher_out, 2),
        "parking_total": flt(parking_total, 2),
        "bike_total": flt(bike_total, 2),
        "forklift_total": flt(forklift_total, 2),
        "wages_total": flt(wages_total, 2),
        "loans_total": flt(loans_total, 2),
        "total_out": flt(total_out, 2),
        "expected_close": expected_close,
        "variance": variance,
        "opening_balance": flt(doc.opening_balance or 0, 2),
        "status": doc.status,
    }


def _feed_items(doc):
    """Flatten a sheet's non-blank entries into one chronological feed."""
    items = []

    for v in doc.vouchers:
        out_amt = flt(v.amount) if not v.cash_in else 0.0
        in_amt = flt(v.amount) if v.cash_in else 0.0
        date = _date_str(v.txn_date)
        ticks = {"pc": bool(v.pc_received), "etr": bool(v.etr_received)}
        if out_amt:
            code = v.category or "OT"
            items.append({
                "id": v.name, "kind": "voucher", "section": "voucher", "row_idx": v.row_idx,
                "date": date, "label": CAT_LABEL.get(code, code), "cat_code": code,
                "recipient": v.recipient or "", "voucher_no": v.voucher_no or "",
                "subtitle": (v.voucher_no or v.recipient or ""), "amount": out_amt,
                "direction": "out", "color": CAT_COLOR.get(code), "ticks": ticks,
                "receipt": v.receipt or None,
                "cancelled": bool(v.cancelled), "cancel_remark": v.cancel_remark or "",
            })
        if in_amt:
            items.append({
                "id": v.name, "kind": "voucher", "section": "voucher", "row_idx": v.row_idx,
                "date": date, "label": "Cash-in", "cat_code": "IN",
                "recipient": v.recipient or "", "voucher_no": v.voucher_no or "",
                "subtitle": v.voucher_no or "", "amount": in_amt, "direction": "in",
                "color": CAT_COLOR["IN"], "ticks": ticks, "receipt": v.receipt or None,
                "cancelled": bool(v.cancelled), "cancel_remark": v.cancel_remark or "",
            })

    for w in doc.wages_entries:
        if not flt(w.amount):
            continue
        t = w.entry_type or "Wage"
        kind = "commission" if t == "Commission" else ("piecework" if t == "Piecework" else "wage")
        items.append({
            "id": w.name, "kind": kind, "section": "wages", "row_idx": w.row_idx,
            "date": _date_str(w.txn_date), "label": WAGE_TYPE_LABEL.get(t, "Wage"),
            "recipient": w.recipient or "", "staff_id": w.staff_id or "", "reason": w.reason or "",
            "subtitle": w.reason or w.staff_id or "", "amount": flt(w.amount), "direction": "out",
            "color": TYPE_COLOR.get(kind), "ticks": {"paye": bool(w.paye)}, "receipt": None,
            "cancelled": bool(w.cancelled), "cancel_remark": w.cancel_remark or "",
        })

    for l in doc.loan_entries:
        if not flt(l.amount_issued):
            continue
        items.append({
            "id": l.name, "kind": "loan", "section": "loan", "row_idx": l.row_idx,
            "date": _date_str(l.txn_date), "label": "Loan", "recipient": l.recipient or "",
            "staff_id": l.staff_id or "", "reason": l.reason or "",
            "subtitle": l.reason or l.staff_id or "", "amount": flt(l.amount_issued),
            "direction": "out", "color": TYPE_COLOR["loan"], "ticks": {"paye": bool(l.paye)},
            "receipt": None,
            "cancelled": bool(l.cancelled), "cancel_remark": l.cancel_remark or "",
        })

    for m in doc.misc_entries:
        if not flt(m.amount):
            continue
        kind = "bike" if m.kind == "Bike Fuel" else "forklift"
        items.append({
            "id": m.name, "kind": kind, "section": "misc", "row_idx": m.row_idx,
            "date": _date_str(m.txn_date), "label": m.kind, "recipient": "",
            "notes": m.notes or "", "subtitle": m.notes or "", "amount": flt(m.amount),
            "direction": "out", "color": TYPE_COLOR.get(kind), "ticks": {}, "receipt": None,
            "cancelled": bool(m.cancelled), "cancel_remark": m.cancel_remark or "",
        })

    for p in doc.parking_entries:
        if not flt(p.amount):
            continue
        items.append({
            "id": p.name, "kind": "parking", "section": "parking", "row_idx": None,
            "day_idx": p.day_idx, "vehicle": p.vehicle, "slot": p.slot, "date": None,
            "label": "Parking", "recipient": p.vehicle,
            "subtitle": f"{p.day_idx} · slot {p.slot}", "amount": flt(p.amount),
            "direction": "out", "color": TYPE_COLOR["parking"], "ticks": {}, "receipt": None,
            "cancelled": bool(p.cancelled), "cancel_remark": p.cancel_remark or "",
        })

    # Newest first: dated desc, undated sink under dated (stable by id).
    items.sort(key=lambda it: (it["date"] or "", str(it["id"])), reverse=True)
    return items


@frappe.whitelist()
def get_feed(sheet):
    """Running feed for the wizard: every recorded entry + live balance figures."""
    if not frappe.has_permission("Petty Cash Sheet", "read", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    return {"items": _feed_items(doc), "summary": _summary(doc)}


# ----------------------------------------------------------------------
# Accounts-Manager guard (defined here so all call sites below can use it)
# ----------------------------------------------------------------------

PETTY_PRIV = {"Accounts Manager", "System Manager"}


def _is_accounts_manager():
    return bool(set(frappe.get_roles()) & PETTY_PRIV)


def _assert_can_write(sheet):
    """Block edits to a locked week unless the caller is an Accounts Manager."""
    if sheet.is_locked() and not _is_accounts_manager():
        frappe.throw("This week is closed. Only an Accounts Manager can edit it.",
                     frappe.PermissionError)


@frappe.whitelist()
def week_status(float_name, date):
    """For a given date + float, return the containing week's status WITHOUT creating it.

    Lets the entry wizard warn a custodian before they fill in an entry for a closed week.
    The petty-cash week runs Mon–Sat, anchored to Friday as ``week_ending``.
    Sunday is treated as the start of the coming week (next Friday).

    Returns::

        {
            "week_ending": "YYYY-MM-DD",   # Friday of the containing week
            "sheet": name_or_None,          # existing Petty Cash Sheet name, or None
            "status": "Draft"|"Closed"|"Submitted"|"Approved"|"New",
            "locked": bool,                 # True when status in Closed/Submitted/Approved
            "is_accounts_manager": bool,    # whether the caller may override a lock
        }
    """
    d = getdate(date)
    # Week runs Mon(0)–Sat(5), Friday(4) is the week_ending anchor.
    # Saturday belongs to the same (just-past) Friday.
    # Sunday opens the next week → next Friday.
    wd = d.weekday()
    if wd == 6:  # Sunday → coming week
        week_ending = d + timedelta(days=5)
    else:  # Mon–Sat: offset to the Friday of this week (negative for Sat → yesterday)
        week_ending = d + timedelta(days=(4 - wd))

    _LOCKED = {"Closed", "Submitted", "Approved"}
    existing = frappe.db.get_value(
        "Petty Cash Sheet",
        {"week_ending": week_ending, "float": float_name, "docstatus": ("<", 2)},
        ["name", "status"],
        as_dict=True,
    )
    if not existing:
        return {
            "week_ending": str(week_ending),
            "sheet": None,
            "status": "New",
            "locked": False,
            "is_accounts_manager": _is_accounts_manager(),
        }
    return {
        "week_ending": str(week_ending),
        "sheet": existing["name"],
        "status": existing["status"],
        "locked": existing["status"] in _LOCKED,
        "is_accounts_manager": _is_accounts_manager(),
    }


# ----------------------------------------------------------------------
# Quick-entry (ports /api/quick-entry)
# ----------------------------------------------------------------------

def _voucher_has_data(v):
    return bool(
        v.voucher_no or v.recipient or v.txn_date or v.notes
        or v.pc_received or v.etr_received or v.cash_in
        or flt(v.amount)
    )


def _first_blank(rows, is_blank):
    for r in rows:
        if is_blank(r):
            return r
    return None


def _next_idx(rows):
    return (max((r.row_idx or 0) for r in rows) + 1) if rows else 1


@frappe.whitelist()
def quick_entry(sheet, kind, **fields):
    """Append ONE line to the right child table of an open (Draft) sheet.

    Rejects Submitted/Approved/cancelled sheets. ``kind`` is one of:
    voucher | wage | commission | piecework | loan | bike | forklift | parking.
    Returns ``{entry_id, row_idx, kind, summary}``.
    """
    if not frappe.has_permission("Petty Cash Sheet", "write", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    _assert_can_write(doc)
    if doc.docstatus != 0 or doc.status in ("Submitted", "Approved"):
        frappe.throw(_("That week is closed — re-open it before adding entries."))

    kind = (kind or "").lower()
    txn_date = getdate(fields.get("txn_date")) if fields.get("txn_date") else None
    amount = flt(fields.get("amount"))
    target = None

    if kind == "voucher":
        cash_in = _truthy(fields.get("cash_in"))
        cat = (fields.get("category") or "").upper()
        if not cash_in and cat not in CATEGORY_CODES:
            frappe.throw(_("Pick a spend category (or tick Cash-in)."))
        target = _first_blank(
            sorted(doc.vouchers, key=lambda x: x.row_idx or 0),
            lambda v: not _voucher_has_data(v),
        )
        if target is None:
            target = doc.append("vouchers", {"row_idx": _next_idx(doc.vouchers)})
        target.txn_date = txn_date
        target.voucher_no = fields.get("voucher_no") or ""
        target.recipient = fields.get("recipient") or ""
        target.notes = fields.get("notes") or ""
        target.pc_received = 1 if _truthy(fields.get("pc_received")) else 0
        target.etr_received = 1 if _truthy(fields.get("etr_received")) else 0
        target.cash_in = 1 if cash_in else 0
        target.category = "" if cash_in else cat
        target.amount = amount

    elif kind in ("wage", "commission", "piecework", "overtime"):
        entry_type = {
            "wage": "Wage", "commission": "Commission",
            "piecework": "Piecework", "overtime": "Overtime",
        }[kind]
        target = _first_blank(
            sorted(doc.wages_entries, key=lambda x: x.row_idx or 0),
            lambda w: not (w.recipient or "").strip() and not flt(w.amount)
            and not w.txn_date and not (w.reason or "").strip() and not (w.staff_id or "").strip(),
        )
        if target is None:
            target = doc.append("wages_entries", {"row_idx": _next_idx(doc.wages_entries)})
        target.txn_date = txn_date
        target.entry_type = entry_type
        target.recipient = fields.get("recipient") or ""
        target.staff_id = fields.get("staff_id") or ""
        target.reason = fields.get("reason") or ""
        target.amount = amount
        target.paye = 1 if _truthy(fields.get("paye")) else 0

    elif kind == "loan":
        target = _first_blank(
            sorted(doc.loan_entries, key=lambda x: x.row_idx or 0),
            lambda l: not (l.recipient or "").strip() and not flt(l.amount_issued)
            and not flt(l.amount_signed) and not l.txn_date
            and not (l.reason or "").strip() and not (l.staff_id or "").strip(),
        )
        if target is None:
            target = doc.append("loan_entries", {"row_idx": _next_idx(doc.loan_entries)})
        target.txn_date = txn_date
        target.recipient = fields.get("recipient") or ""
        target.staff_id = fields.get("staff_id") or ""
        target.reason = fields.get("reason") or ""
        target.amount_issued = amount
        # Quick-add: default signed = issued; refine in the grid.
        target.amount_signed = amount
        target.paye = 1 if _truthy(fields.get("paye")) else 0

    elif kind in ("bike", "forklift"):
        mkind = "Bike Fuel" if kind == "bike" else "Forklift"
        rows = sorted([m for m in doc.misc_entries if m.kind == mkind], key=lambda x: x.row_idx or 0)
        target = _first_blank(
            rows, lambda m: not flt(m.amount) and not m.txn_date and not (m.notes or "").strip(),
        )
        if target is None:
            target = doc.append("misc_entries", {"kind": mkind, "row_idx": _next_idx(rows)})
        target.txn_date = txn_date
        target.amount = amount
        target.notes = fields.get("notes") or ""

    elif kind == "parking":
        day = fields.get("day_idx")
        # Accept either an int 0-5 or a Mon..Sat string.
        if isinstance(day, str) and day.isdigit():
            day = int(day)
        if isinstance(day, int):
            if day not in range(6):
                frappe.throw(_("Pick a day for the parking entry."))
            day = DAY_NAMES[day]
        if day not in DAY_NAMES:
            frappe.throw(_("Pick a day for the parking entry."))
        vehicle = fields.get("vehicle")
        if vehicle not in VEHICLES:
            frappe.throw(_("Pick a vehicle for the parking entry."))
        slots = sorted(
            [p for p in doc.parking_entries if p.day_idx == day and p.vehicle == vehicle],
            key=lambda x: x.slot or 0,
        )
        target = _first_blank(slots, lambda p: not flt(p.amount))
        if target is None:
            frappe.throw(_(
                "Both parking slots for {0} on {1} are already used."
            ).format(vehicle, day))
        target.amount = amount

    else:
        frappe.throw(_("Unknown entry kind '{0}'.").format(kind))

    doc.flags.ignore_permissions = False
    doc.save()

    summ = _summary(doc)
    return {
        "ok": True,
        "sheet": doc.name,
        "kind": kind,
        "entry_id": target.name,
        "row_idx": getattr(target, "row_idx", None),
        "summary": summ,
    }


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


# ----------------------------------------------------------------------
# Cancel / reinstate a single feed entry (soft-void — ERPNext never deletes;
# the row stays on the sheet for audit, just excluded from totals + posting).
# ----------------------------------------------------------------------

_CHILD_TABLES = (
    "vouchers", "wages_entries", "loan_entries", "misc_entries", "parking_entries",
)


def _find_row(doc, entry_id):
    """Locate the child row (and its table) whose ``name`` == entry_id."""
    for table in _CHILD_TABLES:
        for r in (doc.get(table) or []):
            if r.name == entry_id:
                return table, r
    return None, None


def _open_sheet_for_write(sheet):
    """Shared guard: write permission + sheet not Submitted/Approved. Returns the doc."""
    if not frappe.has_permission("Petty Cash Sheet", "write", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if doc.docstatus != 0 or doc.status in ("Submitted", "Approved"):
        frappe.throw(_("That week is closed — re-open it before changing entries."))
    return doc


@frappe.whitelist(methods=["POST"])
def cancel_entry(sheet, entry_id, remark=None):
    """Soft-cancel (void) ONE child row — it is NOT deleted.

    Sets ``cancelled=1`` + ``cancelled_on`` + ``cancel_remark`` on the row, re-saves
    the parent (which re-derives totals, excluding cancelled rows) and returns the
    recomputed ``summary``. The row stays on the sheet for the audit trail.
    ``entry_id`` is the feed item's ``id`` (the child row name).
    """
    doc = _open_sheet_for_write(sheet)
    _assert_can_write(doc)
    table, row = _find_row(doc, entry_id)
    if row is None:
        frappe.throw(_("That entry no longer exists on this sheet."))

    row.cancelled = 1
    row.cancelled_on = frappe.utils.now_datetime()
    row.cancel_remark = (remark or "").strip() or None

    doc.flags.ignore_permissions = False
    doc.save()

    return {"ok": True, "sheet": doc.name, "cancelled_in": table,
            "entry_id": entry_id, "summary": _summary(doc)}


@frappe.whitelist(methods=["POST"])
def reinstate_entry(sheet, entry_id):
    """Reverse a mistaken cancel — clears ``cancelled`` so the row counts again."""
    doc = _open_sheet_for_write(sheet)
    _assert_can_write(doc)
    table, row = _find_row(doc, entry_id)
    if row is None:
        frappe.throw(_("That entry no longer exists on this sheet."))

    row.cancelled = 0
    row.cancelled_on = None
    row.cancel_remark = None

    doc.flags.ignore_permissions = False
    doc.save()

    return {"ok": True, "sheet": doc.name, "reinstated_in": table,
            "entry_id": entry_id, "summary": _summary(doc)}


# ----------------------------------------------------------------------
# Receipt attach (Frappe-native; the file is uploaded via /api/method/upload_file
# first, then its file_url is set on the voucher row's Attach Image field).
# ----------------------------------------------------------------------

@frappe.whitelist()
def attach_receipt(sheet, voucher_name, file_url):
    """Set a voucher row's ``receipt`` Attach Image to an uploaded File's url."""
    if not frappe.has_permission("Petty Cash Sheet", "write", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    _assert_can_write(doc)
    if doc.docstatus != 0:
        frappe.throw(_("Sheet is submitted — cannot change receipts."))
    row = next((v for v in doc.vouchers if v.name == voucher_name), None)
    if row is None:
        frappe.throw(_("Voucher row not found on this sheet."))
    row.receipt = file_url
    doc.save()
    return {"ok": True, "voucher_name": voucher_name, "receipt": file_url}


@frappe.whitelist(methods=["POST"])
def close_week(sheet, cash_count_end):
    if not _is_accounts_manager():
        frappe.throw("Only an Accounts Manager can close a week.", frappe.PermissionError)
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    doc.cash_count_end = flt(cash_count_end)
    doc.status = "Closed"
    doc.closed_by = frappe.session.user
    doc.closed_on = frappe.utils.now_datetime()
    doc.save()  # controller recomputes total_out/expected_close/variance
    return {"name": doc.name, "status": doc.status, "expected_close": doc.expected_close,
            "cash_count_end": doc.cash_count_end, "variance": doc.variance}


@frappe.whitelist(methods=["POST"])
def reopen_week(sheet):
    if not _is_accounts_manager():
        frappe.throw("Only an Accounts Manager can reopen a week.", frappe.PermissionError)
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    doc.status = "Draft"
    doc.closed_by = None
    doc.closed_on = None
    doc.save()
    return {"name": doc.name, "status": doc.status, "expected_close": doc.expected_close,
            "cash_count_end": doc.cash_count_end, "variance": doc.variance}


@frappe.whitelist()
def range_report(from_date, to_date, float=None):
    if not _is_accounts_manager():
        frappe.throw("Accounts Manager only.", frappe.PermissionError)
    filters = {"week_ending": ["between", [from_date, to_date]]}
    if float:
        filters["float"] = float
    names = frappe.get_all("Petty Cash Sheet", filters=filters, pluck="name")
    by_cat, weeks = {}, []
    out = tin = wages = loans = parking = bike = forklift = 0.0
    for nm in names:
        d = frappe.get_doc("Petty Cash Sheet", nm)
        for v in d.vouchers:
            if v.cancelled: continue
            if v.cash_in: tin += flt(v.amount)
            else:
                out += flt(v.amount)
                if v.category:  # skip blank/None rows — would pollute by_cat with a None key
                    by_cat[v.category] = by_cat.get(v.category, 0.0) + flt(v.amount)
        for w in d.wages_entries:
            if not w.cancelled: out += flt(w.amount); wages += flt(w.amount)
        for l in d.loan_entries:
            if not l.cancelled: out += flt(l.amount_issued); loans += flt(l.amount_issued)
        for p in d.parking_entries:
            if not p.cancelled: out += flt(p.amount); parking += flt(p.amount)
        for m in d.misc_entries:
            if m.cancelled: continue
            out += flt(m.amount)
            if m.kind == "Forklift": forklift += flt(m.amount)
            else: bike += flt(m.amount)
        weeks.append({"name": d.name, "week_ending": str(d.week_ending), "float": d.float,
                      "expected_close": d.expected_close, "cash_count_end": d.cash_count_end,
                      "variance": d.variance, "status": d.status})
    return {"from_date": from_date, "to_date": to_date, "float": float,
            "total_out": out, "total_in": tin, "net": tin - out, "by_category": by_cat,
            "sections": {"wages": wages, "loans": loans, "parking": parking,
                         "bike": bike, "forklift": forklift},
            "weeks": sorted(weeks, key=lambda x: x["week_ending"])}


# ----------------------------------------------------------------------
# Petty Cash Analytics — Accounts-Manager-only aggregate view.
# Server enforces the role (never trust the client); cancelled rows are excluded
# from every spend figure but surfaced separately as "voided".
# ----------------------------------------------------------------------

_ANALYTICS_ROLES = {"Accounts Manager", "System Manager"}


def _assert_accounts_manager():
    if not (set(frappe.get_roles()) & _ANALYTICS_ROLES):
        frappe.throw(
            _("Petty Cash Analytics is restricted to Accounts Managers."),
            frappe.PermissionError,
        )


def _sheet_closing(sheet):
    """Cash carried out of a sheet: physical count if entered, else expected close."""
    cnt = flt(sheet.cash_count_end)
    return cnt if cnt else flt(sheet.expected_close)


@frappe.whitelist()
def petty_cash_analytics(period="8"):
    """Aggregate analytics across Petty Cash Sheets. Accounts-Manager-only.

    ``period`` = number of trailing weeks ("4"/"8"/"12") or "all". Cancelled rows
    are excluded from all spend figures; voided entries are reported separately.
    """
    _assert_accounts_manager()

    weeks = int(period) if str(period).isdigit() else None
    filters = {}
    if weeks:
        filters["week_ending"] = (">=", add_days(getdate(), -7 * weeks))

    sheets = frappe.get_all(
        "Petty Cash Sheet", filters=filters,
        fields=["name", "float", "week_ending", "week_no", "opening_balance",
                "expected_close", "cash_count_end", "variance", "status", "custodian_name"],
        order_by="week_ending asc",
    )

    cat = {c: 0.0 for c in CATEGORY_CODES}
    total_out = total_in = 0.0
    sections = {"wages": 0.0, "loans": 0.0, "parking": 0.0, "bike": 0.0, "forklift": 0.0}
    by_float = {}                 # float -> {out, in, current, week_ending}
    week_bucket = {}              # week_ending(str) -> {out, in, cat:{...}}
    variance_trend = []
    recipients = {}               # recipient -> spend
    voided_count = 0
    voided_value = 0.0
    voided_log = []
    voucher_spend_n = 0
    voucher_receipt_n = 0

    for s in sheets:
        we = str(s.week_ending)
        wk = week_bucket.setdefault(we, {"out": 0.0, "in": 0.0, "cat": {c: 0.0 for c in CATEGORY_CODES}})
        fl = by_float.setdefault(s.float, {"out": 0.0, "in": 0.0, "current": 0.0, "week_ending": None})
        # latest sheet per float drives the "current cash position"
        if fl["week_ending"] is None or we >= fl["week_ending"]:
            fl["week_ending"] = we
            fl["current"] = _sheet_closing(s)

        doc = frappe.get_doc("Petty Cash Sheet", s.name)

        def _void(amount, kind, label, date, remark, when):
            nonlocal voided_count, voided_value
            voided_count += 1
            voided_value += flt(amount)
            voided_log.append({
                "date": str(date) if date else we, "kind": kind, "label": label,
                "amount": flt(amount, 2), "remark": remark or "",
                "when": str(when) if when else None, "sheet": s.name,
                "float": s.float, "custodian": s.custodian_name or "",
            })

        for v in doc.vouchers:
            amt = flt(v.amount)
            if v.cancelled:
                if amt:
                    _void(amt, "voucher", CAT_LABEL.get(v.category or "OT", "Voucher"),
                          v.txn_date, v.cancel_remark, v.cancelled_on)
                continue
            if not amt:
                continue
            if v.cash_in:
                total_in += amt
                fl["in"] += amt
                wk["in"] += amt
            else:
                code = v.category if v.category in cat else "OT"
                cat[code] += amt
                wk["cat"][code] += amt
                total_out += amt
                fl["out"] += amt
                wk["out"] += amt
                voucher_spend_n += 1
                if v.pc_received or v.etr_received:
                    voucher_receipt_n += 1
                who = (v.recipient or "").strip()
                if who:
                    recipients[who] = recipients.get(who, 0.0) + amt

        for w in doc.wages_entries:
            amt = flt(w.amount)
            if w.cancelled:
                if amt:
                    _void(amt, "wage", w.entry_type or "Wage", w.txn_date, w.cancel_remark, w.cancelled_on)
                continue
            if amt:
                sections["wages"] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        for l in doc.loan_entries:
            amt = flt(l.amount_issued)
            if l.cancelled:
                if amt:
                    _void(amt, "loan", "Loan", l.txn_date, l.cancel_remark, l.cancelled_on)
                continue
            if amt:
                sections["loans"] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        for m in doc.misc_entries:
            amt = flt(m.amount)
            key = "bike" if m.kind == "Bike Fuel" else "forklift"
            if m.cancelled:
                if amt:
                    _void(amt, key, m.kind, m.txn_date, m.cancel_remark, m.cancelled_on)
                continue
            if amt:
                sections[key] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        for p in doc.parking_entries:
            amt = flt(p.amount)
            if p.cancelled:
                if amt:
                    _void(amt, "parking", f"Parking · {p.vehicle}", None, p.cancel_remark, p.cancelled_on)
                continue
            if amt:
                sections["parking"] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        if flt(s.cash_count_end):
            variance_trend.append({
                "week_ending": we, "week_no": s.week_no, "float": s.float,
                "expected_close": flt(s.expected_close, 2),
                "cash_count_end": flt(s.cash_count_end, 2),
                "variance": flt(s.variance, 2),
            })

    weeks_sorted = sorted(week_bucket.keys())
    category_trend = {
        "weeks": weeks_sorted,
        "series": {c: [flt(week_bucket[w]["cat"][c], 2) for w in weeks_sorted] for c in CATEGORY_CODES},
        "out": [flt(week_bucket[w]["out"], 2) for w in weeks_sorted],
        "in": [flt(week_bucket[w]["in"], 2) for w in weeks_sorted],
    }
    top_recipients = sorted(
        ({"recipient": r, "total": flt(v, 2)} for r, v in recipients.items()),
        key=lambda x: -x["total"],
    )[:10]
    coverage = round(100.0 * voucher_receipt_n / voucher_spend_n, 1) if voucher_spend_n else None
    voided_log.sort(key=lambda x: (x["when"] or x["date"] or ""), reverse=True)

    return {
        "currency": "KES",
        "period": period,
        "weeks": len(weeks_sorted),
        "sheet_count": len(sheets),
        "from": weeks_sorted[0] if weeks_sorted else None,
        "to": weeks_sorted[-1] if weeks_sorted else None,
        "kpi": {
            "total_out": flt(total_out, 2),
            "total_in": flt(total_in, 2),
            "net": flt(total_in - total_out, 2),
            "voided_count": voided_count,
            "voided_value": flt(voided_value, 2),
            "receipt_coverage_pct": coverage,
            "voucher_spend_count": voucher_spend_n,
        },
        "by_category": [
            {"code": c, "label": CAT_LABEL.get(c, c), "total": flt(cat[c], 2)}
            for c in CATEGORY_CODES
        ],
        "category_trend": category_trend,
        "by_float": [
            {"float": k, "out": flt(v["out"], 2), "in": flt(v["in"], 2),
             "current": flt(v["current"], 2)}
            for k, v in sorted(by_float.items())
        ],
        "sections": {k: flt(v, 2) for k, v in sections.items()},
        "variance_trend": variance_trend,
        "top_recipients": top_recipients,
        "voided_log": voided_log[:50],
    }
