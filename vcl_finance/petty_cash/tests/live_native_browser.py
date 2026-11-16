"""Set-up and tear-down for the browser check of the entry page on a native week.

Commits (a browser is a separate session): one native Cash sheet for the week that
contains today, and one custodian user. ``teardown`` removes both.
"""
import json

import frappe
from frappe.utils import getdate, today

USER, PWD = "pc-browser-custodian@test.invalid", "Dev-only-Custodian-2026"


def _week():
    from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import week_saturday
    return week_saturday(getdate(today()))


def setup():
    frappe.set_user("Administrator")
    if not frappe.db.exists("User", USER):
        u = frappe.new_doc("User")
        u.update({"email": USER, "first_name": "Browser Custodian", "send_welcome_email": 0, "enabled": 1,
                  "new_password": PWD})
        u.append("roles", {"role": "Petty Cash User"})
        u.insert(ignore_permissions=True)
    week = _week()
    name = frappe.db.get_value("Petty Cash Sheet", {"week_ending": week, "float": "Cash"}, "name")
    if not name:
        s = frappe.new_doc("Petty Cash Sheet")
        s.update({"week_ending": week, "float": "Cash", "custodian_name": "Shiro", "status": "Draft",
                  "opening_balance": 10000, "native_lines": 1})
        s.insert()
        name = s.name
        # No opening count is recorded here: the browser check does that through the page.
    frappe.db.commit()
    print("RESULT " + json.dumps({"sheet": name, "week": str(week), "user": USER, "pwd": PWD,
                                  "native": frappe.db.get_value("Petty Cash Sheet", name, "native_lines")}))


def state(sheet):
    frappe.set_user("Administrator")
    rows = frappe.get_all("Petty Cash Entry", {"week_sheet": sheet},
                          ["name", "amount", "category", "recipient", "voucher_no", "status", "cancelled", "receipt",
                           "sync_state", "owner", "pc_received", "txn_date"], order_by="creation")
    h = frappe.db.get_value("Petty Cash Sheet", sheet, ["total_out", "expected_close", "lines_revision"], as_dict=True)
    child = sum(frappe.db.count(dt, {"parent": sheet}) for dt in
                ("Petty Cash Voucher", "Petty Cash Wages Entry", "Petty Cash Loan Entry", "Petty Cash Misc Entry", "Petty Cash Parking Entry"))
    print("RESULT " + json.dumps({"entries": rows, "sheet": h, "rows_on_sheet": child}, default=str))


def teardown():
    frappe.set_user("Administrator")
    week = _week()
    name = frappe.db.get_value("Petty Cash Sheet", {"week_ending": week, "float": "Cash", "native_lines": 1}, "name")
    if name:
        for e in frappe.get_all("Petty Cash Entry", {"week_sheet": name}, pluck="name"):
            frappe.db.delete("Comment", {"reference_doctype": "Petty Cash Entry", "reference_name": e})
            frappe.db.delete("Version", {"ref_doctype": "Petty Cash Entry", "docname": e})
            frappe.db.delete("File", {"attached_to_doctype": "Petty Cash Entry", "attached_to_name": e})
            frappe.db.delete("Petty Cash Entry", {"name": e})
        frappe.db.delete("File", {"attached_to_doctype": "Petty Cash Sheet", "attached_to_name": name})
        frappe.db.delete("Comment", {"reference_doctype": "Petty Cash Sheet", "reference_name": name})
        frappe.db.delete("Version", {"ref_doctype": "Petty Cash Sheet", "docname": name})
        frappe.db.delete("Petty Cash Sheet", {"name": name})
    # a following week the browser check may have created to hold the days before the count
    for extra in frappe.get_all("Petty Cash Sheet", {"custodian_name": "Browser test"}, pluck="name"):
        for e in frappe.get_all("Petty Cash Entry", {"origin_sheet": extra}, pluck="name"):
            frappe.db.delete("Comment", {"reference_name": e}); frappe.db.delete("Version", {"docname": e}); frappe.db.delete("Petty Cash Entry", {"name": e})
        for dt in ("Petty Cash Voucher", "Petty Cash Wages Entry", "Petty Cash Loan Entry", "Petty Cash Misc Entry", "Petty Cash Parking Entry"):
            frappe.db.delete(dt, {"parent": extra})
        frappe.db.delete("Version", {"docname": extra}); frappe.db.delete("Petty Cash Sheet", {"name": extra})
    if frappe.db.exists("User", USER):
        frappe.delete_doc("User", USER, force=True, ignore_permissions=True)
    frappe.db.commit()
    print("RESULT " + json.dumps({"left": frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")}) + frappe.db.count("User", {"name": USER}),
                                  "sheets": frappe.db.count("Petty Cash Sheet"), "entries": frappe.db.count("Petty Cash Entry")}))
