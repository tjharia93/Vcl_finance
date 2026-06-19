"""Sheet list — `/petty-cash/`."""
import frappe
from frappe import _


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to view petty cash sheets."), frappe.PermissionError)

    context.no_cache = 1
    context.title = "VCL Petty Cash"
    context.sheets = frappe.get_all(
        "Petty Cash Sheet",
        fields=[
            "name", "week_no", "week_ending", "float", "custodian_name",
            "opening_balance", "total_out", "total_in",
            "expected_close", "variance", "status", "docstatus",
        ],
        order_by="week_ending desc",
        limit=200,
    )
    return context
