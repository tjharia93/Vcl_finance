"""New-sheet form — `/petty-cash/new`."""
from datetime import date, timedelta

import frappe
from frappe import _


def _next_friday(today=None):
    today = today or date.today()
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0:
        return today
    return today + timedelta(days=days_until_friday)


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to create a petty cash sheet."), frappe.PermissionError)

    context.no_cache = 1
    context.title = "New Petty Cash Sheet"
    context.suggested_date = _next_friday().isoformat()

    # POST handling — Frappe's web framework strips form posts into form_dict.
    if frappe.request.method == "POST":
        week_ending = frappe.form_dict.get("week_ending")
        custodian_name = frappe.form_dict.get("custodian_name") or "Shiro"
        opening_balance = float(frappe.form_dict.get("opening_balance") or 0)
        authorised_float = float(frappe.form_dict.get("authorised_float") or 50000)

        if not week_ending:
            frappe.throw(_("Week Ending is required."))

        # Re-use the same helper the API exposes
        from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import create_for_week
        name = create_for_week(week_ending, custodian_name, opening_balance, authorised_float)
        frappe.local.flags.redirect_location = f"/petty-cash/sheet?name={name}"
        raise frappe.Redirect

    return context
