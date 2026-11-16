"""Step 7 for native lines: posting. Two companies, repeat requests, a failure halfway,
the mixed-history ERPNext/QuickBooks entry-set comparison, and reopening with drafts.
Rolled back (the posting functions commit, so ``frappe.db.commit`` is held off here).

    bench --site <site> execute vcl_finance.petty_cash.tests.live_native_step7.run
"""
import frappe

RESULTS = []
BASE = {}
WEEK, DAY = "2027-05-15", "2027-05-11"
PRIOR_WEEK, PRIOR_DAY = "2027-05-08", "2027-05-04"
VCL, BVL = "Vimit Converters Limited", "BAHATI VENTURES LIMITED"


def check(name, fn, expect_error=None):
    try:
        out = fn()
        ok = expect_error is None and out is not False
        detail = "" if ok else ("expected refusal, got none" if expect_error else f"returned {out!r}"[:220])
    except Exception as e:      # noqa: BLE001
        msg = str(e) or type(e).__name__
        ok = expect_error is not None and expect_error.lower() in msg.lower()
        detail = "" if ok else f"{type(e).__name__}: {msg[:260]}"
    RESULTS.append((name, ok, detail))


def rev(sheet):
    return frappe.db.get_value("Petty Cash Sheet", sheet, "lines_revision")


def journals(tag):
    return frappe.get_all("Journal Entry", {"cheque_no": ("like", tag + "%")}, ["name", "company", "docstatus", "total_debit", "total_credit"], order_by="company")


def run():
    frappe.set_user("Administrator")
    # What is already on the site (a replayed Trial week may be kept there on purpose).
    BASE["native"] = frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")})
    # These checks run inside ONE transaction that is rolled back at the end, so the actions
    # must not start a transaction of their own (the separate-session runs test that path).
    frappe.flags.pc_keep_transaction = True
    from vcl_finance.petty_cash import api, entry_api, vat, weekpost, weeks, post, qbo
    from vcl_finance.petty_cash import weekpost_core as C
    real_commit = frappe.db.commit
    frappe.db.commit = lambda *a, **k: None
    frappe.db.savepoint("native_step7")
    try:
        def week(week_ending, native):
            s = frappe.new_doc("Petty Cash Sheet")
            s.update({"week_ending": week_ending, "float": "Cash", "custodian_name": "Test", "status": "Draft",
                      "opening_balance": 20000, "native_lines": native})
            s.insert()
            if native:
                api.set_opening_count(s.name, 20000)
            return s.name
        sheet = week(WEEK, 1)
        q = lambda kind, **f: api.quick_entry(sheet, kind, txn_date=DAY, **f)["entry_id"]
        te = q("voucher", category="TE", amount=1000, recipient="Wanjiru", voucher_no="V1", pc_received=1)
        se = q("voucher", category="SE", amount=2320, recipient="Hardware", voucher_no="V2", etr_received=1)
        fk = q("forklift", amount=3700, notes="gas")
        pw = q("piecework", amount=5000, recipient="Gang B", reason="sets")
        void = q("bike", amount=500, notes="keyed twice")
        api.cancel_entry(sheet, void, remark="keyed twice")
        d = frappe.get_doc("Petty Cash Entry", pw); d.company = BVL; d.save()        # the approver's decision, on the line
        live = [te, se, fk, pw]
        for e in live:
            if frappe.db.get_value("Petty Cash Entry", e, "source_type") != "Wages Entry":   # wages never carry VAT
                vat.set_vat(e, "No VAT")
            entry_api.approve_entry(e)

        # ---- gates ----
        check("posting refused before the week is approved", lambda: weekpost.post_erp(WEEK, "Cash"), "The week is approved")
        # ---- the cash count: a real figure, and an exception stays an exception ----
        check("a negative cash count is refused (a count is what is in the tin)", lambda: api.close_week(sheet, -50, revision=rev(sheet)), "cannot be negative")
        api.close_week(sheet, 8000, revision=rev(sheet))            # expected 7,980: the count is 20 over
        hd = lambda: frappe.db.get_value("Petty Cash Sheet", sheet, ["cash_count_end", "expected_close", "variance", "cash_reconfirm_needed", "cash_confirmed_by"], as_dict=True)
        check("a count that does not agree with the book is recorded as counted, with its variance (8,000 against 7,980 = 20)",
              lambda: (hd().cash_count_end, hd().expected_close, hd().variance, hd().cash_reconfirm_needed) == (8000, 7980, 20, 1))
        check("the week cannot be approved while that variance is unconfirmed", lambda: api.approve_week(sheet, revision=rev(sheet)), "Acknowledge it")
        check("acknowledging without a reason is refused", lambda: api.confirm_cash(sheet, revision=rev(sheet)), "Reason")
        api.confirm_cash(sheet, revision=rev(sheet), reason="20 over: float topped up from the till", evidence="till slip 4471")
        check("Finance confirms it: who and when recorded; the count itself untouched", lambda: hd().cash_confirmed_by == "Administrator" and hd().cash_count_end == 8000 and hd().variance == 20)
        api.approve_week(sheet, revision=rev(sheet))
        pv = weekpost.preview(WEEK, "Cash")
        check("preview lists the 4 live lines as ready, and not the cancelled one",
              lambda: sorted(x["name"] for x in weekpost._survey(WEEK, "Cash")[1]["ready"]) == sorted(live))

        # ---- a failure halfway leaves nothing ----
        calls = {"n": 0}
        real_build = C.build_erp_lines
        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure on the second company")
            return real_build(*a, **k)
        C.build_erp_lines = boom
        check("a failure on the second company's journal stops the run", lambda: weekpost.post_erp(WEEK, "Cash"), "simulated failure")
        C.build_erp_lines = real_build
        tag = f"PC-Cash-{WEEK}"
        check("...and leaves NOTHING: no journal for either company, no line linked, no posting row",
              lambda: journals(tag) == [] and not any(frappe.db.get_value("Petty Cash Entry", e, "journal_entry") for e in live)
              and len(weekpost._stored_postings(sheet)) == 0)
        check("...and the week records the error", lambda: "simulated failure" in (frappe.db.get_value("Petty Cash Sheet", sheet, "last_posting_error") or ""))

        # ---- two companies ----
        out = weekpost.post_erp(WEEK, "Cash")
        js = journals(tag)
        check("posting raises one DRAFT journal per company (2)", lambda: len(out["created"]) == 2 and [j.company for j in js] == [BVL, VCL] and all(j.docstatus == 0 for j in js))
        check("each draft balances (debits = credits)", lambda: all(round(j.total_debit, 2) == round(j.total_credit, 2) and j.total_debit > 0 for j in js))
        by = {j.company: j for j in js}
        check("amounts: Bahati 5,000 (piecework), Vimit 7,020 (TE 1,000 + SE 2,320 + forklift 3,700)",
              lambda: by[BVL].total_debit == 5000 and by[VCL].total_debit == 7020)
        link = lambda e: frappe.db.get_value("Petty Cash Entry", e, "journal_entry")
        check("each line points at ITS company's journal", lambda: link(pw) == by[BVL].name and {link(te), link(se), link(fk)} == {by[VCL].name})
        check("the cancelled line is on no journal", lambda: not link(void))
        res = weekpost.posting_results(sheet)
        check("the week records BOTH companies' journals, each Draft; none hidden",
              lambda: {(r["company"], r["journal_entry"], r["journal_state"]) for r in res["companies"]} == {(BVL, by[BVL].name, "Draft"), (VCL, by[VCL].name, "Draft")}
              and res["companies_without_record"] == [] and not res["last_error"])
        check("the sheet's single journal link is left empty rather than showing only one company",
              lambda: not frappe.db.get_value("Petty Cash Sheet", sheet, "journal_entry"))

        # ---- repeat requests ----
        again = weekpost.post_erp(WEEK, "Cash")
        check("a second posting request makes nothing new and says so",
              lambda: again["created"] == [] and again.get("already_posted") and [j.name for j in journals(tag)] == [j.name for j in js]
              and len(weekpost._stored_postings(sheet)) == 2)
        weekpost.post_erp(WEEK, "Cash")
        check("a third request: still two journals, same names, same totals",
              lambda: [(j.name, j.total_debit) for j in journals(tag)] == [(j.name, j.total_debit) for j in js])

        # ---- a line on a draft journal ----
        frappe.db.set_value("Petty Cash Sheet", sheet, "status", "Closed")
        def touch():
            x = frappe.get_doc("Petty Cash Entry", te); x.supplier_ref = "X"; x.save()
        check("a line on a draft journal cannot be changed; the message points to reopening the week", touch, "Reopen the week to discard the draft")
        frappe.db.set_value("Petty Cash Sheet", sheet, "status", "Approved")

        # ---- mixed history: an old entry on ANOTHER sheet, dated into this week ----
        prior = week(PRIOR_WEEK, 0)
        old = api.quick_entry(prior, "voucher", txn_date=PRIOR_DAY, category="TE", amount=777, recipient="Old line", voucher_no="OLD", pc_received=1)["entry_id"]
        foreign = frappe.db.get_value("Petty Cash Entry", {"origin_row": old}, "name")
        frappe.db.set_value("Petty Cash Entry", foreign, {"week_ending": WEEK, "status": "Approved", "vat_treatment": "No VAT"})   # as the 523 are
        erp_set = {e["name"] for e in weekpost._entries(WEEK, "Cash")}
        check("ERPNext's set for the native week does NOT pull in the old entry dated into it", lambda: foreign not in erp_set and set(live) <= erp_set)
        check("the old entry is still read with its OWN sheet", lambda: foreign in {e["name"] for e in weekpost._entries(PRIOR_WEEK, "Cash")})
        check("the old date-based QuickBooks list WOULD have pulled it into this week (why it is not used here)",
              lambda: foreign in {e["name"] for e in frappe.get_all("Petty Cash Entry", {"week_ending": WEEK, "float": "Cash"}, ["name"])})
        check("...and that older path is refused on a native week", lambda: qbo.stage_week(WEEK, "Cash"), "keeps its lines as entries")
        check("the older ERPNext posting path is refused on a native week too", lambda: post.post_week(WEEK, "Cash"), "keeps its lines as entries")

        st = weekpost.stage_qbo(WEEK, "Cash")
        staged = set(frappe.get_all("Petty Cash Entry", {"qbo_queue": st["queue_row"]}, pluck="name"))
        posted = {e["name"] for e in weekpost._survey(WEEK, "Cash")[1]["posted"]}
        check("QuickBooks staging is built from exactly the lines ERPNext posted for this week",
              lambda: staged <= posted and posted == set(live) and foreign not in staged)
        held = {n for h in st["held"] for n in h["entries"]}
        out_of = {n for h in st["not_for_quickbooks"] for n in h["entries"]}
        check("every posted line is accounted for: staged, held with a reason, or stated as not for QuickBooks",
              lambda: staged | held | out_of == posted and not (staged & out_of))
        check("the Bahati line is stated as not for QuickBooks, with the reason (5,000)",
              lambda: st["not_for_quickbooks"] == [{"reason": f"{BVL} has no books in QuickBooks", "lines": 1, "amount": 5000.0, "entries": [pw]}])
        check("the three Vimit lines are the ones staged (7,020)", lambda: staged == {te, se, fk} and st["amount"] == 7020)
        check("QuickBooks staging is unapproved and mirrors both journals",
              lambda: st["approved"] is False and all(j.name in frappe.db.get_value("QBO Petty Cash Journal", st["queue_row"], "erp_journals") for j in js))
        st2 = weekpost.stage_qbo(WEEK, "Cash")
        check("staging twice keeps ONE staging row", lambda: st2["queue_row"] == st["queue_row"] and frappe.db.count("QBO Petty Cash Journal", {"week_ending": WEEK, "float": "Cash"}) == 1)
        res = weekpost.posting_results(sheet)
        check("the week's posting result shows the QuickBooks row as Staged", lambda: res["quickbooks"]["row"] == st["queue_row"] and res["quickbooks"]["state"] == "Staged")

        # ---- reopening: submitted and pushed refuse; drafts are discarded ----
        frappe.db.set_value("QBO Petty Cash Journal", st["queue_row"], "qbo_journal_id", "9999")
        check("reopen refused once the expense is in QuickBooks", lambda: api.reopen_week(sheet), "already in QuickBooks")
        frappe.db.set_value("QBO Petty Cash Journal", st["queue_row"], "qbo_journal_id", None)
        frappe.db.set_value("Journal Entry", by[BVL].name, "docstatus", 1)
        check("reopen refused while one company's journal is submitted", lambda: api.reopen_week(sheet), "submitted journal")
        check("...and nothing was discarded by the refused attempt (both journals and the staging row still there)",
              lambda: len(journals(tag)) == 2 and frappe.db.exists("QBO Petty Cash Journal", st["queue_row"]))
        frappe.db.set_value("Journal Entry", by[BVL].name, "docstatus", 0)
        r = api.reopen_week(sheet)
        check("reopen discards BOTH companies' drafts and the unpushed staging row",
              lambda: sorted(r["discarded"]["journals"]) == sorted(j.name for j in js) and r["discarded"]["quickbooks_row"] == st["queue_row"]
              and journals(tag) == [] and not frappe.db.exists("QBO Petty Cash Journal", st["queue_row"]))
        check("...clears every link: lines, per-company record, the week",
              lambda: not any(frappe.db.get_value("Petty Cash Entry", e, ["journal_entry", "qbo_queue"]) != (None, None) for e in live)
              and len(weekpost._stored_postings(sheet)) == 0
              and frappe.db.get_value("Petty Cash Sheet", sheet, ["status", "journal_entry", "qbo_queue"]) == ("Draft", None, None))
        check("...and writes what it discarded on the week", lambda: any("discarded draft journal" in (c.content or "") for c in frappe.get_all("Comment", {"reference_doctype": "Petty Cash Sheet", "reference_name": sheet}, ["content"])))
        entry_api.withdraw_entry(te, reason="amount keyed wrong")
        x = frappe.get_doc("Petty Cash Entry", te); x.amount = 1200; x.save()
        check("lines are editable again after the reopen", lambda: frappe.db.get_value("Petty Cash Entry", te, "amount") == 1200)
        vat.set_vat(te, "No VAT"); entry_api.approve_entry(te)
        api.close_week(sheet, 7780, revision=rev(sheet)); api.approve_week(sheet, revision=rev(sheet))     # agrees exactly: nothing to confirm
        out = weekpost.post_erp(WEEK, "Cash")
        by2 = {j.company: j for j in journals(tag)}
        check("posting again builds fresh drafts from the lines as they now are (Vimit 7,220)",
              lambda: len(out["created"]) == 2 and by2[VCL].total_debit == 7220 and by2[BVL].total_debit == 5000
              and all(j.docstatus == 0 for j in by2.values()))
        # ---- a week whose BOOK opening is negative while the tin is empty ----
        n = frappe.new_doc("Petty Cash Sheet")
        n.update({"week_ending": "2027-04-24", "float": "Cash", "custodian_name": "Test", "status": "Draft", "opening_balance": 0, "native_lines": 1})
        n.insert(); neg = n.name
        frappe.db.set_value("Petty Cash Sheet", neg, "opening_balance", -500)
        check("an opening count that differs from the book needs a note", lambda: api.set_opening_count(neg, 0), "Say what is known about this difference")
        api.set_opening_count(neg, 0, note="Book is 500 below the tin at the start; cause not yet known")
        nq = api.quick_entry(neg, "bike", txn_date="2027-04-20", amount=300, notes="fuel")["entry_id"]
        vat.set_vat(nq, "No VAT"); entry_api.approve_entry(nq)
        check("negative book balance: the book figure cannot be typed in as the balance", lambda: api.close_week(neg, -800, revision=rev(neg)), "cannot be negative")
        api.close_week(neg, 0, revision=rev(neg))
        cp = api.cash_position(neg)
        check("negative book balance: closed on the supplied balance (0); book -800, book against actual 800 = historical 500 + this week 300",
              lambda: (cp["book_cash_balance"], cp["supplied_balance"], cp["book_vs_actual"], cp["historical_difference"], cp["current_week_discrepancy"]) == (-800, 0, 800, 500, 300))
        check("negative book balance: only this week's 300 is the new exception; approval refused until it is acknowledged", lambda: api.approve_week(neg, revision=rev(neg)), "Acknowledge it")
        check("negative book balance: the close is written on the week with both chains",
              lambda: any("historical difference carried" in (c.content or "") for c in frappe.get_all("Comment", {"reference_doctype": "Petty Cash Sheet", "reference_name": neg}, ["content"])))
        rep = weeks.coverage_report()
        check("lookup still returns every entry exactly once", lambda: not rep["returned_twice"] and not rep["never_returned"])
    finally:
        frappe.db.rollback(save_point="native_step7")
        frappe.db.rollback()
        frappe.db.commit = real_commit

    left = (frappe.db.count("Petty Cash Sheet", {"week_ending": (">=", "2027-01-01")}) + (frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")}) - BASE["native"])
            + frappe.db.count("Journal Entry", {"cheque_no": ("like", "PC-Cash-2027%")}) + frappe.db.count("QBO Petty Cash Journal", {"week_ending": (">=", "2027-01-01")}))
    RESULTS.append(("rolled back: no test sheet, entry, journal or staging row left behind", left == 0, f"{left} left" if left else ""))
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} passed")
