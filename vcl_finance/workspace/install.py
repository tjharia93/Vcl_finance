"""Idempotent release seed. Creates roles, never assigns them to people."""
import frappe


def ensure_roles():
    roles = ["VCL Financial Controller", "VCL Financial Planner", "VCL Finance Operator"]
    for role in roles:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def execute():
    ensure_roles()
    roles = ["VCL Financial Controller", "VCL Financial Planner", "VCL Finance Operator"]
    # Explicit read-only reporting permissions; company User Permissions still apply.
    from frappe.permissions import add_permission
    for role in roles[:2]:
        for dt in ["Company", "Fiscal Year", "Account", "Cost Center", "GL Entry", "Item Group"] + (["Journal Entry"] if role == roles[0] else []):
            if not frappe.db.exists("Custom DocPerm", {"parent": dt, "role": role, "permlevel": 0}):
                add_permission(dt, role, 0)
            perm = frappe.db.get_value("Custom DocPerm", {"parent": dt, "role": role, "permlevel": 0}, "name")
            if perm:
                frappe.db.set_value("Custom DocPerm", perm, {"read": 1, "select": 1, "report": 1, "create": 0, "write": 0, "submit": 0, "cancel": 0, "delete": 0})
    for dt in ["Company", "Fiscal Year", "Cost Center"]:
        if not frappe.db.exists("Custom DocPerm", {"parent": dt, "role": roles[2], "permlevel": 0}):
            add_permission(dt, roles[2], 0, "read")
    s = frappe.get_single("VCL Finance Workspace Settings")
    if not s.final_approver:
        s.final_approver = "tanuj.haria@vimit.com"
        s.phase_1 = 1
        s.phase_2 = s.phase_3 = s.phase_4 = 0
        s.journal_review_required = s.sync_respects_locks = 0
        s.reporting_gaps_json = frappe.as_json(["Costs and cost-centre tagging require FC validation. ERPNext management actuals remain provisional."])
        from vcl_finance.workspace.guards import allow_release_write
        with allow_release_write():
            s.save(ignore_permissions=True)
    frappe.clear_cache()
