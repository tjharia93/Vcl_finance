"""Explicit, audited mappings for future invoices only; no historical backfill."""
import json
import frappe


def apply_new_invoice_dimensions(doc, method=None):
    if not doc.is_new() or not frappe.db.exists("DocType", "VCL Finance Workspace Settings"):
        return
    settings = frappe.get_single("VCL Finance Workspace Settings")
    if not settings.phase_4:
        return
    rules = json.loads(settings.get("dimension_rules_json") or "[]")
    for line in doc.get("items", []):
        # Preserve deliberately assigned dimensions. A configured rule can replace
        # an inherited Main default, never an existing company's real allocation.
        if line.get("cost_center") and not line.cost_center.startswith("Main -"):
            continue
        group = frappe.get_cached_value("Item", line.item_code, "item_group") if line.get("item_code") else None
        for rule in rules:
            if rule["company"] != doc.company or rule["doctype"] != doc.doctype:
                continue
            value = group if rule["match_field"] == "item_group" else line.get(rule["match_field"])
            if value == rule["match_value"]:
                line.cost_center = rule["cost_center"]
                break


def validate_rules(rules):
    keys = set()
    for rule in rules:
        if rule.get("doctype") not in {"Sales Invoice", "Purchase Invoice"} or rule.get("match_field") not in {"item_code", "item_group", "income_account", "expense_account"}:
            frappe.throw("Choose an invoice type and an item, item group or account mapping.")
        key = (rule["company"], rule["doctype"], rule["match_field"], rule.get("match_value"))
        if not key[-1] or key in keys:
            frappe.throw("Dimension rules must have unique, non-empty matching values.")
        keys.add(key)
        cc = frappe.get_doc("Cost Center", rule.get("cost_center"))
        cc.check_permission("read")
        if cc.company != rule["company"] or cc.is_group or cc.disabled:
            frappe.throw("Choose an enabled company ledger cost centre.")
    return rules
