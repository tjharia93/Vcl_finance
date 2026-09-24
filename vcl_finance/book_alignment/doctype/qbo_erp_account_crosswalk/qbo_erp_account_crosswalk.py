import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class QBOERPAccountCrosswalk(Document):
    """QBO account -> ERPNext account and audited line. The map's permanent home.

    Tanuj owns the decision. Whoever sets or changes it is stamped as the decider,
    so the record says who chose, not just what was chosen.
    """

    def validate(self):
        if self.decision == "Map" and not self.erp_account:
            frappe.throw("A Map decision needs the ERPNext account it maps to.")
        if self.has_value_changed("decision") or self.has_value_changed("erp_account"):
            if self.decision:
                self.decided_by = frappe.session.user
                self.decided_at = now_datetime()
            else:
                self.decided_by = None
                self.decided_at = None
