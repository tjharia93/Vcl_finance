import frappe
from frappe.model.document import Document


class ARCustomerRecon(Document):
    """Per-customer AR reconciliation summary (authoritative True Open from the lake's
    Balance column; QBO stated + variance + four continuity joints). Synced from vcl_data."""

    def before_save(self):
        self.variance = (self.qbo_stated or 0) - (self.true_open or 0)
