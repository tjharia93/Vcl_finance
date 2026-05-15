import frappe
from frappe.model.document import Document


class PettyCashCategory(Document):
    """Master list of petty-cash spending categories used by the voucher register."""

    def validate(self):
        if self.code:
            self.code = self.code.upper().strip()
