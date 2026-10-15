"""Unit tests for the Hauz Pay statement import's pure logic. No bench needed:

    cd ~/projects/Vcl_finance && python3 -m unittest vcl_finance.petty_cash.tests.test_hauzpay_core -v
"""
import importlib.util
import json
import pathlib
import unittest
from datetime import date, datetime

_dir = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("hauzpay_core", _dir / "hauzpay_core.py")
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)
RULES = json.loads((_dir / "hauzpay_rules.json").read_text())

HEAD = ("Date + Time", "Transaction ID", "Payment Method", "Payee", "name", "Narration",
        "Money In (KES)", "Money Out (KES)", "Running Balance (KES)", "eTIMS",
        "Expense Category", "Raised By", "Approved By")
ROSTER_PHONE = "+254721519206"     # JOHN MUCHOKI in hauzpay_rules.json


def stmt(lines, opening="1000.00", closing=None):
    """Rows as openpyxl gives them for the Oct-2026 export: strings, 13 columns."""
    rows = [("PERIOD", "03/10/2026 21:00:00 - 10/10/2026 20:59:59") + ("",) * 11,
            ("OPENING BALANCE", opening) + ("",) * 11]
    bal = float(opening)
    body = []
    for dt, txid, payee, name, narr, mi, mo in lines:
        bal += float(mi or 0) - float(mo or 0)
        body.append((dt, txid, "PHONE", payee, name, narr, mi, mo, f"{bal:.2f}", "Non-eTIMS", "", "T", "T"))
    rows.append(("CLOSING BALANCE", closing if closing is not None else f"{bal:.2f}") + ("",) * 11)
    rows.append(HEAD)
    return rows + body


WEEK = [
    ("04/10/2026 09:00:00", "TOPUP1", "", "Bank", "deposit", "50000.00", ""),
    ("06/10/2026 10:00:00", "UJ6WAGE1", ROSTER_PHONE, "JOHN MUCHOKI", "Labour", "", "4753.00"),
    ("06/10/2026 10:00:00", "c-1", ROSTER_PHONE, "JOHN MUCHOKI", "Transaction Charges", "", "40.00"),
    ("07/10/2026 11:00:00", "UJ7ODD1", "Hubtech", "HUBTECH LIMITED.", "Payment", "", "19816.00"),
    ("07/10/2026 11:00:00", "c-2", "Hubtech", "HUBTECH LIMITED.", "Transaction Charges", "", "91.00"),
    ("08/10/2026 12:00:00", "UJ8KW1", "833301", "Simron", "Balance for Miller Welding", "", "11040.00"),
]


class Helpers(unittest.TestCase):
    def test_week_is_sun_to_sat_anchored_saturday(self):
        self.assertEqual(C.week_saturday(date(2026, 10, 4)), date(2026, 10, 10))   # Sunday opens
        self.assertEqual(C.week_saturday(date(2026, 10, 10)), date(2026, 10, 10))  # Saturday ends
        self.assertEqual(C.week_saturday(date(2026, 10, 11)), date(2026, 10, 17))

    def test_phone_key_agrees_across_spellings(self):
        for v in ("+254721519206", "254721519206", "0721519206", "+254 721 519 206"):
            self.assertEqual(C.phone_key(v), "721519206")
        self.assertEqual(C.phone_key("833301"), "")
        self.assertEqual(C.phone_key("1020260001282156"), "")

    def test_money_reads_strings_commas_and_blanks(self):
        self.assertEqual(C.money("19,816.00"), 19816.0)
        self.assertEqual(C.money(""), 0.0)
        self.assertEqual(C.money(None), 0.0)
        self.assertEqual(C.money(55), 55.0)


class Parse(unittest.TestCase):
    def test_reads_meta_header_and_lines(self):
        p = C.parse_rows(stmt(WEEK))
        self.assertEqual(p["opening"], 1000.0)
        self.assertEqual(len(p["lines"]), 6)
        self.assertEqual(p["lines"][2]["is_charge"], True)
        self.assertEqual(p["lines"][0]["date"], date(2026, 10, 4))
        self.assertEqual(p["lines"][0]["row"], 5)            # rows 1-3 meta, 4 header, 5 first txn

    def test_columns_are_found_by_name_not_position(self):
        rows = stmt(WEEK)
        # Swap two columns in header and body: still parses the same.
        sw = lambda r: (r[0], r[2], r[1]) + tuple(r[3:]) if len(r) > 3 else r
        p = C.parse_rows([sw(r) for r in rows])
        self.assertEqual(p["lines"][1]["txid"], "UJ6WAGE1")

    def test_june_headerless_layout(self):
        rows = [("19/06/2026 11:40:57", "UFJ1", "PHONE", ROSTER_PHONE, "JOHN", "X0X", "", "4753.00", "1.00", "T", "T")]
        p = C.parse_rows(rows)
        self.assertEqual(len(p["lines"]), 1)
        self.assertIsNone(p["opening"])
        self.assertTrue(p["warnings"])

    def test_not_a_statement(self):
        with self.assertRaises(ValueError):
            C.parse_rows([("Foo", "Transaction ID", "Bar")])

    def test_balance_check_ties_and_reports_difference(self):
        ok = C.balance_check(C.parse_rows(stmt(WEEK)))
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["difference"], 0.0)
        bad = C.balance_check(C.parse_rows(stmt(WEEK, closing="999999.00")))
        self.assertFalse(bad["ok"])
        self.assertNotEqual(bad["difference"], 0.0)


class Classify(unittest.TestCase):
    def setUp(self):
        self.p = C.parse_rows(stmt(WEEK))
        self.roster = C.build_roster(RULES)

    def route(self, i):
        return C.classify(self.p["lines"][i], RULES, self.roster)

    def test_routes(self):
        self.assertEqual(self.route(0)["route"], C.CASH_IN)
        w = self.route(1)
        self.assertEqual((w["route"], w["kind"]), (C.WAGE, "wage"))
        self.assertEqual(w["recipient"], "254721519206 - JOHN MUCHOKI")
        self.assertEqual(self.route(2)["route"], C.CHARGE)
        f = self.route(3)
        self.assertEqual((f["route"], f["category"]), (C.FLAGGED, "OT"))
        k = self.route(5)
        self.assertEqual((k["route"], k["category"]), (C.VOUCHER, "SE"))

    def test_learned_roster_routes_wage_but_rules_win_on_name(self):
        roster = C.build_roster(RULES, {"254799000111": "NEW CASUAL", "254721519206": "OTHER NAME"})
        line = dict(self.p["lines"][1], payee="0799000111")
        c = C.classify(line, RULES, roster)
        self.assertEqual((c["route"], c["why"]), (C.WAGE, "Paid as a wage before"))
        self.assertIn("JOHN MUCHOKI", C.classify(self.p["lines"][1], RULES, roster)["recipient"])


class TopUps(unittest.TestCase):
    """Money in = a wallet top-up: ERPNext only, credited to the bank it came from."""
    def line(self, **kw):
        base = dict(row=5, date=date(2026, 10, 9), txid="FT26282M088D", method="bank_ipn", payee="+254728824856",
                    name="VIMIT CONVERTERS LTD Coop Bank", narration="KVN0002", money_in=200000.0, money_out=0.0,
                    balance=None, expense_category="", is_charge=False)
        base.update(kw)
        return base

    def test_coop_deposit_maps_to_coop_bank_account(self):
        c = C.classify(self.line(), RULES, {})
        self.assertEqual((c["route"], c["bank_account"], c["needs_bank"], c["erp_only"]),
                         (C.CASH_IN, "Co-OP - VCL", False, True))
        self.assertEqual(c["source"], "Bank deposit")
        self.assertIn("Co-op Bank", c["recipient"])

    def test_no_bank_in_the_text_is_flagged_never_guessed(self):
        # The June 2026 export: bank_ipn with no name, narration = the bank reference.
        c = C.classify(self.line(payee="", name="", narration="FT26170P61X6"), RULES, {})
        self.assertEqual((c["bank_account"], c["needs_bank"]), (None, True))
        self.assertTrue(c["why"].startswith("bank?"))

    def test_stk_topup_is_flagged_until_a_rule_names_its_account(self):
        c = C.classify(self.line(method="mpesa_stk", payee="", name="", narration="STK"), RULES, {})
        self.assertEqual((c["source"], c["needs_bank"]), ("M-Pesa STK", True))

    def test_rule_order_and_list_patterns(self):
        rules = {"topup_banks": [{"match": ["abc bank"], "account": "ABC BANK - VCL", "label": "ABC"}]}
        self.assertEqual(C.classify(self.line(name="Deposit from ABC Bank"), rules, {})["bank_account"], "ABC BANK - VCL")
        self.assertIsNone(C.classify(self.line(), rules, {})["bank_account"])

    def test_plan_counts_lines_needing_a_bank(self):
        rows = stmt([("04/10/2026 09:00:00", "TOPUP1", "", "", "FT1", "50000.00", "")])
        pl = C.plan(C.parse_rows(rows), RULES, {})
        self.assertEqual((pl["totals"]["needs_bank"], pl["weeks"][0]["needs_bank"]), (1, 1))


class Suggestions(unittest.TestCase):
    """Nothing is decided by the import: every route, category and bank is worded as a
    suggestion for the reviewer (Tanuj, 14/10/2026)."""
    def test_every_new_line_carries_a_suggestion(self):
        rows = stmt(WEEK + [("09/10/2026 09:00:00", "FT26282M088D", "+254728824856", "VIMIT CONVERTERS LTD Coop Bank",
                             "KVN0002", "200000.00", "")])
        lines = {l["txid"]: l["suggestion"] for l in C.plan(C.parse_rows(rows), RULES, {})["weeks"][0]["lines"]}
        self.assertEqual(lines["FT26282M088D"], "Suggested: Cr Co-OP - VCL (Co-op Bank)")
        self.assertTrue(lines["TOPUP1"].startswith("No bank suggested"))
        self.assertEqual(lines["UJ6WAGE1"], "Suggested: wage (Staff roster)")
        self.assertEqual(lines["UJ7ODD1"], "Suggested: OT (unknown payee, pick a category)")
        self.assertEqual(lines["UJ8KW1"], "Suggested: SE (Keyword 'miller welding')")
        self.assertEqual(lines["c-1"], "Suggested: OT (transaction charges)")

    def test_a_topup_suggestion_never_names_a_bank_it_did_not_find(self):
        c = C.classify_topup(dict(method="bank_ipn", name="", narration="FT1", payee=""), RULES)
        self.assertNotIn("Cr ", C.suggestion(c))


class Plan(unittest.TestCase):
    def test_new_week_counts_and_charges_lumped(self):
        pl = C.plan(C.parse_rows(stmt(WEEK)), RULES, {})
        self.assertEqual(len(pl["weeks"]), 1)
        w = pl["weeks"][0]
        self.assertEqual((w["week_ending"], w["action"]), ("2026-10-10", "create"))
        self.assertEqual(w["counts"]["new"], 6)
        self.assertEqual(w["charges"], {"amount": 131.0, "refs": ["c-1", "c-2"], "count": 2, "skipped": 0})
        self.assertEqual(w["flagged"], 1)
        self.assertEqual(pl["totals"][C.FLAGGED], 1)
        self.assertEqual(w["statement_opening"], 1000.0)

    def test_known_txid_is_never_imported_twice(self):
        pl = C.plan(C.parse_rows(stmt(WEEK)), RULES, {}, known_refs={"UJ6WAGE1": "PCS-1", "c-1": "PCS-1"})
        states = {l["txid"]: l["state"] for l in pl["weeks"][0]["lines"]}
        self.assertEqual(states["UJ6WAGE1"], "duplicate")
        self.assertEqual(states["c-1"], "duplicate")
        self.assertEqual(pl["weeks"][0]["charges"]["amount"], 91.0)

    def test_locked_week_is_skipped(self):
        sheets = {date(2026, 10, 10): {"name": "PCS-9", "status": "Approved", "opening_balance": 1000, "rows": []}}
        w = C.plan(C.parse_rows(stmt(WEEK)), RULES, sheets)["weeks"][0]
        self.assertEqual(w["action"], "skip")
        self.assertEqual(w["counts"]["skipped_locked"], 6)
        self.assertEqual(w["charges"]["amount"], 0)

    def test_legacy_rows_without_ids_match_by_date_amount_phone(self):
        # Two 4,753 wages on the same day; the sheet already has ONE (back-filled, no id).
        lines = WEEK[:2] + [("06/10/2026 10:05:00", "UJ6WAGE2", "+254729355273", "JOHN OWITI", "Labour", "", "4753.00")]
        rows = [{"date": date(2026, 10, 6), "amount": 4753.0, "cash_in": False, "phone": "729355273",
                 "src_ref": None, "label": "254729355273 - OWITI"},
                {"date": date(2026, 10, 4), "amount": 50000.0, "cash_in": True, "phone": "", "src_ref": None}]
        sheets = {date(2026, 10, 10): {"name": "PCS-9", "status": "Draft", "opening_balance": 900, "rows": rows}}
        w = C.plan(C.parse_rows(stmt(lines)), RULES, sheets)["weeks"][0]
        st = {l["txid"]: l["state"] for l in w["lines"]}
        self.assertEqual(st, {"TOPUP1": "on_sheet", "UJ6WAGE1": "new", "UJ6WAGE2": "on_sheet"})
        self.assertEqual(w["action"], "use")
        self.assertEqual(w["opening_difference"], 100.0)     # statement 1,000 - sheet 900

    def test_legacy_charges_row_blocks_charges(self):
        rows = [{"date": date(2026, 10, 10), "amount": 500.0, "cash_in": False, "phone": "",
                 "is_charges": True, "src_ref": None}]
        sheets = {date(2026, 10, 10): {"name": "PCS-9", "status": "Draft", "opening_balance": 1000, "rows": rows}}
        w = C.plan(C.parse_rows(stmt(WEEK)), RULES, sheets)["weeks"][0]
        self.assertEqual(w["charges"]["amount"], 0)
        self.assertEqual(w["charges"]["skipped"], 2)

    def test_multi_week_carries_running_opening(self):
        lines = WEEK[:2] + [("12/10/2026 08:00:00", "NEXT1", ROSTER_PHONE, "JOHN", "Labour", "", "100.00")]
        pl = C.plan(C.parse_rows(stmt(lines)), RULES, {})
        self.assertEqual([w["week_ending"] for w in pl["weeks"]], ["2026-10-10", "2026-10-17"])
        self.assertEqual(pl["weeks"][1]["statement_opening"], 1000 + 50000 - 4753)



class Opening(unittest.TestCase):
    """The statement sets each week's opening (Tanuj, 15/10/2026)."""

    def test_period_start(self):
        self.assertEqual(C.period_start("03/10/2026 21:00:00 - 10/10/2026 20:59:59"), datetime(2026, 10, 3, 21, 0))
        self.assertEqual(C.period_start("01/07/2026 00:00:00 - 31/07/2026 23:59:59"), datetime(2026, 7, 1))
        self.assertIsNone(C.period_start(None))
        self.assertIsNone(C.period_start("garbage"))

    def test_an_evening_start_covers_the_next_sunday_a_midweek_start_does_not(self):
        self.assertTrue(C.covers_week_start(datetime(2026, 10, 3, 21), date(2026, 10, 10)))   # Sat 21:00 = Sun 04/10
        self.assertTrue(C.covers_week_start(datetime(2026, 10, 4), date(2026, 10, 10)))
        self.assertFalse(C.covers_week_start(datetime(2026, 10, 3, 9), date(2026, 10, 3)))   # Sat morning, week began 27/09
        self.assertFalse(C.covers_week_start(datetime(2026, 7, 1), date(2026, 7, 4)))         # July export: Wednesday
        self.assertFalse(C.covers_week_start(None, date(2026, 10, 10)))

    def test_an_open_sheet_is_set_from_the_statement(self):
        sheets = {date(2026, 10, 10): {"name": "PCS-9", "status": "Draft", "opening_balance": 45656.77, "rows": []}}
        w = C.plan(C.parse_rows(stmt(WEEK)), RULES, sheets)["weeks"][0]
        self.assertEqual((w["opening_action"], w["opening_statement"], w["opening_system"], w["opening_difference"]),
                         ("set", 1000.0, 45656.77, -44656.77))
        self.assertEqual(w["opening_note"], "Opening set from statement: 1,000.00 "
                                            "(system carry-forward was 45,656.77, difference -44,656.77)")

    def test_a_closed_sheet_is_flagged_never_changed(self):
        for st in ("Approved", "Closed", "Submitted"):
            sheets = {date(2026, 10, 10): {"name": "PCS-9", "status": st, "opening_balance": 900, "rows": []}}
            w = C.plan(C.parse_rows(stmt(WEEK)), RULES, sheets)["weeks"][0]
            self.assertEqual(w["opening_action"], "flag")
            self.assertIn(f"Week is {st}: opening 900.00 not changed. The statement says 1,000.00 (difference 100.00).",
                          w["opening_note"])
        sheets[date(2026, 10, 10)]["opening_balance"] = 1000
        self.assertEqual(C.plan(C.parse_rows(stmt(WEEK)), RULES, sheets)["weeks"][0]["opening_action"], "ok")

    def test_a_statement_starting_midweek_leaves_that_week_and_sets_the_next(self):
        rows = stmt(WEEK + [("12/10/2026 08:00:00", "NEXT1", ROSTER_PHONE, "JOHN", "Labour", "", "100.00")])
        rows[0] = ("PERIOD", "07/10/2026 00:00:00 - 12/10/2026 23:59:59") + ("",) * 11
        sheets = {date(2026, 10, 10): {"name": "PCS-9", "status": "Draft", "opening_balance": 777, "rows": []},
                  date(2026, 10, 17): {"name": "PCS-10", "status": "Draft", "opening_balance": 777, "rows": []}}
        w1, w2 = C.plan(C.parse_rows(rows), RULES, sheets)["weeks"]
        self.assertEqual(w1["opening_action"], "keep")
        self.assertIn("The statement starts 07/10/2026, after this week began", w1["opening_note"])
        self.assertEqual((w2["opening_action"], w2["opening_statement"]), ("set", round(1000 + sum(float(l[5] or 0) - float(l[6] or 0) for l in WEEK), 2)))

    def test_no_opening_on_the_statement_changes_nothing(self):
        w = C.decide_opening({"statement_opening": None, "status": "Draft", "statement_covers_start": True}, 5.0)
        self.assertEqual(w["opening_action"], "none")

    def test_a_new_sheet_with_no_earlier_sheet(self):
        w = C.decide_opening({"statement_opening": 1000.0, "status": "New", "statement_covers_start": True}, None)
        self.assertEqual((w["opening_action"], w["opening_difference"]), ("set", None))
        self.assertIn("system carry-forward was none (no earlier sheet)", w["opening_note"])


if __name__ == "__main__":
    unittest.main()
