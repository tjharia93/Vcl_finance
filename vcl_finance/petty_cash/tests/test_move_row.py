"""``move.move_row`` end to end against an in-memory stand-in for Frappe, with the REAL
mirror (``mirror._entry_values`` / ``_reconcile_approval``, what the sheet's ``on_update``
runs) and the REAL ``hauzpay_core.plan`` for the re-import check. No bench needed:

    cd ~/projects/Vcl_finance && python3 -m unittest vcl_finance.petty_cash.tests.test_move_row -v
"""
import importlib.util
import itertools
import json
import pathlib
import sys
import types
import unittest
from datetime import date

_dir = pathlib.Path(__file__).resolve().parents[1]


class Throw(Exception):
    pass


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v

    def set(self, k, v):
        self[k] = v


def _flt(v, precision=None):
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        f = 0.0
    return round(f, precision) if precision is not None else f


STATE = types.SimpleNamespace(sheets={}, entries={}, names=itertools.count(1), roles=["Accounts Manager"],
                              tamper=None)


def _get_all(doctype, filters=None, fields=None, **kw):
    assert doctype == "Petty Cash Entry", doctype
    f = filters or {}
    rows = [e for e in STATE.entries.values()
            if (not f.get("origin_sheet") or e.origin_sheet == f["origin_sheet"])
            and (not f.get("origin_row") or e.origin_row in f["origin_row"][1])]
    return [Row(e) for e in rows]


def _throw(msg, exc=None, **kw):
    raise Throw(msg)


frappe = types.ModuleType("frappe")
frappe._ = lambda s: s
frappe.throw = _throw
frappe.PermissionError = Throw
frappe.whitelist = lambda *a, **kw: (lambda f: f)
frappe.get_roles = lambda: list(STATE.roles)
frappe.has_permission = lambda *a, **kw: True
frappe.get_all = _get_all
frappe.get_doc = lambda doctype, name: STATE.sheets[name]
frappe.session = types.SimpleNamespace(user="tanuj@vimit.com")
frappe_utils = types.ModuleType("frappe.utils")
frappe_utils.flt, frappe_utils.cint = _flt, lambda v: int(v or 0)
frappe_utils.now_datetime = lambda: "2026-10-15 10:00:00"
frappe.utils = frappe_utils

api = types.ModuleType("vcl_finance.petty_cash.api")
api.PETTY_APPROVERS = {"Accounts Manager", "System Manager", "Petty Cash Approver"}
api._next_idx = lambda rows: (max((r.row_idx or 0) for r in rows) + 1) if rows else 1
api._summary = lambda doc: {}


def _find_row(doc, entry_id):
    for table in ("vouchers", "wages_entries", "loan_entries", "misc_entries", "parking_entries"):
        for r in (doc.get(table) or []):
            if r.name == entry_id:
                return table, r
    return None, None


api._find_row = _find_row

entry_mod = types.ModuleType("vcl_finance.petty_cash.doctype.petty_cash_entry.petty_cash_entry")
entry_mod.reconstruct_date = lambda we, day: None


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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_STUBS = {
    "frappe": frappe, "frappe.utils": frappe_utils,
    "vcl_finance": types.ModuleType("vcl_finance"),
    "vcl_finance.petty_cash": types.ModuleType("vcl_finance.petty_cash"),
    "vcl_finance.petty_cash.api": api,
    "vcl_finance.petty_cash.doctype.petty_cash_entry.petty_cash_entry": entry_mod,
}
_LOADED = ("vcl_finance.petty_cash.move_core", "vcl_finance.petty_cash.move", "vcl_finance.petty_cash.mirror",
           "vcl_finance.petty_cash.hauzpay_core")
_saved = {k: sys.modules.get(k) for k in list(_STUBS) + list(_LOADED)}
sys.modules.update(_STUBS)
MC = _load("vcl_finance.petty_cash.move_core", _dir / "move_core.py")
sys.modules["vcl_finance.petty_cash"].move_core = MC
MV = _load("vcl_finance.petty_cash.move", _dir / "move.py")
M = _load("vcl_finance.petty_cash.mirror", _dir / "mirror.py")
HC = _load("vcl_finance.petty_cash.hauzpay_core", _dir / "hauzpay_core.py")
for _k in _saved:
    if _saved[_k] is not None:
        sys.modules[_k] = _saved[_k]
    else:
        sys.modules.pop(_k, None)

WE = date(2026, 10, 10)


def make_sheet(status="Draft"):
    """Week ending 10/10: an imported voucher, an imported wage, a hand-keyed voucher, blanks."""
    s = Sheet(name="PCS-2026-00046", week_ending=WE, float="Hauz-Pay", status=status, docstatus=0,
              company="Vimit Converters Limited", vouchers=[], wages_entries=[])
    s.vouchers += [
        Row(name="v-kp", row_idx=1, txn_date=date(2026, 10, 8), recipient="+254725266301 - KENNEDY WAFULA",
            category="SE", amount=3000, voucher_no="UJ8KP1", src_ref="UJ8KP1",
            notes="Suggested: SE (Known payee) · Service"),
        Row(name="v-hand", row_idx=2, txn_date=date(2026, 10, 7), recipient="Kamau", category="TG", amount=500),
        Row(name="v-blank", row_idx=3, category="OT", amount=0),
    ]
    s.wages_entries += [
        Row(name="w-jm", row_idx=1, txn_date=date(2026, 10, 6), entry_type="Wage",
            recipient="254721519206 - JOHN MUCHOKI", reason="Suggested: wage (on the roster) · Labour",
            amount=4753, src_ref="UJ6WAGE1"),
        Row(name="w-blank", row_idx=2, entry_type="Wage", amount=0),
    ]
    STATE.sheets[s.name] = s
    s.save()
    return s


def rows(s):
    return {r.name: r for t in ("vouchers", "wages_entries") for r in s[t]}


class MoveRow(unittest.TestCase):
    def setUp(self):
        STATE.sheets.clear(); STATE.entries.clear(); STATE.roles = ["Accounts Manager"]; STATE.tamper = None
        self._mods = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
        sys.modules.update({"frappe": frappe, "frappe.utils": frappe_utils})
        self.s = make_sheet()

    def tearDown(self):
        for k, v in self._mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_voucher_to_wages_voids_the_old_line_and_mirrors_one_new_unapproved_entry(self):
        before = len(STATE.entries)
        res = MV.move_row(self.s.name, "v-kp", "wages")
        old, new = rows(self.s)["v-kp"], rows(self.s)[res["new_row"]]
        self.assertEqual((old.cancelled, old.cancel_remark), (1, "Moved to Wages by tanuj@vimit.com"))
        self.assertEqual(res["new_row"], "w-blank", "the blank scaffolding row is filled, not a new one appended")
        self.assertEqual((new.txn_date, new.amount, new.recipient, new.src_ref, new.entry_type),
                         (date(2026, 10, 8), 3000.0, "+254725266301 - KENNEDY WAFULA", "UJ8KP1", "Wage"))
        self.assertIn("Suggested: SE (Known payee) · Service", new.reason)
        self.assertEqual(STATE.entries["v-kp"].status, "Void")
        e = STATE.entries[new.name]
        self.assertEqual((e.status, e.source_type, e.get("posting_account")), ("Unapproved", "Wages Entry", None))
        self.assertEqual(len(STATE.entries), before + 1, "exactly one new entry")
        self.assertFalse(new.locked)

    def test_wage_to_vouchers_lands_as_OT_with_a_suggestion(self):
        res = MV.move_row(self.s.name, "w-jm", "vouchers")
        new = rows(self.s)[res["new_row"]]
        self.assertEqual(res["new_row"], "v-blank")
        self.assertEqual((new.category, new.cash_in, new.src_ref, new.voucher_no, new.amount),
                         ("OT", 0, "UJ6WAGE1", "UJ6WAGE1", 4753.0))
        self.assertTrue(new.notes.startswith("Suggested: OT (moved from Wages"))
        self.assertEqual(rows(self.s)["w-jm"].cancel_remark, "Moved to Vouchers by tanuj@vimit.com")
        self.assertEqual(STATE.entries["w-jm"].status, "Void")
        e = STATE.entries[new.name]
        self.assertEqual((e.status, e.source_type, e.source_key), ("Unapproved", "Voucher Category", "OT"))

    def test_with_no_blank_row_left_a_row_is_appended(self):
        MV.move_row(self.s.name, "v-kp", "wages")
        res = MV.move_row(self.s.name, "v-hand", "wages")
        new = rows(self.s)[res["new_row"]]
        self.assertEqual((new.row_idx, new.amount), (3, 500.0))

    def test_a_blank_row_that_once_had_an_entry_is_not_reused(self):
        STATE.entries["w-blank"] = Row(name="PCE-old", origin_row="w-blank", origin_sheet=self.s.name,
                                       status="Approved", sync_state="Orphaned")
        res = MV.move_row(self.s.name, "v-kp", "wages")
        self.assertNotEqual(res["new_row"], "w-blank")
        self.assertEqual(STATE.entries["w-blank"].status, "Approved")

    def test_only_finance_may_move(self):
        STATE.roles = ["Petty Cash User"]
        with self.assertRaises(Throw):
            MV.move_row(self.s.name, "v-kp", "wages")
        self.assertEqual(MV.movable_rows(self.s.name), {"allowed": False, "rows": {}})
        STATE.roles = ["Petty Cash Approver"]
        self.assertTrue(MV.move_row(self.s.name, "v-kp", "wages")["ok"])

    def test_refusals_change_nothing(self):
        cases = [("v-kp", lambda: rows(self.s)["v-kp"].update(locked=1)),
                 ("v-kp", lambda: STATE.entries["v-kp"].update(status="Approved")),
                 ("v-kp", lambda: STATE.entries["v-kp"].update(journal_entry="ACC-JV-1")),
                 ("v-kp", lambda: STATE.entries["v-kp"].update(qbo_queue="QPQ-1")),
                 ("v-kp", lambda: self.s.update(status="Closed"))]
        for row, spoil in cases:
            self.setUp(); spoil()
            snap = json.dumps([self.s.vouchers, self.s.wages_entries], default=str, sort_keys=True)
            with self.assertRaises(Throw):
                MV.move_row(self.s.name, row, "wages")
            self.assertEqual(json.dumps([self.s.vouchers, self.s.wages_entries], default=str, sort_keys=True), snap)

    def test_a_mirror_that_does_not_come_out_right_throws(self):
        STATE.tamper = lambda e: e.update(status="Approved") if e.origin_row == "w-blank" else None
        with self.assertRaises(Throw):
            MV.move_row(self.s.name, "v-kp", "wages")
        self.setUp()
        STATE.tamper = lambda e: e.update(status="Unapproved") if e.origin_row == "v-kp" else None
        with self.assertRaises(Throw):
            MV.move_row(self.s.name, "v-kp", "wages")

    def test_movable_rows_lists_only_what_the_server_allows(self):
        STATE.entries["v-hand"].update(status="Withdrawn")
        out = MV.movable_rows(self.s.name)
        self.assertEqual(out["rows"], {"v-kp": {"to": "wages_entries", "label": "Move to wages"},
                                       "w-jm": {"to": "vouchers", "label": "Move to vouchers"}})
        self.s.status = "Approved"
        self.assertEqual(MV.movable_rows(self.s.name)["rows"], {})

    # ---- re-import: the moved line is still known ----
    def _replan(self):
        """What hauzpay._live_state + plan would see for this week now: every src_ref
        token on the sheet (voided rows included, as the SQL does) and the uncancelled
        rows for the legacy date+amount match (as _sheet_rows does)."""
        known = {tok: self.s.name for r in rows(self.s).values() for tok in HC.ref_tokens(r.src_ref)}
        live = [{"date": r.txn_date, "amount": float(r.amount), "cash_in": bool(r.cash_in), "phone": "",
                 "is_charges": False, "src_ref": r.src_ref, "label": r.recipient}
                for r in rows(self.s).values() if not r.cancelled and r.txn_date and r.amount]
        head = ("Date + Time", "Transaction ID", "Payment Method", "Payee", "name", "Narration",
                "Money In (KES)", "Money Out (KES)", "Running Balance (KES)", "eTIMS", "Expense Category",
                "Raised By", "Approved By")
        body = [("06/10/2026 10:00:00", "UJ6WAGE1", "PHONE", "+254721519206", "JOHN MUCHOKI", "Labour", "", "4753.00", "5247.00"),
                ("07/10/2026 10:00:00", "", "PHONE", "Kamau", "KAMAU", "Transport", "", "500.00", "4747.00"),
                ("08/10/2026 13:00:00", "UJ8KP1", "PHONE", "+254725266301", "KENNEDY WAFULA", "Service", "", "3000.00", "1747.00")]
        parsed = HC.parse_rows([("OPENING BALANCE", "10000.00"), ("CLOSING BALANCE", "1747.00"), head]
                               + [b + ("", "", "T", "T") for b in body])
        rules = json.loads((_dir / "hauzpay_rules.json").read_text())
        p = HC.plan(parsed, rules, {WE: {"name": self.s.name, "status": "Draft", "opening_balance": 0, "rows": live}},
                    known_refs=known)
        return {(l["txid"] or l["payee"]): l["state"] for l in p["weeks"][0]["lines"]}

    def test_re_importing_after_a_move_imports_nothing_twice(self):
        MV.move_row(self.s.name, "v-kp", "wages")
        MV.move_row(self.s.name, "w-jm", "vouchers")
        MV.move_row(self.s.name, "v-hand", "wages")       # hand-keyed: no src_ref, matched on date+amount
        self.assertEqual(self._replan(), {"UJ6WAGE1": "duplicate", "UJ8KP1": "duplicate", "Kamau": "on_sheet"})

    def test_ref_tokens_drop_the_charges_marker(self):
        self.assertEqual(HC.ref_tokens(HC.CHARGES_REF + "\nc-1\nc-2"), ["c-1", "c-2"])
        self.assertEqual(HC.ref_tokens("UJ8KP1"), ["UJ8KP1"])
        self.assertEqual(HC.ref_tokens(None), [])


if __name__ == "__main__":
    unittest.main()
