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

import frappe
from frappe import _
from frappe.utils import flt, getdate

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
    cat = {c: 0.0 for c in CATEGORY_CODES}
    cat_in = 0.0
    for v in doc.vouchers:
        amt = flt(v.amount)
        if v.cash_in:
            cat_in += amt
        elif v.category in cat:
            cat[v.category] += amt

    parking_total = flt(sum(flt(p.amount) for p in doc.parking_entries))
    bike_total = flt(sum(flt(m.amount) for m in doc.misc_entries if m.kind == "Bike Fuel"))
    forklift_total = flt(sum(flt(m.amount) for m in doc.misc_entries if m.kind == "Forklift"))
    wages_total = flt(sum(flt(w.amount) for w in doc.wages_entries))
    loans_total = flt(sum(flt(l.amount_issued) for l in doc.loan_entries))
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
            })
        if in_amt:
            items.append({
                "id": v.name, "kind": "voucher", "section": "voucher", "row_idx": v.row_idx,
                "date": date, "label": "Cash-in", "cat_code": "IN",
                "recipient": v.recipient or "", "voucher_no": v.voucher_no or "",
                "subtitle": v.voucher_no or "", "amount": in_amt, "direction": "in",
                "color": CAT_COLOR["IN"], "ticks": ticks, "receipt": v.receipt or None,
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
# Receipt attach (Frappe-native; the file is uploaded via /api/method/upload_file
# first, then its file_url is set on the voucher row's Attach Image field).
# ----------------------------------------------------------------------

@frappe.whitelist()
def attach_receipt(sheet, voucher_name, file_url):
    """Set a voucher row's ``receipt`` Attach Image to an uploaded File's url."""
    if not frappe.has_permission("Petty Cash Sheet", "write", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if doc.docstatus != 0:
        frappe.throw(_("Sheet is submitted — cannot change receipts."))
    row = next((v for v in doc.vouchers if v.name == voucher_name), None)
    if row is None:
        frappe.throw(_("Voucher row not found on this sheet."))
    row.receipt = file_url
    doc.save()
    return {"ok": True, "voucher_name": voucher_name, "receipt": file_url}
