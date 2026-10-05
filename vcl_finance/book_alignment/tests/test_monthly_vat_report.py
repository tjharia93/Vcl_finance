"""Pure-logic tests for the Monthly VAT Report (no database)."""
import unittest
from datetime import date

from vcl_finance.book_alignment.report.monthly_vat_report import monthly_vat_report as r


class TestMonthlyVatReport(unittest.TestCase):
    def test_month_range_handles_leap_february_and_december(self):
        self.assertEqual(r.month_range(2028, "February"), (date(2028, 2, 1), date(2028, 2, 29)))
        self.assertEqual(r.month_range(2026, "September"), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(r.month_range(2026, "December"), (date(2026, 12, 1), date(2026, 12, 31)))

    def test_import_by_template_or_customs_entry_number(self):
        self.assertTrue(r.is_import("Importation - VCL", "3941"))
        self.assertTrue(r.is_import("Kenya Tax - VCL", "26NBOIM410174734"))
        self.assertTrue(r.is_import(None, "26MBAIM405120534"))
        self.assertFalse(r.is_import("Kenya Tax - VCL", "KRACU0100063419/640"))
        self.assertFalse(r.is_import(None, None))

    def test_classification(self):
        self.assertEqual(r.classify_sale(160), r.OUT_STD)
        self.assertEqual(r.classify_sale(0), r.OUT_ZERO)
        self.assertEqual(r.classify_purchase(16, "Kenya Tax - VCL", "441"), r.IN_LOCAL)
        self.assertEqual(r.classify_purchase(0, None, "441"), r.IN_NONE)
        self.assertEqual(r.classify_purchase(24273, "Kenya Tax - VCL", "26NBOIM410174734"), r.IN_IMPORT)
