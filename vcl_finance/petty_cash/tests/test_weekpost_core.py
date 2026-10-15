"""Unit tests for the pure VAT and posting logic. No bench needed:

    cd ~/projects/Vcl_finance && python3 -m unittest vcl_finance.petty_cash.tests.test_weekpost_core -v
"""
import importlib.util
import pathlib
import unittest

_p = pathlib.Path(__file__).resolve().parents[1] / "weekpost_core.py"
_spec = importlib.util.spec_from_file_location("weekpost_core", _p)
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)


def entry(name, amount, source_type="Voucher Category", **kw):
    return dict(name=name, amount=amount, source_type=source_type, receipt="x.png", **kw)


class SplitVat(unittest.TestCase):
    def test_standard_16(self):
        self.assertEqual(C.split_vat(1160, "Standard 16%"), (1000.0, 160.0))

    def test_petrol_8(self):
        self.assertEqual(C.split_vat(1080, "Petrol 8%"), (1000.0, 80.0))

    def test_zero_rated_treatments(self):
        for t in ("Exempt", "No VAT", "Not applicable"):
            self.assertEqual(C.split_vat(500, t), (500.0, 0.0))

    def test_net_plus_vat_is_always_gross(self):
        for g in (0.01, 1, 99.99, 1234.56, 2800, 6600, 987654.32):
            for t in ("Standard 16%", "Petrol 8%"):
                n, v = C.split_vat(g, t)
                self.assertAlmostEqual(n + v, g, places=2)

    def test_override_is_taken_when_rated(self):
        self.assertEqual(C.split_vat(1000, "Standard 16%", 137.5), (862.5, 137.5))

    def test_override_refused_when_unrated(self):
        with self.assertRaises(C.VatError):
            C.split_vat(1000, "Exempt", 10)

    def test_override_cannot_exceed_gross(self):
        with self.assertRaises(C.VatError):
            C.split_vat(100, "Standard 16%", 101)

    def test_unknown_treatment(self):
        with self.assertRaises(C.VatError):
            C.split_vat(100, "Zero rated")


class Effective(unittest.TestCase):
    def test_blank_is_not_reviewed(self):
        t, net, vat, problem = C.effective_vat(entry("a", 100))
        self.assertIsNone(t)
        self.assertEqual(problem, "VAT not reviewed")

    def test_wages_and_loan_need_nothing(self):
        for st in ("Wages Entry", "Loan"):
            t, net, vat, problem = C.effective_vat(entry("a", 100, st))
            self.assertEqual((t, net, vat, problem), ("Not applicable", 100.0, 0.0, None))

    def test_computed_when_nothing_stored(self):
        t, net, vat, problem = C.effective_vat(entry("a", 1160, vat_treatment="Standard 16%"))
        self.assertEqual((net, vat, problem), (1000.0, 160.0, None))

    def test_stored_vat_is_used_while_its_basis_matches(self):
        e = entry("a", 1000, vat_treatment="Standard 16%", vat_amount=137.5, vat_basis=C.vat_basis(1000, "Standard 16%"))
        t, net, vat, problem = C.effective_vat(e)
        self.assertEqual((net, vat, problem), (862.5, 137.5, None))

    def test_amount_changed_after_vat_was_set_is_flagged_not_reused(self):
        # the mirror rewrote the amount from 1160 to 2320; the stored 160 must not be reused
        e = entry("a", 2320, vat_treatment="Standard 16%", vat_amount=160, vat_basis=C.vat_basis(1160, "Standard 16%"))
        t, net, vat, problem = C.effective_vat(e)
        self.assertIn("out of date", problem)

    def test_treatment_changed_after_vat_was_set_is_flagged(self):
        e = entry("a", 1080, vat_treatment="Petrol 8%", vat_amount=149.0, vat_basis=C.vat_basis(1080, "Standard 16%"))
        self.assertIn("out of date", C.effective_vat(e)[3])

    def test_no_basis_computes_from_the_treatment(self):
        # treatment set by hand in the desk form: no stored figure is trusted
        t, net, vat, problem = C.effective_vat(entry("a", 1160, vat_treatment="Standard 16%", vat_amount=999))
        self.assertEqual((net, vat, problem), (1000.0, 160.0, None))

    def test_a_typed_zero_on_a_rated_treatment_is_respected(self):
        e = entry("a", 1160, vat_treatment="Standard 16%", vat_amount=0, vat_basis=C.vat_basis(1160, "Standard 16%"))
        self.assertEqual(C.effective_vat(e)[1:3], (1160.0, 0.0))

    def test_non_numeric_override_is_a_vat_error(self):
        with self.assertRaises(C.VatError):
            C.split_vat(1000, "Standard 16%", "abc")

    def test_vat_without_receipt_is_a_problem(self):
        e = entry("a", 1160, vat_treatment="Standard 16%")
        e["receipt"] = None
        self.assertEqual(C.effective_vat(e)[3], "VAT claimed with no receipt")

    def test_no_vat_needs_no_receipt(self):
        e = entry("a", 100, vat_treatment="No VAT")
        e["receipt"] = None
        self.assertIsNone(C.effective_vat(e)[3])

    def test_etr_flag_counts_as_evidence(self):
        e = entry("a", 1160, vat_treatment="Standard 16%", etr_received=1)
        e["receipt"] = None
        self.assertIsNone(C.effective_vat(e)[3])


def line(name, account, gross, vat=0.0, **kw):
    return dict(name=name, account=account, gross=gross, net=round(gross - vat, 2), vat=vat,
                subject=kw.get("subject", "x"), txn_date="2026-10-01", **{k: v for k, v in kw.items() if k != "subject"})


class ErpLines(unittest.TestCase):
    L = [line("P1", "5240.2 - Parking", 200), line("P2", "5240.2 - Parking", 300),
         line("F1", "5208.1 - Staff Welfare", 1160, 160), line("F2", "5208.1 - Staff Welfare", 580, 80)]

    def test_grouped_has_one_line_per_account_plus_vat_plus_cash(self):
        rows, t = C.build_erp_lines(self.L, "1110 - Cash - VCL", "VAT - VCL", "grouped")
        self.assertEqual([r["account"] for r in rows], ["5208.1 - Staff Welfare", "5240.2 - Parking", "VAT - VCL", "1110 - Cash - VCL"])
        parking = next(r for r in rows if r["account"].startswith("5240.2"))
        self.assertEqual(parking["debit"], 500.0)
        self.assertEqual(parking["entries"], ["P1", "P2"])
        self.assertEqual(next(r for r in rows if r["account"] == "VAT - VCL")["debit"], 240.0)
        self.assertEqual(t["gross"], 2240.0)
        self.assertEqual(t["debit"], t["credit"])

    def test_each_mode_keeps_every_entry(self):
        rows, t = C.build_erp_lines(self.L, "1110 - Cash - VCL", "VAT - VCL", "each")
        self.assertEqual(len(rows), 4 + 2 + 1)   # 4 entries, 2 VAT lines, 1 cash
        self.assertEqual(t["debit"], t["credit"])

    def test_remark_lists_the_entries(self):
        rows, _ = C.build_erp_lines(self.L, "c", "v", "grouped")
        self.assertIn("P1, P2", next(r for r in rows if r["account"].startswith("5240.2"))["remark"])

    def test_long_entry_lists_are_truncated_not_dropped(self):
        many = [line(f"E{i}", "A", 10) for i in range(100)]
        rows, _ = C.build_erp_lines(many, "c", "v")
        self.assertIn("+40 more", rows[0]["remark"])
        self.assertEqual(len(rows[0]["entries"]), 100)

    def test_pennies_balance(self):
        ls = [line(f"E{i}", "A", 33.33, 4.6) for i in range(7)]
        rows, t = C.build_erp_lines(ls, "c", "v")
        self.assertAlmostEqual(t["debit"], t["credit"], places=2)


class TopUps(unittest.TestCase):
    """A Hauz-Pay top-up: Dr the wallet, Cr the bank on the line, no VAT, outside the spend totals."""
    SPEND = [line("P1", "5240.2 - Parking", 200), line("F1", "5208.1 - Staff Welfare", 1160, 160)]
    TOP = [line("T1", "Co-OP - VCL", 200000, cash_in=1), line("T2", "Co-OP - VCL", 50000, cash_in=1)]

    def test_replenishment_is_auto_not_applicable(self):
        self.assertIsNone(C.effective_vat(entry("T", 5000, "Replenishment"))[3])
        self.assertEqual(C.effective_vat(entry("T", 5000, "Replenishment"))[0], "Not applicable")

    def test_grouped_topups_credit_the_bank_and_debit_the_wallet(self):
        rows, t = C.build_erp_lines(self.SPEND + self.TOP, "Hauz-Pay Wallet - VCL", "VAT - VCL", "grouped")
        bank = [r for r in rows if r["account"] == "Co-OP - VCL"]
        self.assertEqual(len(bank), 1)
        self.assertEqual((bank[0]["debit"], bank[0]["credit"]), (0.0, 250000.0))
        self.assertIn("Top-up", bank[0]["remark"])
        wallet = [r for r in rows if r["account"] == "Hauz-Pay Wallet - VCL"]
        self.assertEqual(sorted((r["debit"], r["credit"]) for r in wallet), [(0.0, 1360.0), (250000.0, 0.0)])
        self.assertEqual(t["debit"], t["credit"])
        self.assertEqual((t["gross"], t["vat"], t["topup"], t["topups"]), (1360.0, 160.0, 250000.0, 2))

    def test_each_mode_one_credit_per_topup(self):
        rows, t = C.build_erp_lines(self.SPEND + self.TOP, "W", "V", "each")
        self.assertEqual(len([r for r in rows if r["account"] == "Co-OP - VCL"]), 2)
        self.assertTrue(all("Top-up" in r["remark"] for r in rows if r["account"] == "Co-OP - VCL"))
        self.assertEqual(t["debit"], t["credit"])

    def test_week_of_only_a_topup_has_no_empty_paid_out_line(self):
        rows, t = C.build_erp_lines(self.TOP[:1], "W", "V", "grouped")
        self.assertEqual([(r["account"], r["debit"], r["credit"]) for r in rows],
                         [("Co-OP - VCL", 0.0, 200000.0), ("W", 200000.0, 0.0)])
        self.assertEqual(t["gross"], 0.0)

    def test_no_topups_leaves_the_journal_as_before(self):
        rows, t = C.build_erp_lines(self.SPEND, "W", "V", "grouped")
        self.assertEqual(rows[-1]["remark"], "Petty cash paid out")
        self.assertEqual((t["topup"], t["topups"]), (0.0, 0))

    def test_a_topup_with_vat_is_refused(self):
        with self.assertRaises(C.VatError):
            C.build_erp_lines([line("T", "Co-OP - VCL", 1160, 160, cash_in=1)], "W", "V")


class QboLines(unittest.TestCase):
    def test_never_merges_across_tax_codes(self):
        ls = [dict(name="a", qbo_account="56", treatment="Standard 16%", gross=1160, vat=160, label="Food"),
              dict(name="b", qbo_account="56", treatment="No VAT", gross=500, vat=0, label="Food"),
              dict(name="c", qbo_account="56", treatment="Standard 16%", gross=580, vat=80, label="Food")]
        out = C.build_qbo_lines(ls)
        self.assertEqual(len(out), 2)
        std = next(o for o in out if o["tax_code"] == "3")
        self.assertEqual((std["amount"], std["vat"], std["entries"]), (1740.0, 240.0, ["a", "c"]))

    def test_payload_is_tax_inclusive_expense(self):
        out = C.build_qbo_lines([dict(name="a", qbo_account="235", treatment="No VAT", gross=200, vat=0, label="Parking")])
        p = C.build_qbo_payload(out, "95", "2026-10-03", "PC-Cash-2026-10-03", "note")
        self.assertEqual(p["PaymentType"], "Cash")
        self.assertEqual(p["GlobalTaxCalculation"], "TaxInclusive")
        self.assertEqual(p["AccountRef"], {"value": "95"})
        self.assertEqual(p["Line"][0]["AccountBasedExpenseLineDetail"]["TaxCodeRef"], {"value": "10"})
        self.assertEqual(p["Line"][0]["Amount"], 200.0)
        self.assertEqual(p["Line"][0]["AccountBasedExpenseLineDetail"]["TaxInclusiveAmt"], 200.0)

    def test_taxed_line_sends_net_as_amount_and_gross_as_inclusive(self):
        # QuickBooks adds VAT on top of Amount unless TaxInclusiveAmt carries the gross (54170 was +552).
        out = C.build_qbo_lines([dict(name="a", qbo_account="101", treatment="Standard 16%", gross=1950, vat=268.97, label="SE"),
                                 dict(name="b", qbo_account="1", treatment="Petrol 8%", gross=2000, vat=148.15, label="Fuel")], per_entry=True)
        p = C.build_qbo_payload(out, "95", "2026-10-10", "x", "y")
        got = {l["AccountBasedExpenseLineDetail"]["TaxInclusiveAmt"]: l["Amount"] for l in p["Line"]}
        self.assertEqual(got, {1950: 1681.03, 2000: 1851.85})

    def test_nothing_to_send(self):
        self.assertIsNone(C.build_qbo_payload([], "95", "2026-10-03", "x", "y"))

    def test_vat_gap_shows_when_receipt_vat_differs_from_the_tax_code(self):
        ls = [dict(name="a", qbo_account="56", treatment="Standard 16%", gross=1000, vat=137.5, label="Food")]
        g = C.build_qbo_lines(ls)[0]
        self.assertEqual(g["vat_qbo"], 137.93)
        self.assertEqual(g["vat_gap"], -0.43)

    def test_no_vat_gap_when_figures_agree(self):
        ls = [dict(name="a", qbo_account="56", treatment="Standard 16%", gross=1160, vat=160, label="Food")]
        self.assertEqual(C.build_qbo_lines(ls)[0]["vat_gap"], 0.0)

    def test_qbo_total_equals_erp_gross(self):
        ls = [dict(name="a", qbo_account="56", treatment="Standard 16%", gross=1160, vat=160, label="Food"),
              dict(name="b", qbo_account="235", treatment="No VAT", gross=500, vat=0, label="Parking")]
        out = C.build_qbo_lines(ls)
        self.assertEqual(round(sum(o["amount"] for o in out), 2), 1660.0)

    def test_per_entry_line_names_date_kind_etr_and_entry(self):
        ln = dict(name="PCE-2026-02444", qbo_account="101", treatment="Standard 16%", gross=1950, vat=268.97,
                  txn_date="2026-10-06", kind="Spares / Engineering", subject="3pcs PPR ELBOW", notes="SPARES",
                  supplier_ref="|0090773260000015419")
        out = C.build_qbo_lines([ln], per_entry=True)
        self.assertEqual(out[0]["description"],
                         "06/10/2026 · Spares / Engineering · 3pcs PPR ELBOW · SPARES · ETR 0090773260000015419 · PCE-2026-02444")

    def test_per_entry_skips_repeats_and_shows_pin(self):
        ln = dict(name="E1", qbo_account="1", treatment="Petrol 8%", gross=2000, vat=148.15, txn_date="2026-10-05",
                  kind="Bike Fuel", subject="Bike Fuel", supplier_ref="P051234567X|276087800")
        self.assertEqual(C.qbo_description(ln), "05/10/2026 · Bike Fuel · ETR 276087800 · PIN P051234567X · E1")

    def test_per_entry_plain_receipt_ref(self):
        ln = dict(name="E1", txn_date="2026-10-05", kind="Food", subject="Milk", supplier_ref="R-889")
        self.assertEqual(C.qbo_description(ln), "05/10/2026 · Food · Milk · Receipt R-889 · E1")

    def test_per_entry_keeps_every_entry_and_the_total(self):
        ls = [dict(name="a", qbo_account="56", treatment="No VAT", gross=100, vat=0, txn_date="2026-10-05", kind="Food"),
              dict(name="b", qbo_account="56", treatment="No VAT", gross=250, vat=0, txn_date="2026-10-04", kind="Food")]
        out = C.build_qbo_lines(ls, per_entry=True)
        self.assertEqual([o["entries"] for o in out], [["b"], ["a"]])
        self.assertEqual(round(sum(o["amount"] for o in out), 2), 350.0)


if __name__ == "__main__":
    unittest.main()
