"""Bootstrap seed data for the Petty Cash module.

Called from hooks.py:after_install — idempotent, safe to re-run.
"""
import frappe


# Best-guess ERP COA accounts per voucher category (Phase 2 posting). These mirror
# posting.VOUCHER_CAT_FALLBACK. They are only applied when gl_account is blank and
# the account actually exists on the site — an unknown/absent account is left
# blank so the JE preview flags the line "NEEDS ERP a/c" rather than mis-posting.
SEED_CATEGORIES = [
    {"code": "TG", "label": "Transport — Goods",       "gl_account": "5214.1 - Customer Deliveries - Transporter - VCL"},
    {"code": "TE", "label": "Transport — Employee",    "gl_account": "5216 - Travel Expenses - VCL"},
    {"code": "SE", "label": "Spares / Engineering",    "gl_account": "5240.4 - Vehicle Maintenance - VCL"},
    {"code": "OA", "label": "Office / Admin",          "gl_account": "5206 - Legal Expenses - VCL"},
    {"code": "FD", "label": "Food",                    "gl_account": "5208.1 - Staff Welfare: Tea, Milk, Water, Snacks - VCL"},
    {"code": "GP", "label": "Geeprint",                "gl_account": "5211 - Print and Stationery - VCL"},
    {"code": "OT", "label": "Other",                   "gl_account": "5240.2 - Parking - Deliveries - VCL"},
]


def after_install():
    seed_categories()
    ensure_petty_cash_role()
    frappe.db.commit()


def ensure_petty_cash_role():
    """Create the restricted 'Petty Cash User' role (Phase 5 login). Idempotent."""
    if frappe.db.exists("Role", "Petty Cash User"):
        return
    doc = frappe.new_doc("Role")
    doc.role_name = "Petty Cash User"
    doc.desk_access = 1
    doc.insert(ignore_permissions=True)


def seed_categories():
    for cat in SEED_CATEGORIES:
        gl = cat.get("gl_account")
        # Only assign the account if it actually exists on this site; otherwise
        # leave blank so the JE preview flags it rather than mis-posting.
        if gl and not frappe.db.exists("Account", gl):
            gl = None
        if frappe.db.exists("Petty Cash Category", cat["code"]):
            # Back-fill a missing gl_account on an existing category (non-destructive).
            if gl and not frappe.db.get_value("Petty Cash Category", cat["code"], "gl_account"):
                frappe.db.set_value("Petty Cash Category", cat["code"], "gl_account", gl)
            continue
        doc = frappe.new_doc("Petty Cash Category")
        doc.code = cat["code"]
        doc.label = cat["label"]
        if gl:
            doc.gl_account = gl
        doc.insert(ignore_permissions=True)
