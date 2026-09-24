import frappe
from frappe.model.document import Document


class AuditedFSLine(Document):
    """One line of the signed financial statements, with its audited amount per
    year-end. The report reads the amount for the as-at date it is run for; a date
    with no row shows no audited figure rather than a zero."""

    def validate(self):
        seen = set()
        for row in self.amounts:
            if row.as_at in seen:
                frappe.throw(f"Two audited amounts for {row.as_at} on {self.code}.")
            seen.add(row.as_at)
