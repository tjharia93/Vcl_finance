"""Step 6 for native lines: every report and read path, compared with a week that keeps
rows and holds THE SAME lines. Rolled back.

    bench --site <site> execute vcl_finance.petty_cash.tests.live_native_step6.run
"""
import io
import json

import frappe

from vcl_finance.petty_cash.tests.live_native_step3 import LINES

RESULTS = []
BASE = {}
CHAIN_LINES = {}
LEGACY_WEEK, NATIVE_WEEK = "2027-04-10", "2027-04-17"
LDAY, NDAY = "2027-04-06", "2027-04-13"


def check(name, fn, expect_error=None):
    try:
        out = fn()
        ok = expect_error is None and out is not False
        detail = "" if ok else ("expected refusal, got none" if expect_error else f"returned {out!r}"[:200])
    except Exception as e:      # noqa: BLE001
        msg = str(e) or type(e).__name__
        ok = expect_error is not None and expect_error.lower() in msg.lower()
        detail = "" if ok else f"{type(e).__name__}: {msg[:220]}"
    RESULTS.append((name, ok, detail))


def new_sheet(week, native=0):
    d = frappe.new_doc("Petty Cash Sheet")
    d.update({"week_ending": week, "float": "Cash", "custodian_name": "Test", "status": "Draft",
              "opening_balance": 10000, "native_lines": native})
    d.insert()
    return d.name


def xlsx_cells(content):
    """Every cell value of every tab, with the things that legitimately differ taken out."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content))
    out = {}
    for ws in wb.worksheets:
        rows = [[c for c in row] for row in ws.iter_rows(values_only=True)]
        # EXCLUDED from the cell-for-cell comparison, and checked on their own below: the
        # opening-balance chain lines on the Notes tab. They describe the NEIGHBOURING
        # week, and the two test weeks have different neighbours.
        chain = [c for r in rows for c in r if isinstance(c, str) and ("does NOT tie" in c or "does NOT carry" in c)]
        CHAIN_LINES.setdefault(ws.title, []).append(chain)
        out[ws.title] = [r for r in rows if not any(isinstance(c, str) and ("does NOT tie" in c or "does NOT carry" in c) for c in r)]
    return out


def scrub(obj, *words):
    s = json.dumps(obj, default=str, sort_keys=True)
    for a, b in words:
        s = s.replace(a, b)
    return s


def run():
    frappe.set_user("Administrator")
    # What is already on the site (a replayed Trial week may be kept there on purpose).
    BASE["native"] = frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")})
    # These checks run inside ONE transaction that is rolled back at the end, so the actions
    # must not start a transaction of their own (the separate-session runs test that path).
    frappe.flags.pc_keep_transaction = True
    from vcl_finance.petty_cash import api, control, reconcile, report_xlsx, vat, weekpost, resolve, entry_api, postings
    frappe.db.savepoint("native_step6")
    try:
        legacy, nat = new_sheet(LEGACY_WEEK), new_sheet(NATIVE_WEEK, 1)
        ids = {legacy: [], nat: []}
        for sheet, day in ((legacy, LDAY), (nat, NDAY)):
            for kind, fields in LINES:
                ids[sheet].append(api.quick_entry(sheet, kind, txn_date=day, **fields)["entry_id"])
            api.attach_receipt(sheet, row_name=ids[sheet][0], file_url="/files/slip.jpg")
            api.cancel_entry(sheet, ids[sheet][8], remark="keyed twice")          # the bike line
        # words that differ only because the two weeks are different weeks / sheets
        swap = [(legacy, "SHEET"), (nat, "SHEET"), (LEGACY_WEEK, "WEEK"), (NATIVE_WEEK, "WEEK"), (LDAY, "DAY"), (NDAY, "DAY")]
        swap.append(("2027-04-18", "D-6"))      # the day after the native week, as the day after the row week already maps
        for i in range(7):
            from datetime import date, timedelta
            a = date.fromisoformat(LEGACY_WEEK) - timedelta(days=i); b = date.fromisoformat(NATIVE_WEEK) - timedelta(days=i)
            swap += [(a.isoformat(), f"D-{i}"), (b.isoformat(), f"D-{i}"), (a.strftime("%d/%m/%Y"), f"d-{i}"), (b.strftime("%d/%m/%Y"), f"d-{i}"),
                     (a.strftime("%d-%m-%Y"), f"d-{i}"), (b.strftime("%d-%m-%Y"), f"d-{i}"), (a.strftime("%d %b"), f"db-{i}"), (b.strftime("%d %b"), f"db-{i}"),
                     (a.strftime("%d/%m"), f"dm-{i}"), (b.strftime("%d/%m"), f"dm-{i}")]
        L, N = frappe.get_doc("Petty Cash Sheet", legacy), frappe.get_doc("Petty Cash Sheet", nat)
        # the week NUMBER differs too (they are consecutive weeks)
        for d_ in (L, N):
            swap += [(f"Week {d_.week_no}", "Week N"), (f"Wk {d_.week_no}", "Wk N"), (f'"week_no": {d_.week_no}', '"week_no": N'),
                     (f" {d_.week_no} d-6", " N d-6")]

        # ---- the sheet document itself, as anything that loads it sees it ----
        count = lambda d: {t: sum(1 for r in d.get(t) if (r.get("amount") or r.get("amount_issued"))) for t in
                           ("vouchers", "wages_entries", "loan_entries", "misc_entries", "parking_entries")}
        check("a loaded native sheet shows the same rows per table as the row week (3/4/1/2/1)", lambda: count(N) == count(L) == {"vouchers": 3, "wages_entries": 4, "loan_entries": 1, "misc_entries": 2, "parking_entries": 1})
        check("...but stores none (0 child rows in the database)",
              lambda: sum(frappe.db.count(dt, {"parent": nat}) for dt in ("Petty Cash Voucher", "Petty Cash Wages Entry", "Petty Cash Loan Entry", "Petty Cash Misc Entry", "Petty Cash Parking Entry")) == 0)
        check("header totals equal (out, in, expected close)", lambda: (N.total_out, N.total_in, N.expected_close) == (L.total_out, L.total_in, L.expected_close))
        rest = frappe.get_doc("Petty Cash Sheet", nat).as_dict()
        check("a REST-style read of the sheet carries the rows, receipt and cancelled flag",
              lambda: sum(1 for r in rest["vouchers"] if r.get("amount")) == 3 and len(rest["vouchers"]) == 18 and any(r.get("receipt") == "/files/slip.jpg" for r in rest["vouchers"])
              and any(r.get("cancelled") for r in rest["misc_entries"]))

        # ---- saving the sheet: header yes, lines no ----
        def header_save():
            d = frappe.get_doc("Petty Cash Sheet", nat); d.notes = "header edit"; d.save(); return frappe.db.get_value("Petty Cash Sheet", nat, "notes") == "header edit"
        check("saving a header field of a native sheet works and writes no rows", lambda: header_save()
              and frappe.db.count("Petty Cash Voucher", {"parent": nat}) == 0 and len(frappe.get_all("Petty Cash Entry", {"week_sheet": nat})) == 11)
        def row_edit():
            d = frappe.get_doc("Petty Cash Sheet", nat); d.vouchers[0].amount = 1; d.save()
        check("changing a line by saving the sheet is refused, pointing to the entry page", row_edit, "Manage transactions from the entry page")
        def row_zero():   # what the old entry page did to 'delete'
            d = frappe.get_doc("Petty Cash Sheet", nat); d.update({"wages_entries": [{"name": ids[nat][3], "amount": 0}]}); d.save()
        check("the old delete-by-zero write is refused the same way", row_zero, "Manage transactions from the entry page")
        def row_drop():
            d = frappe.get_doc("Petty Cash Sheet", nat); d.set("vouchers", d.vouchers[1:]); d.save()
        check("removing a line by saving the sheet is refused", row_drop, "cannot be removed")
        def row_tick():
            d = frappe.get_doc("Petty Cash Sheet", nat); d.vouchers[0].reconciled = 1; d.save()
        check("ticking a line by saving the sheet is refused (the tick lives on the entry)", row_tick, "Manage transactions from the entry page")
        def row_add():
            d = frappe.get_doc("Petty Cash Sheet", nat); d.append("vouchers", {"txn_date": NDAY, "recipient": "X", "amount": 9, "category": "TE"}); d.save()
        check("adding a row by saving the sheet is refused", row_add, "cannot be added as a row")
        check("after all those refusals the week is unchanged", lambda: frappe.db.get_value("Petty Cash Sheet", nat, "total_out") == L.total_out
              and len(frappe.get_all("Petty Cash Entry", {"week_sheet": nat})) == 11)

        # ---- summary, feed, week status ----
        money = lambda d: {k: v for k, v in d.items() if k not in ("native", "lines_revision", "cash_reconfirm_needed", "balance_label")}
        check("summary equal", lambda: money(api.get_feed(legacy)["summary"]) == money(api.get_feed(nat)["summary"]))
        strip = lambda it: {k: v for k, v in it.items() if k not in ("id", "row_idx", "date", "day_idx")}
        check("feed equal (11 items incl. the cancelled one and the receipt)",
              lambda: sorted(scrub(strip(i)) for i in api.get_feed(legacy)["items"]) == sorted(scrub(strip(i)) for i in api.get_feed(nat)["items"]))
        check("week status equal", lambda: scrub(api.week_status("Cash", LDAY), *swap) == scrub(api.week_status("Cash", NDAY), *swap))

        # ---- reports ----
        rl, rn = api.range_report("2027-04-04", LEGACY_WEEK, float="Cash"), api.range_report("2027-04-11", NATIVE_WEEK, float="Cash")
        check("date-range report equal (every category and week total)", lambda: scrub(rl, *swap) == scrub(rn, *swap))
        frappe.local.response = frappe._dict()
        api.range_report_xlsx("2027-04-04", LEGACY_WEEK, float="Cash"); xl = xlsx_cells(frappe.local.response.filecontent)
        frappe.local.response = frappe._dict()
        api.range_report_xlsx("2027-04-11", NATIVE_WEEK, float="Cash"); xn = xlsx_cells(frappe.local.response.filecontent)
        check("Excel report: same tabs", lambda: list(xl) == list(xn))
        check("Excel report: every cell on every tab equal, after mapping sheet number, week dates and week number; "
              "opening-chain lines excluded", lambda: scrub(xl, *swap) == scrub(xn, *swap))
        # ...and the excluded lines, on their own. The native week follows the row week, so
        # its chain line must state the row week's close and the right gap; the row week's
        # must state that its close does not carry into the native week's opening.
        notes_l, notes_n = CHAIN_LINES["Notes & checks"][0], CHAIN_LINES["Notes & checks"][1]
        close_l = L.expected_close
        gap = 10000 - close_l
        check(f"excluded line checked separately: native week's opening-chain note states the row week's close ({close_l:,.0f}) and gap ({gap:,.0f})",
              lambda: len(notes_n) == 1 and legacy in notes_n[0] and f"close {close_l:,.0f}" in notes_n[0] and f"gap {gap:,.0f}" in notes_n[0])
        check("excluded line checked separately: row week's note states its close does not carry into the native week's opening, same gap",
              lambda: any(nat in c and "does NOT carry" in c and f"gap {gap:,.0f}" in c for c in notes_l))
        check("no other line was excluded: 2 chain lines on the row week (previous and next week), 1 on the native week",
              lambda: (len(notes_l), len(notes_n)) == (2, 1) and all(not v[0] and not v[1] for k, v in CHAIN_LINES.items() if k != "Notes & checks"))
        check("control totals check equal", lambda: scrub(control.control_check(legacy), *swap) == scrub(control.control_check(nat), *swap))
        check("reconcile check: entries agree with the sheet on both weeks",
              lambda: scrub(reconcile.reconcile_sheet(legacy), *swap) == scrub(reconcile.reconcile_sheet(nat), *swap))

        # ---- print: the web print page and the desk print format ----
        def print_ctx(sheet):
            from vcl_finance.www import __init__ as _  # noqa: F401
            import importlib
            mod = importlib.import_module("vcl_finance.www.petty-cash.print")
            frappe.local.form_dict = frappe._dict(name=sheet, copies=1)
            ctx = frappe._dict(); mod.get_context(ctx)
            import re
            keep = {k: v for k, v in ctx.items() if k not in ("doc", "sheet", "title")}
            flat = lambda v: v.as_dict() if hasattr(v, "as_dict") else v
            def deep(v):
                v = flat(v)
                if isinstance(v, dict):
                    return {k: deep(x) for k, x in v.items() if k not in ("name", "parent", "creation", "modified", "owner", "modified_by", "idx", "docstatus", "parentfield", "parenttype", "doctype", "locked_on", "cancelled_on", "__unsaved", "__islocal")}
                if isinstance(v, (list, tuple)):
                    return [deep(x) for x in v]
                return v
            return re.sub(r"PC[SE]-\d{4}-\d+", "ID", scrub(deep(keep), *swap))
        def same(a, b):
            if a == b:
                return True
            i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            raise AssertionError(f"differs at {i}: ...{a[max(0, i - 70):i + 70]!r} VS ...{b[max(0, i - 70):i + 70]!r}")
        check("web print page: same context (vouchers, wages, loans, misc, parking, totals)", lambda: same(print_ctx(legacy), print_ctx(nat)))
        def desk_print(sheet):
            html = frappe.get_print("Petty Cash Sheet", sheet, print_format="Petty Cash Weekly Sheet")
            import re
            text = re.sub(r"<[^>]+>", " ", html); text = re.sub(r"\s+", " ", text)
            for a, b in swap: text = text.replace(a, b)
            return re.sub(r"PC[SE]-\d{4}-\d+", "ID", text)
        # The cumulative loans ledger legitimately differs: the native week is the later
        # of the two, so its ledger also carries the row week's loan. Compare everything
        # else, then test the ledger on its own.
        import re as _re
        no_ledger = lambda t_: _re.sub(r"FULL LOAN LIST.*?NOTE:", "LEDGER", t_, flags=_re.S)
        loans = lambda t_: int(_re.search(r"(\d+) loans", t_).group(1))
        dl, dn = desk_print(legacy), desk_print(nat)
        check("desk print format renders the native week with the same content (ledger aside)", lambda: same(no_ledger(dl), no_ledger(dn)))
        check("loans ledger on the native week lists its own loan and the earlier week's (one more than the row week)",
              lambda: loans(dn) == loans(dl) + 1 and "Kamau" in dn)
        total = lambda t_: int(_re.search(r"RUNNING TOTAL &rarr; ([\d,]+)", t_).group(1).replace(",", ""))
        check("excluded section checked separately: the ledger's running total on the native week is the row week's plus this week's 2,000 loan",
              lambda: total(dn) == total(dl) + 2000)
        check("the excluded section is only the ledger (from its heading to the note under it), present once in each print",
              lambda: dl.count("FULL LOAN LIST") == dn.count("FULL LOAN LIST") == 1)
        later = new_sheet("2027-04-24")
        check("a LATER week's ledger still carries the loan keyed on the native week",
              lambda: loans(desk_print(later)) == loans(dn))

        # ---- the review screens' data ----
        names = lambda rows: sorted(shape_of(r) for r in rows)
        def shape_of(r):
            e = frappe.db.get_value("Petty Cash Entry", r["name"], ["source_type", "source_key", "amount", "cash_in", "cancelled"], as_dict=True)
            return (e.source_type, e.source_key or "", float(e.amount), int(e.cash_in), int(e.cancelled))
        check("VAT screen lists the same lines", lambda: names(vat.vat_week(LEGACY_WEEK, "Cash")["entries"]) == names(vat.vat_week(NATIVE_WEEK, "Cash")["entries"]))
        check("posting preview reads the same lines", lambda: names(weekpost._entries(LEGACY_WEEK, "Cash")) == names(weekpost._entries(NATIVE_WEEK, "Cash")))
        rt = lambda w: sorted((v.get("outcome"), v.get("erp_account") or "") for v in resolve.routes_for_week(w, "Cash")["routes"].values())
        check("route column gives the same routes", lambda: rt(LEGACY_WEEK) == rt(NATIVE_WEEK))
        pend = entry_api.pending_entries(limit=5000)
        got = {e["name"] for e in pend["lines"]}
        live_n = [i for k, i in enumerate(ids[nat]) if k != 8]
        check("approvals queue contains every live native line", lambda: set(live_n) <= got)
        check("approvals queue does not contain the cancelled line", lambda: ids[nat][8] not in got)
        un = postings.unposted_lines(limit=5000)
        check("unposted-lines list runs with native lines present", lambda: un is not None)

        # ---- pages ----
        def page(path_mod, sheet):
            import importlib
            mod = importlib.import_module(path_mod)
            frappe.local.form_dict = frappe._dict(name=sheet)
            ctx = frappe._dict(); mod.get_context(ctx); return ctx
        c = page("vcl_finance.www.petty-cash.sheet", nat)
        check("week page: native, 11 lines listed, cancelled one marked with its reason, receipt linked",
              lambda: c.native and len(c.lines) == 11 and any(l["status"] == "Cancelled: keyed twice" for l in c.lines)
              and any(l["receipt"] == "/files/slip.jpg" for l in c.lines))
        check("week page for the row week is still the editable grid", lambda: not page("vcl_finance.www.petty-cash.sheet", legacy).native)
        try:
            import os
            src = open(os.path.join(frappe.get_app_path("vcl_finance"), "www", "petty-cash", "sheet.html")).read()
            body = src.split("{% block page_content %}", 1)[1].rsplit("{% endblock %}", 1)[0]
            # Rendered with the SITE's own template environment, not with full access: a page
            # may only call what a web page is allowed to call.
            tpl = frappe.render_template(body, dict(c, title="t"))
        except Exception as ex:      # noqa: BLE001
            tpl = f"RENDER FAILED {ex}"
        check("week page shows the notice and links, and no editing controls",
              lambda: "Manage transactions from the entry page." in tpl and "Open entry" in tpl and "Add entry" in tpl
              and 'name="amount"' not in tpl and "data-pc-editor" not in tpl and "Auto-saves" not in tpl)
        check("week page, no opening count yet: Finance is offered 'Record opening count' first, not Close week",
              lambda: 'data-pc-week="opening"' in tpl and 'data-pc-week="close"' not in tpl and "No opening actual balance recorded yet" in tpl)
        api.set_opening_count(nat, 10000)
        c2 = page("vcl_finance.www.petty-cash.sheet", nat)
        tpl2 = frappe.render_template(body, dict(c2, title="t"))
        check("week page, opening balance recorded: book, actual and historical figures are shown apart; Close week asks for the supplied balance by component and for both dates",
              lambda: all(x in tpl2 for x in ("Book cash balance", "Actual balance, this week", "Historical difference carried forward", "Book against actual",
                                              "Corporate M-Pesa", "Personal M-Pesa", "Balance is as at the opening of", "Received and reviewed on"))
              and 'data-pc-week="close"' in tpl2 and "physical" not in tpl2.lower())
        e = page("vcl_finance.www.petty-cash.entry", nat)
        check("entry page loads the native week without writing a blank grid",
              lambda: json.loads(e.wz_data)["summary"]["native"] is True and frappe.db.count("Petty Cash Voucher", {"parent": nat}) == 0)
    finally:
        frappe.db.rollback(save_point="native_step6")
        frappe.db.rollback()

    left = frappe.db.count("Petty Cash Sheet", {"week_ending": (">=", "2027-01-01")}) + (frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")}) - BASE["native"])
    RESULTS.append(("rolled back: nothing left behind", left == 0, f"{left} left" if left else ""))
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} passed")
