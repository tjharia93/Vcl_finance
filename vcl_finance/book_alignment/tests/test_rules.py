"""The report's arithmetic against the figures Tanuj has already reviewed.

Plain unittest, no bench: rules.py imports nothing from frappe. Run from the
repo root:  python3 -m unittest vcl_finance.book_alignment.tests.test_rules

Fixtures are live pulls at 31-12-2025, taken 24-Sep-2026: the QBO TrialBalance
(146 rows, with each account's QBO id, name, class and type), the ERPNext GL
balances on the Trial Balance basis, and every QBO Account. The expected figures
are the Excel version's, which recalculated correctly in Excel.
"""
import json
import os
import unittest

from vcl_finance.book_alignment import rules

HERE = os.path.dirname(__file__)


def load(name):
    with open(os.path.join(HERE, name)) as f:
        return json.load(f)


class TestAgainstFY2025(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.natures = {c: n for c, _, _, _, n, _ in rules.FS_LINES_FY2025}
        cls.audited = {c: a for c, _, _, a, _, _ in rules.FS_LINES_FY2025}
        tb = load("fixture_qbo_tb_20251231.json")
        cls.qbo = {i: bal for i, _, _, _, bal in tb}
        # the crosswalk as the seed patch builds it: rule line + workbook proposal
        with open(os.path.join(HERE, "..", "seed", "crosswalk_proposals_2026-09-24.json")) as f:
            proposals = json.load(f)
        cls.crosswalk = [
            {"qbo_account": i, "fs_line": rules.qbo_line(n, c, t), "decision": "",
             "erp_account": None, "proposed_erp_account": proposals.get(i)}
            for i, n, c, t in load("fixture_qbo_accounts.json")
        ]
        cls.erp = load("fixture_erp_gl_20251231.json")

    def qbo_totals(self):
        line_of = {r["qbo_account"]: r["fs_line"] for r in self.crosswalk}
        return rules.line_totals(self.qbo, line_of, self.natures)

    def erp_totals(self, prefer=rules.PREFER_SEED, crosswalk=None):
        m = rules.erp_line_map(self.crosswalk if crosswalk is None else crosswalk, prefer=prefer)
        return rules.line_totals(self.erp, {a: v[0] for a, v in m.items()}, self.natures)

    def test_trial_balances(self):
        self.assertEqual(len(self.qbo), 146)
        self.assertAlmostEqual(sum(self.qbo.values()), 0, places=2)
        self.assertAlmostEqual(sum(self.erp.values()), 136428620.53, places=2)

    def test_lines_that_tie(self):
        q, e = self.qbo_totals(), self.erp_totals()
        for code, amount in (("TR", 151706399), ("OD", 66802115)):
            self.assertEqual(round(self.audited[code]), amount)
            self.assertEqual(round(q[code]), amount)
            self.assertEqual(round(e[code]), amount)

    def test_inventories_and_revenue(self):
        q, e = self.qbo_totals(), self.erp_totals()
        self.assertEqual(round(q["INV"]), 35457099)
        self.assertEqual(round(e["INV"]), 360361817)
        self.assertEqual(round(q["REV"]), 495524911)
        self.assertEqual(round(e["REV"]), 493496846)

    def test_profit_before_tax(self):
        self.assertEqual(round(rules.pbt(self.audited)), 19957071)
        self.assertEqual(round(rules.pbt(self.qbo_totals())), -34374938)

    def test_nothing_drops_out(self):
        # every balance lands on some line, so the natural-side totals unwind to the TB
        for totals, source in ((self.qbo_totals(), self.qbo), (self.erp_totals(), self.erp)):
            unwound = sum(rules.natural(self.natures[c]) * v for c, v in totals.items())
            self.assertAlmostEqual(unwound, sum(source.values()), places=2)

    def test_proposals_first_moves_stock_adjustment(self):
        # why the seed goes first by default: see rules.erp_line_map
        m = rules.erp_line_map(self.crosswalk, prefer=rules.PREFER_PROPOSED)
        self.assertEqual(m["5119 - Stock Adjustment - VCL"][:2], ("INV", "Proposed"))
        self.assertIn("conflict", m["1110 - Cash - VCL"][1])

    def test_decision_beats_everything(self):
        cw = [dict(r) for r in self.crosswalk]
        cw.append({"qbo_account": "zz", "fs_line": "OPEX", "decision": "Map",
                   "erp_account": "4110 - Sales - VCL", "proposed_erp_account": None})
        for prefer in (rules.PREFER_SEED, rules.PREFER_PROPOSED):
            m = rules.erp_line_map(cw, prefer=prefer)
            self.assertEqual(m["4110 - Sales - VCL"], ("OPEX", "Decided", ["zz"]))

    def test_ignore_and_create_carry_no_mapping(self):
        cw = [{"qbo_account": "1", "fs_line": "OPEX", "decision": d, "erp_account": None,
               "proposed_erp_account": "X"} for d in ("Ignore", "Create")]
        self.assertNotIn("X", rules.erp_line_map(cw, seed={}))
        cw[0]["decision"] = "Question"
        self.assertEqual(rules.erp_line_map(cw, seed={})["X"][1], "Proposed")


if __name__ == "__main__":
    unittest.main()
