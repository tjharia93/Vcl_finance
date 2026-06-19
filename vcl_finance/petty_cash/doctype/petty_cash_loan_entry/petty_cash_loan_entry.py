from frappe.model.document import Document


class PettyCashLoanEntry(Document):
    """Child of Petty Cash Sheet — one staff-loan entry.

    `amount_issued` is the cash that actually leaves the float (feeds total_out);
    `amount_signed` is the documentary amount the recipient signed for.
    """
    pass
