import frappe
from frappe.model.document import Document


class DailyCashPosition(Document):
    """One day's cash & debt position: every bank, cash and loan account against
    its bank statement, QuickBooks and ERPNext.

    Written by the recon job (bank_rec/daily_position.py), which is the only
    thing that has the statement parsers and the QBO tokens. Nothing here
    recomputes a balance — this doctype is a record of what was read, so the
    Compass card and a later question about "what did we think on the 25th"
    return the same numbers.
    """

    def validate(self):
        # The tie-out only spans accounts with BOTH a statement and a QBO
        # balance, so the variance must agree with the lines it came from. A
        # mismatch means the payload was assembled wrong and the whole position
        # is untrustworthy — better to refuse it than to publish a number that
        # does not add up.
        covered = [l for l in self.lines if l.bank_present and l.qbo_present]
        if not covered:
            return
        bank = sum(l.bank_balance or 0 for l in covered)
        qbo = sum(l.qbo_balance or 0 for l in covered)
        if abs((self.covered_variance or 0) - (bank - qbo)) > 1:
            frappe.throw(
                f"Variance {self.covered_variance:,.2f} does not match the covered "
                f"lines ({bank - qbo:,.2f}). Refusing to record an incoherent position."
            )
