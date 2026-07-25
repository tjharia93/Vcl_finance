from frappe.model.document import Document


class DailyCashPositionLine(Document):
    """One account's balances for a day, plus a flag per source saying whether
    that source actually had a figure. Currency fields cannot hold NULL, so
    without the *_present flags an account with no statement is indistinguishable
    from one holding exactly nothing."""
    pass
