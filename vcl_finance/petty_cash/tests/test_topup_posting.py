"""Hauz-Pay top-ups through the posting layer: ``post._classify``, ``weekpost._gates`` and
``weekpost._qbo_lines`` against a minimal stand-in for Frappe. No bench needed:

    cd ~/projects/Vcl_finance && python3 -m unittest vcl_finance.petty_cash.tests.test_topup_posting -v

A top-up on the Hauz-Pay float posts to ERPNext only (Dr the wallet, Cr the bank chosen on
the line). A cash-in on the Cash tin stays never-post, exactly as before.
"""
import sys
import types
import unittest


def _flt(v, precision=None):
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        f = 0.0
    return round(f, precision) if precision is not None else f


def _cint(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


class Throw(Exception):
    pass


def _throw(msg, exc=None, **kw):
    raise Throw(msg)


frappe = types.ModuleType("frappe")
frappe._ = lambda s: s
frappe.throw = _throw
frappe.PermissionError = Throw
frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.get_roles = lambda: ["Accounts Manager"]
frappe.get_all = lambda *a, **k: []
frappe.get_cached_value = lambda *a, **k: None
frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None, exists=lambda *a, **k: False)
utils = types.ModuleType("frappe.utils")
utils.flt, utils.cint = _flt, _cint
utils.now_datetime = lambda: "2026-10-15 10:00:00"
frappe.utils = utils
api = types.ModuleType("vcl_finance.petty_cash.api")
api.PETTY_PRIV = {"Accounts Manager"}
api.PETTY_APPROVERS = {"Accounts Manager"}

# Everything this file loads is taken back out of sys.modules afterwards: other suites assert
# that the posting modules are never imported by the statement import.
_LOADED = ("vcl_finance.petty_cash.post", "vcl_finance.petty_cash.weekpost", "vcl_finance.petty_cash.qbo",
           "vcl_finance.petty_cash.resolve", "vcl_finance.petty_cash.weekpost_core")
_saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils", "vcl_finance.petty_cash.api") + _LOADED}
sys.modules.update({"frappe": frappe, "frappe.utils": utils, "vcl_finance.petty_cash.api": api})
try:
    from vcl_finance.petty_cash import post as P
    from vcl_finance.petty_cash import resolve as R
    from vcl_finance.petty_cash import weekpost as W
    from vcl_finance.petty_cash import weekpost_core as C
finally:
    for k, v in _saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

# The Replenishment route is shared with the Cash tin, where it is marked never-post.
R.resolve = lambda e, company=None: {"outcome": R.NEVER, "erp_account": None, "qbo_account": None,
                                     "tax_code": None, "reason": "Float top-up, booked at the bank",
                                     "map_row": "PM-REPL"}


def topup(name="PCE-1", float_="Hauz-Pay", account=None, **kw):
    e = dict(name=name, float=float_, company="Vimit Converters Limited", source_type="Replenishment",
             source_key="", amount=200000, cash_in=1, status="Approved", cancelled=0,
             posting_account=account, journal_entry=None, txn_date="2026-10-09")
    e.update(kw)
    return e


def survey(rows):
    out = {P.POSTED: [], P.READY: [], P.BLOCKED: [], P.EXCLUDED: []}
    for e in rows:
        state, account, reason = P._classify(e)
        e["_account"], e["_reason"] = account, reason
        e["_treatment"], e["_net"], e["_vat"], e["_vat_problem"] = C.effective_vat(e)
        out[state].append(e)
    return out


SHEET = {"status": "Approved"}


class Classify(unittest.TestCase):
    def test_hauzpay_topup_with_bank_is_ready(self):
        self.assertEqual(P._classify(topup(account="Co-OP - VCL")), (P.READY, "Co-OP - VCL", None))

    def test_hauzpay_topup_without_bank_is_held_in_plain_words(self):
        state, account, reason = P._classify(topup())
        self.assertEqual((state, account), (P.BLOCKED, None))
        self.assertEqual(reason, "Top-up: pick the bank it came from")

    def test_cash_tin_topup_stays_never_post(self):
        state, _, _ = P._classify(topup(float_="Cash"))
        self.assertEqual(state, P.EXCLUDED)

    def test_unapproved_topup_is_not_ready(self):
        self.assertEqual(P._classify(topup(account="Co-OP - VCL", status="Unapproved"))[0], P.BLOCKED)

    def test_kept_out_topup_stays_out(self):
        self.assertEqual(P._classify(topup(account="Co-OP - VCL", exclude_from_posting=1))[0], P.EXCLUDED)


class Vat(unittest.TestCase):
    def test_topup_needs_no_vat_review(self):
        t, net, vat, problem = C.effective_vat(topup())
        self.assertEqual((t, net, vat, problem), ("Not applicable", 200000.0, 0.0, None))

    def test_any_cash_in_is_not_applicable(self):
        self.assertIsNone(C.effective_vat(dict(name="x", amount=50, cash_in=1, source_type="Voucher Category"))[3])

    def test_is_topup_is_hauzpay_cash_in_only(self):
        self.assertTrue(C.is_topup(topup()))
        self.assertFalse(C.is_topup(topup(float_="Cash")))
        self.assertFalse(C.is_topup(topup(cash_in=0)))


class Gates(unittest.TestCase):
    def gate(self, rows, key):
        b = survey(rows)
        return next(g for g in W._gates(SHEET, rows, b) if g["key"] == key)

    def test_hauzpay_topup_with_bank_passes_the_cash_in_gate(self):
        self.assertTrue(self.gate([topup(account="Co-OP - VCL")], "no_cash_in")["ok"])
        self.assertTrue(self.gate([topup(account="Co-OP - VCL")], "vat")["ok"])

    def test_hauzpay_topup_without_bank_holds_the_accounts_gate(self):
        g = self.gate([topup()], "accounts")
        self.assertFalse(g["ok"])
        self.assertIn("pick the bank", g["detail"])

    def test_cash_tin_cash_in_with_an_account_still_fails(self):
        # Someone put an account on a Cash-tin top-up: it must still not post.
        self.assertFalse(self.gate([topup(float_="Cash", account="Co-OP - VCL")], "no_cash_in")["ok"])


class QuickBooks(unittest.TestCase):
    def test_topup_never_reaches_quickbooks(self):
        rows = [topup(account="Co-OP - VCL", journal_entry="ACC-JV-1")]
        b = survey(rows)
        lines, blocked = W._qbo_lines(b[P.POSTED])
        self.assertEqual((lines, blocked), ([], {}))


class Journal(unittest.TestCase):
    def test_line_carries_cash_in_for_the_journal(self):
        e = survey([topup(account="Co-OP - VCL")])[P.READY][0]
        ln = W._line(e)
        self.assertEqual(ln["cash_in"], 1)
        rows, t = C.build_erp_lines([ln], "Hauz-Pay Wallet - VCL", "VAT - VCL", "grouped")
        self.assertEqual([(r["account"], r["debit"], r["credit"]) for r in rows],
                         [("Co-OP - VCL", 0.0, 200000.0), ("Hauz-Pay Wallet - VCL", 200000.0, 0.0)])
        self.assertEqual((t["gross"], t["topup"]), (0.0, 200000.0))


if __name__ == "__main__":
    unittest.main()
