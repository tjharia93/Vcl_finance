from frappe.model.document import Document
import frappe
from vcl_finance.workspace.guards import release_write_allowed

class VCLFinanceWorkspaceSettings(Document):
    def validate(self):
        if not release_write_allowed():
            frappe.throw("Use the Finance release action with final authorisation and acceptance evidence.", frappe.PermissionError)
