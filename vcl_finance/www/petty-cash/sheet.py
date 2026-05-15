"""Sheet editor — `/petty-cash/sheet?name=PCS-2026-0008`."""
from datetime import date, datetime, timedelta

import frappe
from frappe import _

from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    DAY_NAMES, VEHICLES,
)


DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.PermissionError)

    name = frappe.form_dict.get("name") or frappe.form_dict.get("sheet")
    if not name:
        frappe.local.flags.redirect_location = "/petty-cash/"
        raise frappe.Redirect

    if not frappe.has_permission("Petty Cash Sheet", "read", name):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    doc = frappe.get_doc("Petty Cash Sheet", name)
    # Calling .save() during page render is too aggressive — we just trust that
    # validate() back-fills the grid the next time the user changes anything.
    # For first-time render of a brand-new sheet, force-fill once:
    if doc.docstatus == 0 and not doc.vouchers:
        doc.ensure_grid()
        doc.save(ignore_permissions=True)
        doc = frappe.get_doc("Petty Cash Sheet", name)

    we = doc.week_ending
    if isinstance(we, str):
        we = datetime.fromisoformat(we).date()
    week_dates = [(we - timedelta(days=4 - i)) for i in range(6)] if we else [None] * 6

    parking_grid = {}
    for p in doc.parking_entries:
        parking_grid.setdefault(p.day_idx, {}).setdefault(p.vehicle, {})[p.slot] = p

    bike_rows = sorted(
        [m for m in doc.misc_entries if m.kind == "Bike Fuel"],
        key=lambda m: m.row_idx or 0,
    )
    forklift_rows = sorted(
        [m for m in doc.misc_entries if m.kind == "Forklift"],
        key=lambda m: m.row_idx or 0,
    )

    context.no_cache = 1
    context.title = f"Wk {doc.week_no or '?'} — {doc.name}"
    context.doc = doc
    context.parking_grid = parking_grid
    context.bike_rows = bike_rows
    context.forklift_rows = forklift_rows
    context.day_names = DAY_NAMES
    context.day_labels = DAY_LABELS
    context.week_dates = week_dates
    context.vehicles = VEHICLES
    return context
