import frappe
from frappe.model.document import Document


class AROpenEntry(Document):
    """An open, allocatable AR item — a Debit (invoice / debit opening / debit journal)
    OR a Credit (payment / credit note / credit opening / credit journal). Both sides are
    allocatable (symmetric). Synced from vcl_data ar_open_items / unapplied credits."""
    pass
