"""Step 2 / 2a checks for native lines, run against a real site and ROLLED BACK.

    bench --site <site> execute vcl_finance.petty_cash.tests.live_native_step2.run

Not a unittest module (the unit suite here runs without a bench). Everything happens in
one transaction that is rolled back at the end, so the site is left as it was.
"""
import frappe

WEEK = "2027-01-09"   # a Saturday far from any real sheet
RESULTS = []
BASE = {}


def check(name, fn, expect_error=None):
    try:
        out = fn()
        ok = expect_error is None and out is not False
        detail = "" if ok else ("expected refusal, got none" if expect_error else f"returned {out!r}")
    except Exception as e:      # noqa: BLE001 — a refusal is the thing under test
        msg = str(e)
        ok = expect_error is not None and expect_error.lower() in msg.lower()
        detail = "" if ok else f"{type(e).__name__}: {msg[:160]}"
    RESULTS.append((name, ok, detail))


def new_sheet(native=0, week=WEEK, float_name="Cash"):
    d = frappe.new_doc("Petty Cash Sheet")
    d.week_ending, d.float, d.custodian_name, d.status = week, float_name, "Test", "Draft"
    d.opening_balance, d.native_lines = 1000, native
    d.insert()
    return d


def new_entry(sheet, amount=100, txn_date="2027-01-05", **kw):
    e = frappe.new_doc("Petty Cash Entry")
    e.update({"week_sheet": sheet, "txn_date": txn_date, "amount": amount,
              "source_type": "Voucher Category", "source_key": "TE", "category": "TE",
              "entry_kind": "voucher", "recipient": "Test"})
    e.update(kw)
    e.insert()
    return e


def run():
    frappe.set_user("Administrator")
    # What is already on the site (a replayed Trial week may be kept there on purpose).
    BASE["native"] = frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")})
    # These checks run inside ONE transaction that is rolled back at the end, so the actions
    # must not start a transaction of their own (the separate-session runs test that path).
    frappe.flags.pc_keep_transaction = True
    from vcl_finance.petty_cash import weeks, post, vat, weekpost, reconcile
    frappe.db.savepoint("native_step2")
    try:
        # ---- switch rules ----
        legacy = new_sheet(0)
        legacy.vouchers[0].update({"txn_date": "2027-01-05", "recipient": "Row", "amount": 50, "category": "TE"})
        legacy.save()
        mirrored = frappe.get_all("Petty Cash Entry", {"origin_sheet": legacy.name}, pluck="name")
        check("legacy sheet still mirrors its row", lambda: len(mirrored) == 1)

        def on_with_rows():
            legacy.native_lines = 1; legacy.save()
        check("R1 switch ON refused while the sheet has a real row", on_with_rows, "can only be turned on")
        legacy.reload()

        check("L3 native entry refused on a week that keeps rows",
              lambda: new_entry(legacy.name), "keeps its lines as rows")

        native = new_sheet(1, week="2027-01-16")
        check("R1b switch ON accepted on an empty sheet", lambda: frappe.db.get_value("Petty Cash Sheet", native.name, "native_lines") == 1)
        check("native sheet is not padded with grid rows", lambda: len(native.vouchers) == 0 and len(native.wages_entries) == 0)

        e1 = new_entry(native.name, 100, "2027-01-12")
        e2 = new_entry(native.name, 250, "2027-01-14", cash_in=1)
        e3 = new_entry(native.name, 40, "2027-01-15", company=None)
        check("L5 float copied from the sheet", lambda: e1.float == "Cash" and e1.sync_state == "Native")
        check("L5 company defaults from the sheet", lambda: e3.company == native.company)
        check("native entry carries no origin_sheet, origin_row is its own name",
              lambda: not e1.origin_sheet and e1.origin_row == e1.name)
        check("week_ending derived to the sheet's Saturday", lambda: str(e1.week_ending) == "2027-01-16")

        def add_row():
            native.reload(); native.append("vouchers", {"txn_date": "2027-01-12", "recipient": "X", "amount": 5}); native.save()
        check("R4 real row refused on a native week", add_row, "cannot be added as a row")
        native.reload()

        def off_with_entries():
            native.native_lines = 0; native.save()
        check("R2 switch OFF refused while native entries exist", off_with_entries, "cannot be")
        native.reload()

        for _ in range(5):
            native.save()
        states = frappe.get_all("Petty Cash Entry", {"week_sheet": native.name}, pluck="sync_state")
        check("R3 five saves of a native sheet orphan nothing", lambda: states == ["Native"] * 3)
        native.reload()
        check("totals come from the entries (out 140, in 250)",
              lambda: (native.total_out, native.total_in, native.expected_close) == (140, 250, 1110))

        # ---- link rules ----
        check("L1 both links refused", lambda: new_entry(native.name, origin_sheet=legacy.name), "never both")
        def unlinked():
            e = frappe.new_doc("Petty Cash Entry"); e.update({"txn_date": "2027-01-12", "amount": 5, "source_type": "Voucher Category", "source_key": "TE"}); e.insert()
        check("L2 new unlinked entry refused", unlinked, "must belong to a week")
        check("L4 date outside the week refused", lambda: new_entry(native.name, txn_date="2027-01-20"), "not in the week")
        check("L4b no date refused", lambda: new_entry(native.name, txn_date=None), "date is required")
        def edit_date_out():
            d = frappe.get_doc("Petty Cash Entry", e1.name); d.txn_date = "2027-01-02"; d.save()
        check("L4c editing the date out of the week refused", edit_date_out, "not in the week")
        other = new_sheet(1, week="2027-01-23")
        def edit_week():
            d = frappe.get_doc("Petty Cash Entry", e1.name); d.week_sheet = other.name; d.txn_date = "2027-01-19"; d.save()
        check("L6 changing the week by editing refused", edit_week, "Move to week")
        def edit_float():
            d = frappe.get_doc("Petty Cash Entry", e1.name); d.float = "Hauz-Pay"; d.save()
        check("L5b changing the float on the line refused", edit_float, "float is not changed")

        # ---- the shared lookup ----
        names = sorted([e1.name, e2.name, e3.name])
        got = weeks.week_entries(native.name)
        check("lookup returns the three native lines once each",
              lambda: sorted(r["name"] for r in got) == names and all(r["link_kind"] == "native" for r in got))
        check("lookup on the legacy sheet returns only its mirrored line",
              lambda: [r["name"] for r in weeks.week_entries(legacy.name)] == mirrored)
        check("posting reader sees the native lines", lambda: sorted(e["name"] for e in post._entries("2027-01-16", "Cash")) == names)
        check("weekpost reader sees the native lines", lambda: sorted(e["name"] for e in weekpost._entries("2027-01-16", "Cash")) == names)
        check("VAT reader sees the native lines", lambda: sorted(e["name"] for e in vat.vat_week("2027-01-16", "Cash")["entries"]) == names)
        check("reconcile totals see the native lines", lambda: reconcile._entry_totals(native.name) == (140.0, 250.0, 3))
        # the old filter, for the record: it could not see them
        check("old sheet_filters would have MISSED them (why the lookup exists)",
              lambda: frappe.get_all("Petty Cash Entry", filters=post.sheet_filters("2027-01-16", "Cash")[0]) == [])
        rep = weeks.coverage_report()
        check("partition holds with a mixed set: nothing twice, nothing missing",
              lambda: not rep["returned_twice"] and not rep["never_returned"]
              and rep["entries_returned"] == rep["entries_in_table"] and rep["by_kind"]["native"] == BASE["native"] + 3)
    finally:
        frappe.db.rollback(save_point="native_step2")
        frappe.db.rollback()

    left = frappe.db.count("Petty Cash Sheet", {"week_ending": (">=", "2027-01-01")}) + (frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")}) - BASE["native"])
    RESULTS.append(("rolled back: no test sheet or native entry left behind", left == 0, f"{left} left" if left else ""))
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} passed")
