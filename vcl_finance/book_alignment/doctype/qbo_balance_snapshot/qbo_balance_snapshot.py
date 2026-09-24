from frappe.model.document import Document
from frappe.utils import getdate


class QBOBalanceSnapshot(Document):
    """One QBO account's trial-balance figure at one date, Dr + / Cr -.

    Written only by the on-prem pull (CommandCentre/tools/pull_qbo_tb_snapshot.py):
    Frappe Cloud cannot reach QBO. The name is the date plus the account, so a
    re-pull for the same date updates rather than duplicates.
    """

    def autoname(self):
        self.name = f"{getdate(self.as_at).isoformat()}-{self.qbo_account}"
