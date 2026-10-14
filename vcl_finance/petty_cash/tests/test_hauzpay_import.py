"""The Hauz Pay import decides nothing: every line it writes lands Unapproved, with no
account chosen, and nothing is approved, posted or staged (Tanuj, 14/10/2026).

Runs ``hauzpay.import_statement`` end to end against an in-memory stand-in for Frappe,
and mirrors the written rows with the REAL ``mirror._entry_values`` and
``mirror._reconcile_approval`` (what the sheet's ``on_update`` runs). No bench needed:

    cd ~/projects/Vcl_finance && python3 -m unittest vcl_finance.petty_cash.tests.test_hauzpay_import -v
"""
import importlib.util
import itertools
import pathlib
import sys
import types
import unittest
from datetime import date, datetime

_dir = pathlib.Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ stand-in Frappe
class Throw(Exception):
    pass


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


def _flt(v, precision=None):
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        f = 0.0
    return round(f, precision) if precision is not None else f


def _getdate(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


class DB:
    def __init__(self):
        self.set_value_calls = []

    def set_value(self, *a, **kw):
        self.set_value_calls.append(a)


STATE = types.SimpleNamespace(sheets={}, entries={}, db=DB(), names=itertools.count(1),
                              live_banks=["Co-OP - VCL"], tamper=None)


def _get_all(doctype, filters=None, fields=None, pluck=None, **kw):
    if doctype == "Account":
        return list(STATE.live_banks)
    if doctype == "Petty Cash Entry":
        rows = list(STATE.entries.values())
        want = (filters or {}).get("origin_row")
        if want:
            rows = [e for e in rows if e.origin_row in want[1]]
        return [Row(e) for e in rows]
    raise AssertionError(f"unexpected get_all({doctype})")


def _throw(msg, exc=None, **kw):
    raise Throw(msg)


frappe = types.ModuleType("frappe")
frappe._ = lambda s: s
frappe.throw = _throw
frappe.PermissionError = Throw
frappe.whitelist = lambda *a, **kw: (lambda f: f)
frappe.get_roles = lambda: ["Accounts Manager"]
frappe.has_permission = lambda *a, **kw: True
frappe.get_all = _get_all
frappe.get_doc = lambda doctype, name: STATE.sheets[name]
frappe.db = STATE.db
frappe_utils = types.ModuleType("frappe.utils")
frappe_utils.flt, frappe_utils.getdate, frappe_utils.cint = _flt, _getdate, lambda v: int(v or 0)
frappe.utils = frappe_utils


def _first_blank(rows, is_blank):           # api._first_blank: a locked row is never reused
    return next((r for r in rows if not int(r.get("locked") or 0) and is_blank(r)), None)


api = types.ModuleType("vcl_finance.petty_cash.api")
api.PETTY_APPROVERS = {"Accounts Manager"}
api._first_blank = _first_blank
api._next_idx = lambda rows: (max((r.row_idx or 0) for r in rows) + 1) if rows else 1
api._summary = lambda doc: {}
api._voucher_has_data = lambda v: bool(v.voucher_no or v.recipient or v.txn_date or v.notes
                                       or v.pc_received or v.etr_received or v.cash_in or _flt(v.amount))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Sheet(Row):
    """A Petty Cash Sheet whose save() runs the real mirror over its rows."""
    def append(self, table, values):
        r = Row(values)
        self[table].append(r)
        return r

    def save(self):
        for table in ("vouchers", "wages_entries"):
            for i, r in enumerate(self[table], 1):
                r.idx = i
                r.name = r.name or f"{table}-{next(STATE.names)}"
                if not M.is_real_row(r):
                    continue
                values = M._entry_values(self, table, r)
                prior = STATE.entries.get(r.name)
                M._reconcile_approval(values, prior["status"] if prior else None)
                e = Row(prior or {"name": f"PCE-{next(STATE.names)}"})
                e.update(values)
                if STATE.tamper:
                    STATE.tamper(e)
                STATE.entries[r.name] = e


def _create_for_week(week_ending, float_name, custodian_name, opening_balance):
    name = f"PCS-{week_ending.isoformat()}"
    STATE.sheets[name] = Sheet(name=name, week_ending=week_ending, float=float_name, status="Draft",
                               docstatus=0, company="Vimit Converters Limited", vouchers=[], wages_entries=[])
    return name


sheet_mod = types.ModuleType("vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet")
sheet_mod.week_saturday = lambda d: _getdate(d) + __import__("datetime").timedelta(days=(5 - _getdate(d).weekday()) % 7)
sheet_mod._prior_close = lambda float_name, we: None
sheet_mod.create_for_week = _create_for_week
entry_mod = types.ModuleType("vcl_finance.petty_cash.doctype.petty_cash_entry.petty_cash_entry")
entry_mod.reconstruct_date = lambda we, day: None

_STUBS = {
    "frappe": frappe, "frappe.utils": frappe_utils,
    "vcl_finance": types.ModuleType("vcl_finance"),
    "vcl_finance.petty_cash": types.ModuleType("vcl_finance.petty_cash"),
    "vcl_finance.petty_cash.api": api,
    "vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet": sheet_mod,
    "vcl_finance.petty_cash.doctype.petty_cash_entry.petty_cash_entry": entry_mod,
}
_LOADED = ("vcl_finance.petty_cash.hauzpay_core", "vcl_finance.petty_cash.hauzpay", "vcl_finance.petty_cash.mirror")
_saved = {k: sys.modules.get(k) for k in list(_STUBS) + list(_LOADED)}
sys.modules.update(_STUBS)
C = _load("vcl_finance.petty_cash.hauzpay_core", _dir / "hauzpay_core.py")
sys.modules["vcl_finance.petty_cash"].hauzpay_core = C
H = _load("vcl_finance.petty_cash.hauzpay", _dir / "hauzpay.py")
M = _load("vcl_finance.petty_cash.mirror", _dir / "mirror.py")
for _k in _saved:
    # Leave no stand-in behind for the next test module.
    if _saved[_k] is not None:
        sys.modules[_k] = _saved[_k]
    else:
        sys.modules.pop(_k, None)


# ------------------------------------------------------------------ the statement
HEAD = ("Date + Time", "Transaction ID", "Payment Method", "Payee", "name", "Narration",
        "Money In (KES)", "Money Out (KES)", "Running Balance (KES)", "eTIMS",
        "Expense Category", "Raised By", "Approved By")
LINES = [   # one of every route
    ("04/10/2026 09:00:00", "FT26282M088D", "bank_ipn", "+254728824856", "VIMIT CONVERTERS LTD Coop Bank", "KVN0002", "200000.00", ""),
    ("04/10/2026 09:30:00", "FT26170P61X6", "bank_ipn", "", "", "FT26170P61X6", "66000.00", ""),
    ("06/10/2026 10:00:00", "UJ6WAGE1", "PHONE", "+254721519206", "JOHN MUCHOKI", "Labour", "", "4753.00"),
    ("06/10/2026 10:00:00", "c-1", "PHONE", "+254721519206", "JOHN MUCHOKI", "Transaction Charges", "", "40.00"),
    ("07/10/2026 11:00:00", "UJ7ODD1", "PHONE", "Hubtech", "HUBTECH LIMITED.", "Payment", "", "19816.00"),
    ("08/10/2026 12:00:00", "UJ8KW1", "PHONE", "833301", "Simron", "Balance for Miller Welding", "", "11040.00"),
    ("08/10/2026 13:00:00", "UJ8KP1", "PHONE", "+254725266301", "KENNEDY WAFULA", "Service", "", "3000.00"),
]


def statement():
    rows, bal = [("OPENING BALANCE", "1000.00")], 1000.0
    body = []
    for dt, tx, method, payee, name, narr, mi, mo in LINES:
        bal += float(mi or 0) - float(mo or 0)
        body.append((dt, tx, method, payee, name, narr, mi, mo, f"{bal:.2f}", "", "", "T", "T"))
    return C.parse_rows(rows + [("CLOSING BALANCE", f"{bal:.2f}"), HEAD] + body)


class ImportDecidesNothing(unittest.TestCase):
    def setUp(self):
        STATE.sheets.clear(); STATE.entries.clear(); STATE.db.set_value_calls.clear(); STATE.tamper = None
        self._mods = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
        sys.modules.update({"frappe": frappe, "frappe.utils": frappe_utils})
        H._read_statement = lambda file_url: statement()
        H._live_state = lambda weeks: ({we: {"name": n, "status": s.status, "opening_balance": 1000.0, "rows": []}
                                        for n, s in STATE.sheets.items() for we in weeks if s.week_ending == we},
                                       {}, {}, "Shiro")

    def tearDown(self):
        for k, v in self._mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def run_import(self):
        return H.import_statement("/private/files/hauzpay.xlsx")

    def test_every_line_lands_unapproved_with_no_account_chosen(self):
        res = self.run_import()
        ents = list(STATE.entries.values())
        # 2 top-ups + 3 vouchers (flagged, keyword, known payee) + 1 wage + 1 lumped charges row
        self.assertEqual(len(ents), 7)
        self.assertEqual({e.status for e in ents}, {"Unapproved"})
        for e in ents:
            self.assertFalse(e.get("posting_account"), e.origin_row)
            self.assertFalse(e.get("override_reason"), e.origin_row)
            self.assertFalse(e.get("approved_by") or e.get("journal_entry") or e.get("qbo_queue"))
        self.assertEqual(STATE.db.set_value_calls, [], "the import wrote straight to the database")
        rows = [r for s in STATE.sheets.values() for t in ("vouchers", "wages_entries") for r in s[t]]
        self.assertFalse(any(r.locked for r in rows), "a locked row IS an approval")
        self.assertEqual((res["totals"]["unapproved"], res["totals"]["posted"]), (7, 0))
        self.assertEqual(res["totals"]["flagged"], 1)

    def test_the_flagged_line_lands_unapproved_too(self):
        self.run_import()
        flagged = next(e for e in STATE.entries.values() if "unknown payee" in (e.notes or ""))
        self.assertEqual((flagged.status, flagged.category), ("Unapproved", "OT"))

    def test_suggestions_are_in_the_notes_not_in_the_posting_fields(self):
        self.run_import()
        by_ref = {r.src_ref.split("\n")[0]: r for s in STATE.sheets.values()
                  for t in ("vouchers", "wages_entries") for r in s[t]}
        self.assertIn("Suggested: Cr Co-OP - VCL", by_ref["FT26282M088D"].notes)
        self.assertIn("No bank suggested", by_ref["FT26170P61X6"].notes)
        self.assertTrue(by_ref["UJ6WAGE1"].reason.startswith("Suggested: wage"))
        self.assertIn("Suggested: OT (unknown payee", by_ref["UJ7ODD1"].notes)
        self.assertIn("Suggested: SE", by_ref["UJ8KW1"].notes)
        self.assertIn("Suggested: SE (Known payee)", by_ref["UJ8KP1"].notes)
        self.assertIn("Suggested: OT (transaction charges)", by_ref[C.CHARGES_REF].notes)
        topup = STATE.entries[by_ref["FT26282M088D"].name]
        self.assertEqual((topup.cash_in, topup.source_type, topup.get("posting_account")), (1, "Replenishment", None))

    def test_nothing_posts_or_stages(self):
        self.run_import()
        for mod in ("post", "weekpost", "qbo", "postings", "posting"):
            self.assertNotIn(f"vcl_finance.petty_cash.{mod}", sys.modules)
        src = (_dir / "hauzpay.py").read_text()
        for word in ("posting_account =", "\"posting_account\":", "set_value(", "approve_entry", "locked = ",
                     ".submit(", "import weekpost", "import post", "import qbo"):
            self.assertNotIn(word, src, word)

    def test_a_locked_or_voided_blank_row_is_never_reused(self):
        name = _create_for_week(date(2026, 10, 10), "Hauz-Pay", "Shiro", 1000)
        STATE.sheets[name].vouchers.extend([Row(name="v-locked", row_idx=1, locked=1),
                                            Row(name="v-void", row_idx=2, cancelled=1)])
        self.run_import()
        self.assertFalse(STATE.sheets[name].vouchers[0].recipient)
        self.assertFalse(STATE.sheets[name].vouchers[1].recipient)
        self.assertEqual({e.status for e in STATE.entries.values()}, {"Unapproved"})

    def test_an_entry_that_comes_out_decided_rolls_the_import_back(self):
        STATE.tamper = lambda e: e.update(posting_account="Co-OP - VCL") if e.cash_in else None
        with self.assertRaises(Throw):
            self.run_import()
        STATE.sheets.clear(); STATE.entries.clear()
        STATE.tamper = lambda e: e.update(status="Approved")
        with self.assertRaises(Throw):
            self.run_import()

    def test_a_withdrawn_charges_row_is_topped_up_without_tripping_the_guard(self):
        name = _create_for_week(date(2026, 10, 10), "Hauz-Pay", "Shiro", 1000)
        STATE.sheets[name].vouchers.append(Row(name="v-chg", row_idx=1, recipient="Hauz Pay transaction charges",
                                               category="OT", amount=10, src_ref=C.CHARGES_REF + "\nold-1",
                                               voucher_no="CHARGES", txn_date=date(2026, 10, 5)))
        STATE.entries["v-chg"] = Row(origin_row="v-chg", status="Withdrawn", cash_in=0)
        res = self.run_import()
        self.assertEqual(STATE.entries["v-chg"].status, "Withdrawn")
        self.assertEqual(STATE.sheets[name].vouchers[0].amount, 50.0)
        self.assertEqual(res["totals"]["unapproved"], 6)


if __name__ == "__main__":
    unittest.main()
