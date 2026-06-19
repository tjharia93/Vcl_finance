"""Bootstrap seed data for the Petty Cash module.

Called from hooks.py:after_install — idempotent, safe to re-run.
"""
import frappe


SEED_CATEGORIES = [
    {"code": "TG", "label": "Transport — Goods"},
    {"code": "TE", "label": "Transport — Employee"},
    {"code": "SE", "label": "Spares / Engineering"},
    {"code": "OA", "label": "Office / Admin"},
    {"code": "FD", "label": "Food"},
    {"code": "GP", "label": "Geeprint"},
    {"code": "OT", "label": "Other"},
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
        if frappe.db.exists("Petty Cash Category", cat["code"]):
            continue
        doc = frappe.new_doc("Petty Cash Category")
        doc.code = cat["code"]
        doc.label = cat["label"]
        doc.insert(ignore_permissions=True)
