"""The staging row for one week's QuickBooks journal.

It exists because Frappe Cloud cannot push to Intuit. The refresh token lives on
the CommandCentre box behind an fcntl lock, so the app stages a payload here and
the runner there picks it up — the same division the Bill queue already uses.

Approval is against the PAYLOAD HASH, not the row. If the underlying lines change
after somebody approves, the hash moves and the approval is cleared, because what
they agreed to is no longer what would be sent.
"""

import frappe
from frappe.model.document import Document


class QBOPettyCashJournal(Document):
    def before_save(self):
        if self.is_new() or not self.approved:
            return
        prior = frappe.db.get_value(self.doctype, self.name,
                                    ["payload_hash", "approved"], as_dict=True)
        if prior and prior.approved and prior.payload_hash != self.payload_hash:
            self.approved = 0
            self.approved_by = None
            self.approved_at = None
            frappe.msgprint(
                frappe._("The lines changed since this was approved, so the approval "
                         "has been cleared. Look at it again before pushing."),
                indicator="orange", alert=True)
