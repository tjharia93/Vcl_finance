import unittest
from decimal import Decimal
from vcl_finance.workspace import core


class FinancePolicies(unittest.TestCase):
    def test_fractional_base_values_round_after_aggregation(self):
        rows = [{"base_grand_total": "0.004", "grand_total": "0.004"}] * 3
        self.assertEqual(core.summarize_drafts(rows, "pi", "KES")["directions"]["Drafts"]["base_amount"], 0.01)

    def test_cash_rolls_opening_balance_and_propagates_unknowns(self):
        p = {"periods": core.periods("2026-11-12", 13, True), "lines": [
            {"category": "Opening cash", "values": [1000] + [None] * 12},
            {"category": "Collection", "values": [200] * 13},
            {"category": "Payment", "values": [150] * 13},
            {"category": "Financing", "values": [0] * 13}]}
        rows = core.cash_outlook(p)
        self.assertEqual(rows[1]["opening"], 1050)
        self.assertEqual(rows[-1]["closing"], 1650)
        p["lines"][2]["values"][1] = None
        rows = core.cash_outlook(p)
        self.assertIsNone(rows[1]["closing"])
        self.assertIsNone(rows[-1]["closing"])

    def test_missing_cash_payment_lines_do_not_mean_zero(self):
        p = {"periods": core.periods("2026-11-12", 13, True), "lines": [
            {"category": "Opening cash", "values": [1000] * 13},
            {"category": "Collection", "values": [200] * 13}]}
        self.assertIsNone(core.cash_outlook(p)[0]["payments"])

    def test_mixed_currency_uses_base_values(self):
        s = core.summarize_drafts([{"currency": "KES", "grand_total": 100, "base_grand_total": 100},
                                  {"currency": "USD", "grand_total": 10, "base_grand_total": 1300}], "pi", "KES")
        self.assertEqual(s["directions"]["Drafts"]["base_amount"], 1400)
        self.assertEqual(len(s["transaction_totals"]), 2)

    def test_missing_base_is_not_transaction_fallback(self):
        s = core.summarize_drafts([{"currency": "USD", "grand_total": 10}], "pi", "KES")
        self.assertIsNone(s["directions"]["Drafts"]["base_amount"])

    def test_payment_directions_are_not_netted(self):
        rows = [{"payment_type": "Receive", "base_received_amount": 150, "received_amount": 150},
                {"payment_type": "Pay", "base_paid_amount": 100, "paid_amount": 100},
                {"payment_type": "Internal Transfer", "base_paid_amount": 80, "paid_amount": 80}]
        self.assertEqual(set(core.summarize_drafts(rows, "pe", "KES")["directions"]), {"Receive", "Pay", "Internal Transfer"})

    def test_large_population_has_no_summary_cap(self):
        rows = [{"currency": "KES", "grand_total": 1, "base_grand_total": 1} for _ in range(1201)]
        self.assertEqual(core.summarize_drafts(rows, "pi", "KES")["directions"]["Drafts"]["base_amount"], 1201)

    def test_amount_cleanup_and_nonfinite(self):
        self.assertEqual(core.money(' 1,234.565 '), Decimal('1234.57'))
        for value in ['NaN', 'Infinity', '-Infinity', 'not money']:
            with self.assertRaises(ValueError):
                core.money(value)

    def test_month_and_week_rollovers(self):
        self.assertEqual(core.periods('2026-12-12', 2)[1], {"start": '2027-01-01', "end": '2027-01-31'})
        self.assertEqual(core.periods('2026-11-12', 13, True)[0]['start'], '2026-11-09')
        self.assertEqual(len(core.periods('2026-11-12', 13, True)), 13)
        self.assertEqual(core.periods('2028-02-01', 1)[0]['end'], '2028-02-29')

    def test_maker_cannot_review(self):
        with self.assertRaises(PermissionError):
            core.transition('In review', 'approve', 'maker', 'maker', fc=True)
        with self.assertRaises(PermissionError):
            core.transition('In review', 'approve', 'other', 'maker', fc=False)
        self.assertEqual(core.transition('In review', 'approve', 'reviewer', 'maker', fc=True), 'Approved')

    def test_final_approval_required_for_publication(self):
        with self.assertRaises(PermissionError):
            core.transition('Approved', 'publish', 'reviewer', 'maker', fc=True, final_required=True)
        self.assertEqual(core.transition('Approved', 'publish', 'final', 'maker', final=True, final_required=True), 'Published')

    def test_illegal_transition(self):
        with self.assertRaises(ValueError):
            core.transition('Draft', 'publish', 'final', 'maker', final=True)

    def test_document_fingerprint_ignores_framework_metadata_only(self):
        a = {'accounts': [{'account': 'Cash', 'debit': 10}], 'modified': 'yesterday', 'docstatus': 0}
        b = {**a, 'modified': 'today', 'docstatus': 1}
        self.assertEqual(core.fingerprint(a), core.fingerprint(b))
        b['accounts'] = [{'account': 'Cash', 'debit': 11}]
        self.assertNotEqual(core.fingerprint(a), core.fingerprint(b))

    def test_plan_preserves_nulls_and_requires_horizon(self):
        p = {'plan_type': 'Rolling forecast', 'scenario': 'Base', 'periods': core.periods('2026-11-01', 12),
             'assumptions': [{'owner': 'planner', 'description': 'Reviewed inputs'}],
             'lines': [{'account': 'Sales', 'category': 'Revenue', 'values': [None] * 12}]}
        self.assertIsNone(core.validate_plan(p)['lines'][0]['values'][0])
        p['periods'] = p['periods'][:3]
        with self.assertRaises(ValueError):
            core.validate_plan(p)

    def test_duplicate_planning_grain_rejected(self):
        line = {'account': 'Sales', 'category': 'Revenue', 'values': [1] * 12}
        p = {'plan_type': 'Annual budget', 'scenario': 'Base', 'periods': core.periods('2026-01-01', 12),
             'assumptions': ['test'], 'lines': [line, dict(line)]}
        with self.assertRaises(ValueError):
            core.validate_plan(p)

    def test_close_due_dates_are_working_days(self):
        tasks = core.close_tasks('2026-10-31')
        self.assertEqual(tasks[0]['due_date'], '2026-11-02')
        self.assertEqual(tasks[-1]['due_date'], '2026-11-09')


if __name__ == '__main__':
    unittest.main()
