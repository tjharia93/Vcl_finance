import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class ARControlLine(Document):
    """One customer, one month: QBO roll-forward beside the ERPNext roll-forward.

    Everything except the review fields is written by the on-prem sync
    (erp_qbo_align/ar_control/sync.py) and overwritten on every run. The review
    status and note are Finance's own and the sync never touches them.
    """

    def before_save(self):
        old = self.get_doc_before_save()
        if old and (old.review_status != self.review_status or (old.review_note or "") != (self.review_note or "")):
            self.reviewed_by = frappe.session.user
            self.reviewed_on = now_datetime()
