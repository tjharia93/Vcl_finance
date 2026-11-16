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
    if doc.docstatus == 0 and not doc.vouchers and not doc.get("native_lines"):
        doc.ensure_grid()
        doc.save(ignore_permissions=True)
        doc = frappe.get_doc("Petty Cash Sheet", name)

    # A native week keeps no rows: rebuild them in memory from its entries so the page
    # shows the week exactly as it shows any other. (Read-only here — its lines are
    # changed through the entry page, not this grid.)
    from vcl_finance.petty_cash.api import _native
    _native(doc)

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
    context.native = bool(doc.get("native_lines"))
    if context.native:
        _native_context(context, doc)
    return context


def _native_context(context, doc):
    """A native week is shown read-only here: every line with its receipt and status,
    the totals, and links to the entry page where lines are actually managed. The cash
    count and the week's own workflow stay available, to whoever may use them."""
    from vcl_finance.petty_cash.api import _feed_items, _is_accounts_manager

    state = {e.name: e for e in frappe.get_all(
        "Petty Cash Entry", filters={"week_sheet": doc.name},
        fields=["name", "status", "reconciled", "recipient", "notes", "voucher_no"], limit_page_length=0)}
    lines = []
    for it in _feed_items(doc):
        e = state.get(it["id"]) or {}
        if it.get("cancelled"):
            status = "Cancelled" + (f": {it['cancel_remark']}" if it.get("cancel_remark") else "")
        elif e.get("reconciled"):
            status = "Signed and ticked"
        elif e.get("status") == "Approved":
            status = "Signed"
        elif e.get("status") == "Withdrawn":
            status = "Sign-off withdrawn"
        else:
            status = "Not signed"
        lines.append({
            "id": it["id"], "date": it.get("date"), "label": it.get("label"),
            "voucher_no": it.get("voucher_no") or e.get("voucher_no") or "",
            "detail": it.get("recipient") or it.get("reason") or it.get("notes") or e.get("notes") or "",
            "amount": it.get("amount") or 0, "direction": it.get("direction"),
            "receipt": it.get("receipt"), "cancelled": bool(it.get("cancelled")), "status": status,
        })
    lines.sort(key=lambda l: (l["date"] or "", str(l["id"])))
    context.lines = lines
    from vcl_finance.petty_cash import native
    context.cash = native.cash_position(doc.name)
    # Names for display, looked up here: the page itself may not call into frappe.utils.
    name_of = lambda user: frappe.utils.get_fullname(user) if user else ""
    context.cash["acknowledged_name"] = name_of(context.cash.get("acknowledged_by"))
    if context.cash.get("baseline"):
        context.cash["baseline"]["acknowledged_name"] = name_of(context.cash["baseline"].get("acknowledged_by"))
    prev = native._previous_native_week(doc.name)
    # The first week kept as entries is where the opening count is recorded by hand.
    context.opening_is_carried = bool(prev and prev.native_lines and prev.status in ("Closed", "Approved"))
    # Two dates for a supplied balance. What it is OF: the opening of a Monday (this week's
    # Monday for the opening balance, the following Monday for the closing balance). And
    # when it was received and reviewed: today by default, never before the balance date.
    week_end = frappe.utils.getdate(doc.week_ending)
    context.opening_monday = str(frappe.utils.add_days(week_end, -5))
    context.closing_monday = str(frappe.utils.add_days(week_end, 2))
    context.review_default = str(max(frappe.utils.getdate(frappe.utils.today()), frappe.utils.add_days(week_end, 2)))
    context.opening_review_default = str(max(frappe.utils.getdate(frappe.utils.today()), frappe.utils.add_days(week_end, -5)))
    context.revision = int(doc.get("lines_revision") or 0)
    context.can_manage = _is_accounts_manager()
    # Lines can still be opened on the entry page while the week is not frozen.
    context.can_open = doc.docstatus == 0 and doc.status not in ("Approved", "Submitted")
