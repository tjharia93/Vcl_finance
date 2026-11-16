"""Step 3 checks for native lines: keying, receipts, cancel/reinstate and moves, through
the ACTUAL whitelisted server actions, on a real site, ROLLED BACK.

    bench --site <site> execute vcl_finance.petty_cash.tests.live_native_step3.run

The core proof: the same lines are keyed with ``api.quick_entry`` on a week that keeps
rows and on a native week, and the resulting entries, summary and feed are compared.
"""
import json

import frappe

RESULTS = []
BASE = {}
LEGACY_WEEK, NATIVE_WEEK, NATIVE_WEEK_2, LEGACY_WEEK_2 = "2027-01-09", "2027-01-16", "2027-01-23", "2027-01-30"

# kind, fields (date filled per week)
LINES = [
    ("voucher", {"category": "TE", "amount": 1200, "recipient": "Wanjiru", "voucher_no": "V001", "pc_received": 1, "notes": "town run"}),
    ("voucher", {"category": "SE", "amount": 3450.5, "recipient": "Hardware", "voucher_no": "V002", "etr_received": 1}),
    ("voucher", {"cash_in": 1, "amount": 50000, "recipient": "Top up", "voucher_no": "V003"}),
    ("wage", {"amount": 800, "recipient": "Otieno", "staff_id": "C014", "reason": "casual day"}),
    ("overtime", {"amount": 600, "recipient": "Achieng", "reason": "late shift", "paye": 1}),
    ("commission", {"amount": 450, "recipient": "Mwangi"}),
    ("piecework", {"amount": 9100, "recipient": "Gang B", "reason": "sets"}),
    ("loan", {"amount": 2000, "recipient": "Kamau", "staff_id": "E101", "reason": "advance"}),
    ("bike", {"amount": 500, "notes": "KMFX fuel"}),
    ("forklift", {"amount": 3700, "notes": "gas"}),
    ("parking", {"amount": 300, "vehicle": "KAP 466"}),
]
DAYS = {LEGACY_WEEK: "2027-01-05", NATIVE_WEEK: "2027-01-12", NATIVE_WEEK_2: "2027-01-19", LEGACY_WEEK_2: "2027-01-26"}


def check(name, fn, expect_error=None):
    try:
        out = fn()
        ok = expect_error is None and out is not False
        detail = "" if ok else ("expected refusal, got none" if expect_error else f"returned {out!r}")
    except Exception as e:      # noqa: BLE001
        msg = str(e)
        ok = expect_error is not None and expect_error.lower() in msg.lower()
        detail = "" if ok else f"{type(e).__name__}: {msg[:200]}"
    RESULTS.append((name, ok, detail))
    return ok


def new_sheet(week, native=0):
    d = frappe.new_doc("Petty Cash Sheet")
    d.week_ending, d.float, d.custodian_name, d.status = week, "Cash", "Test", "Draft"
    d.opening_balance, d.native_lines = 10000, native
    d.insert()
    return d.name


def shape(e):
    """What an entry IS, leaving out the things that legitimately differ by model."""
    p = json.loads(e.payload or "{}")
    for k in ("row_idx", "locked", "locked_by", "day_idx"):
        p.pop(k, None)
    return (e.source_type, e.source_key or "", e.category or "", e.recipient or "", float(e.amount),
            int(e.cash_in or 0), int(e.pc_received or 0), int(e.etr_received or 0), e.notes or "",
            e.status, int(e.cancelled or 0), e.company, e.float, json.dumps(p, sort_keys=True))


def entries_of(sheet):
    from vcl_finance.petty_cash import weeks
    names = [r["name"] for r in weeks.week_entries(sheet)]
    return [frappe.get_doc("Petty Cash Entry", n) for n in names]


def head(sheet):
    return frappe.db.get_value("Petty Cash Sheet", sheet, ["total_out", "total_in", "expected_close", "lines_revision"], as_dict=True)


def run():
    frappe.set_user("Administrator")
    # What is already on the site (a replayed Trial week may be kept there on purpose).
    BASE["native"] = frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")})
    # These checks run inside ONE transaction that is rolled back at the end, so the actions
    # must not start a transaction of their own (the separate-session runs test that path).
    frappe.flags.pc_keep_transaction = True
    from vcl_finance.petty_cash import api, entry_api, weeks, weekpost, native
    frappe.db.savepoint("native_step3")
    try:
        legacy, nat = new_sheet(LEGACY_WEEK), new_sheet(NATIVE_WEEK, 1)

        # ---- keying through api.quick_entry, both models ----
        ids = {legacy: [], nat: []}
        for sheet, week in ((legacy, LEGACY_WEEK), (nat, NATIVE_WEEK)):
            for kind, fields in LINES:
                r = api.quick_entry(sheet, kind, txn_date=DAYS[week], **fields)
                ids[sheet].append(r["entry_id"])
        check("11 lines keyed on each week through quick_entry", lambda: len(ids[nat]) == 11 and len(ids[legacy]) == 11)
        check("native ids are Petty Cash Entry names", lambda: all(frappe.db.exists("Petty Cash Entry", i) for i in ids[nat]))
        check("native week has no rows on the sheet",
              lambda: frappe.db.count("Petty Cash Voucher", {"parent": nat}) == 0 and frappe.db.count("Petty Cash Wages Entry", {"parent": nat}) == 0)
        le, ne = entries_of(legacy), entries_of(nat)
        check("each native entry equals the mirrored entry for the same line (11 of 11)",
              lambda: sorted(shape(e) for e in le) == sorted(shape(e) for e in ne))
        check("every native entry is Native, linked by week_sheet only",
              lambda: all(e.sync_state == "Native" and e.week_sheet == nat and not e.origin_sheet for e in ne))

        sl, sn = api._summary(frappe.get_doc("Petty Cash Sheet", legacy)), api._summary(frappe.get_doc("Petty Cash Sheet", nat))
        money = lambda d: {k: v for k, v in d.items() if k not in ("native", "lines_revision", "cash_reconfirm_needed", "balance_label")}
        check("summary identical on both weeks (every category and total)", lambda: money(sl) == money(sn))
        fl = api.get_feed(legacy)["items"]; fn = api.get_feed(nat)["items"]
        strip = lambda it: {k: v for k, v in it.items() if k not in ("id", "row_idx", "date", "day_idx")}
        key = lambda it: json.dumps(it, sort_keys=True, default=str)
        check("feed identical on both weeks (11 items, same labels and amounts)",
              lambda: len(fn) == 11 and sorted(key(strip(i)) for i in fl) == sorted(key(strip(i)) for i in fn))
        hl, hn = head(legacy), head(nat)
        check("stored totals equal on both weeks and equal the entries",
              lambda: (hl.total_out, hl.total_in, hl.expected_close) == (hn.total_out, hn.total_in, hn.expected_close) == (22100.5, 50000, 37899.5))
        check("revision moved once per line keyed (11)", lambda: hn.lines_revision == 11)

        # ---- the same refusals as the row path ----
        check("voucher without a category refused", lambda: api.quick_entry(nat, "voucher", txn_date=DAYS[NATIVE_WEEK], amount=5, pc_received=1), "spend category")
        check("spend voucher without PC or ETR refused", lambda: api.quick_entry(nat, "voucher", txn_date=DAYS[NATIVE_WEEK], amount=5, category="TE"), "Tick PC or ETR")
        check("parking without a vehicle refused", lambda: api.quick_entry(nat, "parking", txn_date=DAYS[NATIVE_WEEK], amount=5), "vehicle")
        check("line without a date refused", lambda: api.quick_entry(nat, "bike", amount=5), "date is required")
        check("refused lines left nothing behind", lambda: len(entries_of(nat)) == 11 and head(nat).lines_revision == 11)

        # ---- receipt ----
        v1 = ids[nat][0]
        api.attach_receipt(nat, row_name=v1, file_url="/files/test-slip.jpg")
        e = frappe.get_doc("Petty Cash Entry", v1)
        check("receipt attached to the same entry", lambda: e.receipt == "/files/test-slip.jpg" and e.name == v1)
        check("receipt moved the revision, not the totals", lambda: head(nat).lines_revision == 12 and head(nat).total_out == 22100.5)
        check("receipt shows in the feed", lambda: [i for i in api.get_feed(nat)["items"] if i["id"] == v1][0]["receipt"] == "/files/test-slip.jpg")

        # ---- cancel / reinstate ----
        r = api.cancel_entry(nat, v1, remark="keyed twice")
        e.reload()
        check("cancel voids the same entry (Void, cancelled, remark, stamp)",
              lambda: e.name == v1 and e.status == "Void" and e.cancelled == 1 and e.cancel_remark == "keyed twice" and e.cancelled_on)
        check("cancelled line stops counting (out 22,100.50 -> 20,900.50)",
              lambda: r["summary"]["total_out"] == 20900.5 and head(nat).total_out == 20900.5 and head(nat).expected_close == 39099.5)
        check("cancelled line stays visible in the feed, marked cancelled",
              lambda: [i for i in api.get_feed(nat)["items"] if i["id"] == v1][0]["cancelled"] is True)
        rows, buckets = weekpost._survey(NATIVE_WEEK, "Cash")
        ready = {x["name"] for x in buckets["ready"]} if "ready" in buckets else {x["name"] for k, v in buckets.items() if "READY" in str(k).upper() for x in v}
        check("cancelled line is not ready to post", lambda: v1 not in ready)
        r = api.reinstate_entry(nat, v1)
        e.reload()
        check("reinstate brings the same entry back, unsigned", lambda: e.name == v1 and e.status == "Unapproved" and e.cancelled == 0)
        check("reinstated line counts again", lambda: r["summary"]["total_out"] == 22100.5 and head(nat).total_out == 22100.5)
        check("the receipt survived cancel and reinstate", lambda: e.receipt == "/files/test-slip.jpg")

        # ---- date routing at keying ----
        r = api.quick_entry(nat, "bike", txn_date="2027-02-02", amount=111, notes="next week")
        routed = r["sheet"]
        # The week the system opens is kept as entries when the site says so
        # (``petty_cash_native_from``), and keeps rows when it does not.
        from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import kept_as_entries
        auto = kept_as_entries("2027-02-02", "Cash")
        link = "week_sheet" if auto else "origin_sheet"
        check("a line dated into a week with no sheet is routed there (the system opens the week, "
              + ("kept as entries" if auto else "keeping rows") + ")",
              lambda: r["rerouted"] and routed != nat
              and bool(frappe.db.get_value("Petty Cash Sheet", routed, "native_lines")) == auto
              and frappe.db.count("Petty Cash Entry", {link: routed}) == 1 and head(nat).total_out == 22100.5)

        # ---- moves ----
        nat2, legacy2 = new_sheet(NATIVE_WEEK_2, 1), new_sheet(LEGACY_WEEK_2)
        mv = ids[nat][3]                       # the wage, 800
        before = frappe.get_doc("Petty Cash Entry", mv)
        api.attach_receipt(nat, row_name=mv, file_url="/files/wage-sheet.jpg")
        r = api.move_entry_to_week(mv, DAYS[NATIVE_WEEK_2])
        m = frappe.get_doc("Petty Cash Entry", mv)
        check("move native -> native keeps the same entry", lambda: r["moved"] and m.name == mv and m.week_sheet == nat2 and str(m.week_ending) == NATIVE_WEEK_2 and m.sync_state == "Native")
        check("  both weeks' totals follow (22,100.50 - 800 / 800)", lambda: head(nat).total_out == 21300.5 and head(nat2).total_out == 800)
        check("  receipt and creation date preserved", lambda: m.receipt == "/files/wage-sheet.jpg" and m.creation == before.creation)

        r = api.move_entry_to_week(mv, DAYS[LEGACY_WEEK_2])
        m.reload()
        check("move native -> week that keeps rows keeps the same entry, now mirrored",
              lambda: m.name == mv and m.sync_state == "Mirrored" and m.origin_sheet == legacy2 and not m.week_sheet and r["target_keeps"] == "rows")
        check("  exactly one row and one entry on the target, nothing orphaned",
              lambda: frappe.db.count("Petty Cash Wages Entry", {"parent": legacy2, "amount": 800}) == 1
              and frappe.get_all("Petty Cash Entry", {"origin_sheet": legacy2}, pluck="sync_state") == ["Mirrored"])
        check("  totals follow (800 left the native week, arrived on the row week)", lambda: head(nat2).total_out == 0 and head(legacy2).total_out == 800)
        check("  receipt preserved, on the entry and on the row",
              lambda: m.receipt == "/files/wage-sheet.jpg" and frappe.db.get_value("Petty Cash Wages Entry", m.origin_row, "receipt") == "/files/wage-sheet.jpg")
        d = frappe.get_doc("Petty Cash Sheet", legacy2); d.save(); d.save()
        check("  saving that sheet twice more changes nothing",
              lambda: frappe.get_all("Petty Cash Entry", {"origin_sheet": legacy2}, pluck="name") == [mv] and head(legacy2).total_out == 800)

        r = api.move_entry_to_week(mv, DAYS[NATIVE_WEEK])
        m.reload()
        check("move week that keeps rows -> native keeps the same entry, now native",
              lambda: m.name == mv and m.sync_state == "Native" and m.week_sheet == nat and not m.origin_sheet and m.entry_kind == "wage")
        check("  its row is gone and nothing is orphaned",
              lambda: frappe.db.count("Petty Cash Wages Entry", {"parent": legacy2, "amount": 800}) == 0
              and frappe.db.count("Petty Cash Entry", {"origin_sheet": legacy2}) == 0)
        check("  totals back where they started (22,100.50 / 0)", lambda: head(nat).total_out == 22100.5 and head(legacy2).total_out == 0)
        check("  receipt still attached after three moves", lambda: m.receipt == "/files/wage-sheet.jpg")
        check("  the entry is the one originally keyed (same name, same creation)", lambda: m.name == mv and m.creation == before.creation)

        r = api.move_entry_to_week(mv, "2027-01-14")
        m.reload()
        check("a date change inside the same week is just a date change", lambda: not r["moved"] and str(m.txn_date) == "2027-01-14" and m.week_sheet == nat)

        # ---- what a move refuses ----
        entry_api.approve_entry(ids[nat][1])
        check("a signed line cannot be moved", lambda: api.move_entry_to_week(ids[nat][1], DAYS[NATIVE_WEEK_2]), "signed off")
        api.cancel_entry(nat, ids[nat][8])
        check("a voided line cannot be moved", lambda: api.move_entry_to_week(ids[nat][8], DAYS[NATIVE_WEEK_2]), "voided")
        frappe.db.set_value("Petty Cash Sheet", nat2, "status", "Approved")
        check("a line cannot be moved into an approved week", lambda: api.move_entry_to_week(ids[nat][4], DAYS[NATIVE_WEEK_2]), "approved")
        check("a line cannot be keyed into an approved week", lambda: api.quick_entry(nat2, "bike", txn_date=DAYS[NATIVE_WEEK_2], amount=5), "closed")
        frappe.db.set_value("Petty Cash Sheet", nat2, "status", "Draft")
        frappe.db.set_value("Petty Cash Sheet", nat, "status", "Approved")
        check("a line cannot be cancelled on an approved week", lambda: api.cancel_entry(nat, ids[nat][5]), "closed")
        def direct_edit():
            d = frappe.get_doc("Petty Cash Entry", ids[nat][5]); d.amount = 1; d.save()
        check("a direct edit of a line on an approved week is refused by the entry itself", direct_edit, "approved")
        frappe.db.set_value("Petty Cash Sheet", nat, "status", "Draft")

        # ---- exclusions and coverage ----
        live = weeks.week_entries(nat, fields=["name", "cancelled", "status"], filters={"cancelled": 0})
        check("voided line is excluded from the live set used for totals", lambda: ids[nat][8] not in {x["name"] for x in live})
        check("stored totals equal a fresh sum of the entries", lambda: native.totals(nat, 10000)["total_out"] == head(nat).total_out)
        rep = weeks.coverage_report()
        check("lookup still returns every entry exactly once", lambda: not rep["returned_twice"] and not rep["never_returned"] and rep["entries_returned"] == rep["entries_in_table"])
        new_orphans = frappe.db.count("Petty Cash Entry", {"sync_state": "Orphaned"})
        check("no orphan was created by any of this (still 1,276)", lambda: new_orphans == 1276)
    finally:
        frappe.db.rollback(save_point="native_step3")
        frappe.db.rollback()

    left = frappe.db.count("Petty Cash Sheet", {"week_ending": (">=", "2027-01-01")}) + (frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")}) - BASE["native"])
    RESULTS.append(("rolled back: nothing left behind", left == 0, f"{left} left" if left else ""))
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} passed")
