import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class ARAllocation(Document):
    """A single allocation linking a Credit item to a Debit item (symmetric). The work
    product of AR reconciliation. System of record in ERPNext; two-way synced with the
    vcl_data lake (ar_allocation_lines) keyed by sync_key = canonical|alloc_id.

    The lake-side puller reads AR Allocation rows whose `modified` is newer than the last
    sync to pull manual/ERP edits back into vcl_data; `origin` marks where a row was last
    touched so the sync can apply last-write-wins.
    """

    def before_save(self):
        # Stamp the edit time so the lake puller can detect ERP-side changes.
        self.updated_at = now_datetime()
        if not self.origin:
            self.origin = "lake"
        # A user edit in ERPNext (not a sync write) is a manual review.
        if self.flags.get("from_lake_sync"):
            return
        if self.has_value_changed("amount") or self.has_value_changed("debit_ref") or self.has_value_changed("credit_ref"):
            self.origin = "manual"
