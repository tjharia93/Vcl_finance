"""Bootstrap seed data for the Petty Cash module.

Called from hooks.py:after_install — idempotent, safe to re-run.
"""
import frappe


SEED_CATEGORIES = [
    {"code": "TG", "label": "Transport — Goods"},
    {"code": "TE", "label": "Transport — Employee"},
    {"code": "SE", "label": "Spares / Engineering"},
    {"code": "OA", "label": "Office / Admin"},
    {"code": "CM", "label": "Commission"},
    {"code": "OT", "label": "Other"},
]


def after_install():
    seed_categories()
    frappe.db.commit()


def seed_categories():
    for cat in SEED_CATEGORIES:
        if frappe.db.exists("Petty Cash Category", cat["code"]):
            continue
        doc = frappe.new_doc("Petty Cash Category")
        doc.code = cat["code"]
        doc.label = cat["label"]
        doc.insert(ignore_permissions=True)
