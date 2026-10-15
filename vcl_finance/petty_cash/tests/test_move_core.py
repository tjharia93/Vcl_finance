"""move_core: which petty cash lines may move between Vouchers and Wages, and how the
fields map. Pure, no Frappe:

    cd ~/projects/Vcl_finance && python3 -m unittest vcl_finance.petty_cash.tests.test_move_core -v
"""
import importlib.util
import pathlib
import unittest
from datetime import date

_spec = importlib.util.spec_from_file_location("move_core", pathlib.Path(__file__).resolve().parents[1] / "move_core.py")
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

V, W = C.VOUCHERS, C.WAGES
D = date(2026, 10, 6)


def voucher(**kw):
    r = {"name": "v1", "txn_date": D, "recipient": "+254725266301 - KENNEDY WAFULA", "category": "OT",
         "amount": 3000, "notes": "Suggested: OT (unknown payee) · Service", "voucher_no": "UJ8KP1",
         "src_ref": "UJ8KP1", "receipt": "/private/files/slip.jpg", "pc_received": 1}
    r.update(kw)
    return r


def wage(**kw):
    r = {"name": "w1", "txn_date": D, "entry_type": "Overtime", "recipient": "254721519206 - JOHN MUCHOKI",
         "staff_id": "S-12", "reason": "Suggested: wage (on the roster) · Labour", "amount": 4753,
         "paye": 1, "src_ref": "UJ6WAGE1"}
    r.update(kw)
    return r


UNAPPROVED = {"status": "Unapproved", "journal_entry": None, "qbo_queue": None}


class Refusal(unittest.TestCase):
    def ok(self, table, row, target, status="Draft", docstatus=0, entry=UNAPPROVED):
        return C.refusal(table, row, target, status, docstatus, entry)

    def test_an_unsigned_line_on_a_draft_week_may_move_both_ways(self):
        self.assertIsNone(self.ok(V, voucher(), W))
        self.assertIsNone(self.ok(W, wage(), V))

    def test_a_line_with_no_entry_yet_may_move(self):
        self.assertIsNone(self.ok(V, voucher(), W, entry=None))

    def test_the_week_must_be_a_draft(self):
        for st in ("Submitted", "Approved", "Closed"):
            self.assertIn(st, self.ok(V, voucher(), W, status=st))
        self.assertIsNotNone(self.ok(V, voucher(), W, docstatus=1))

    def test_a_signed_voided_or_reconciled_line_stays(self):
        self.assertIn("locked", self.ok(V, voucher(locked=1), W))
        self.assertIn("voided", self.ok(W, wage(cancelled=1), V))
        self.assertIn("reconciled", self.ok(V, voucher(reconciled=1), W))

    def test_the_entry_must_still_be_unapproved_and_unposted(self):
        for st in ("Approved", "Withdrawn", "Void"):
            self.assertIn(st, self.ok(V, voucher(), W, entry={"status": st}))
        self.assertIn("JV-1", self.ok(V, voucher(), W, entry=dict(UNAPPROVED, journal_entry="JV-1")))
        self.assertIn("QuickBooks", self.ok(V, voucher(), W, entry=dict(UNAPPROVED, qbo_queue="Q-1")))

    def test_a_top_up_and_the_charges_row_never_become_wages(self):
        self.assertIn("top-up", self.ok(V, voucher(cash_in=1, category=""), W))
        self.assertIn("charges", self.ok(V, voucher(src_ref=C.CHARGES_REF + "\nc-1"), W))

    def test_blank_rows_and_wrong_targets(self):
        self.assertIn("amount", self.ok(V, voucher(amount=0), W))
        self.assertIn("already", self.ok(V, voucher(), V))
        self.assertIsNotNone(self.ok("loan_entries", {"amount": 5}, V))
        self.assertIsNotNone(self.ok(V, voucher(), None))

    def test_target_names(self):
        self.assertEqual([C.normalise_table(x) for x in ("wages", "Wages", "wages_entries", "vouchers", "Voucher")],
                         [W, W, W, V, V])
        self.assertIsNone(C.normalise_table("loan_entries"))


class Mapping(unittest.TestCase):
    def test_voucher_to_wage(self):
        out = C.moved_values(V, voucher(), "tanuj@vimit.com")
        self.assertEqual(out, {
            "txn_date": D, "amount": 3000.0, "recipient": "+254725266301 - KENNEDY WAFULA",
            "receipt": "/private/files/slip.jpg", "src_ref": "UJ8KP1", "entry_type": "Wage",
            "reason": "Moved from Vouchers by tanuj@vimit.com · Suggested: OT (unknown payee) · Service"})

    def test_wage_to_voucher(self):
        out = C.moved_values(W, wage(), "tanuj@vimit.com")
        self.assertEqual((out["category"], out["cash_in"], out["voucher_no"], out["src_ref"], out["amount"]),
                         ("OT", 0, "UJ6WAGE1", "UJ6WAGE1", 4753.0))
        self.assertTrue(out["notes"].startswith("Suggested: OT (moved from Wages by tanuj@vimit.com"))
        for bit in ("Was Overtime", "Staff ID S-12", "PAYE", "Labour"):
            self.assertIn(bit, out["notes"])
        self.assertNotIn("entry_type", out)

    def test_the_wage_reason_fits_its_140_character_field(self):
        out = C.moved_values(V, voucher(notes="x" * 500), "u")
        self.assertEqual(len(out["reason"]), 140)

    def test_a_hand_keyed_line_moves_with_no_src_ref(self):
        out = C.moved_values(W, wage(src_ref=None), "u")
        self.assertEqual((out["src_ref"], out["voucher_no"]), (None, ""))

    def test_remark(self):
        self.assertEqual(C.cancel_remark(W, "tanuj@vimit.com"), "Moved to Wages by tanuj@vimit.com")
        self.assertEqual(C.cancel_remark(V, "a"), "Moved to Vouchers by a")

    def test_blank_scaffolding(self):
        self.assertTrue(C.is_reusable_blank(V, {"amount": 0, "category": "OT"}))
        self.assertTrue(C.is_reusable_blank(W, {"entry_type": "Wage"}))
        self.assertFalse(C.is_reusable_blank(V, {"locked": 1}))
        self.assertFalse(C.is_reusable_blank(V, {"cancelled": 1}))
        self.assertFalse(C.is_reusable_blank(W, {"reason": "x"}))
        self.assertFalse(C.is_reusable_blank(V, {"pc_received": 1}))


if __name__ == "__main__":
    unittest.main()
