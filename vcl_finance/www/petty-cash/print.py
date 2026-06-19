"""Print view — `/petty-cash/print?name=PCS-2026-0008&copies=3`.

Pulls the same Sheet doc the editor uses and hydrates the Phase 1 A4 landscape
template. Empty rows render as blank cells so the custodian can still hand-write
on a partly-filled printed sheet.
"""
from datetime import datetime, timedelta

import frappe
from frappe import _

from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    CATEGORY_CODES, DAY_NAMES, VEHICLES, VOUCHER_ROWS,
)


DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.PermissionError)

    name = frappe.form_dict.get("name")
    if not name:
        frappe.local.flags.redirect_location = "/petty-cash/"
        raise frappe.Redirect

    if not frappe.has_permission("Petty Cash Sheet", "read", name):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    doc = frappe.get_doc("Petty Cash Sheet", name)

    try:
        copies = max(1, min(int(frappe.form_dict.get("copies") or 1), 4))
    except (TypeError, ValueError):
        copies = 1

    # Chunk vouchers into 18-row pages for the continuation sheets.
    vouchers = sorted(doc.vouchers, key=lambda v: v.row_idx or 0)
    voucher_chunks = []
    for i in range(copies):
        start = i * VOUCHER_ROWS
        chunk = vouchers[start:start + VOUCHER_ROWS]
        # Pad with None so each printed page has exactly 18 rows.
        if len(chunk) < VOUCHER_ROWS:
            chunk = chunk + [None] * (VOUCHER_ROWS - len(chunk))
        voucher_chunks.append(chunk)

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

    we = doc.week_ending
    if isinstance(we, str):
        we = datetime.fromisoformat(we).date()
    week_dates = [(we - timedelta(days=4 - i)) for i in range(6)] if we else [None] * 6

    # Build a small parking-day-total dict so the template doesn't need maths.
    parking_by_day = {d: 0 for d in DAY_NAMES}
    parking_by_vehicle = {v: 0 for v in VEHICLES}
    for p in doc.parking_entries:
        parking_by_day[p.day_idx] = parking_by_day.get(p.day_idx, 0) + (p.amount or 0)
        parking_by_vehicle[p.vehicle] = parking_by_vehicle.get(p.vehicle, 0) + (p.amount or 0)
    parking_total = sum(parking_by_vehicle.values())
    bike_total = sum((m.amount or 0) for m in bike_rows)
    forklift_total = sum((m.amount or 0) for m in forklift_rows)
    wages_total = sum((w.amount or 0) for w in doc.wages_entries)
    loans_total = sum((l.amount_issued or 0) for l in doc.loan_entries)

    # Category breakdown
    cat_out = {c: 0.0 for c in CATEGORY_CODES}
    cat_in = 0.0
    pc_count = 0
    etr_count = 0
    voucher_count = 0
    for v in doc.vouchers:
        amt = v.amount or 0
        if v.cash_in:
            cat_in += amt
        elif v.category in cat_out:
            cat_out[v.category] += amt
        if v.voucher_no or v.recipient or amt:
            voucher_count += 1
        if v.pc_received:
            pc_count += 1
        if v.etr_received:
            etr_count += 1
    voucher_total_out = sum(cat_out.values())

    context.no_cache = 1
    context.no_breadcrumbs = 1
    context.title = f"Wk {doc.week_no} — Print"
    context.doc = doc
    context.copies = copies
    context.voucher_chunks = voucher_chunks
    context.parking_grid = parking_grid
    context.bike_rows = bike_rows
    context.forklift_rows = forklift_rows
    context.day_names = DAY_NAMES
    context.day_labels = DAY_LABELS
    context.week_dates = week_dates
    context.vehicles = VEHICLES

    context.summary = {
        "cat_out": cat_out,
        "cat_in": cat_in,
        "voucher_total_out": voucher_total_out,
        "voucher_count": voucher_count,
        "pc_count": pc_count,
        "etr_count": etr_count,
        "parking_by_day": parking_by_day,
        "parking_by_vehicle": parking_by_vehicle,
        "parking_total": parking_total,
        "bike_total": bike_total,
        "forklift_total": forklift_total,
        "wages_total": wages_total,
        "loans_total": loans_total,
        "total_out": doc.total_out,
        "expected_close": doc.expected_close,
        "variance": doc.variance,
    }
    return context
