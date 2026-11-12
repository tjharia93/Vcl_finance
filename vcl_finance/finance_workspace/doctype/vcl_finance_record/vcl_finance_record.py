from frappe.model.document import Document
from vcl_finance.workspace.guards import record_validate, record_delete

class VCLFinanceRecord(Document):
    def validate(self):
        record_validate(self)

    def on_trash(self):
        record_delete(self)
