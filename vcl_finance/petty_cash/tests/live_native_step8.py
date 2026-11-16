"""Step 8 for native lines: the cash control. The book and the actual balance as two separate chains;
the historical difference recorded once and carried; each week's cash checked on its own.
Rolled back.

    bench --site <site> execute vcl_finance.petty_cash.tests.live_native_step8.run

The story, over three consecutive weeks on one float:
  W1  first week kept as entries. Book opens at -27,000; the actual balance is 3,000. That
      difference (30,000) is the BASELINE, acknowledged once. The week's cash agrees.
  W2  the actual balance is 120 short. A NEW exception for this week only.
  W3  cash agrees again. The historical difference is still 30,000; W2's 120 is shown
      separately and is not folded into it.
"""
import frappe

RESULTS = []
BASE = {}
W = [("2027-07-10", "2027-07-06"), ("2027-07-17", "2027-07-13"), ("2027-07-24", "2027-07-20")]
FINANCE, SIGNER = "pc8-finance@test.invalid", "pc8-signer@test.invalid"


def check(name, fn, expect_error=None):
    try:
        out = fn()
        ok = expect_error is None and out is not False
        detail = "" if ok else ("expected refusal, got none" if expect_error else f"returned {out!r}"[:240])
    except Exception as e:      # noqa: BLE001
        msg = str(e) or type(e).__name__
        ok = expect_error is not None and expect_error.lower() in msg.lower()
        detail = "" if ok else f"{type(e).__name__}: {msg[:260]}"
    RESULTS.append((name, ok, detail))


def run():
    frappe.set_user("Administrator")
    BASE["native"] = frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")})
    # These checks run inside ONE transaction that is rolled back at the end, so the actions
    # must not start a transaction of their own (the separate-session runs test that path).
    frappe.flags.pc_keep_transaction = True
    from vcl_finance.petty_cash import api, entry_api, vat
    from vcl_finance.petty_cash.doctype.petty_cash_sheet import petty_cash_sheet as PCS
    frappe.db.savepoint("native_step8")
    try:
        for who, roles in ((FINANCE, ["Accounts Manager", "Accounts User"]), (SIGNER, ["Petty Cash User", "Petty Cash Approver"])):
            u = frappe.new_doc("User"); u.update({"email": who, "first_name": who.split("@")[0], "send_welcome_email": 0, "enabled": 1})
            for r in roles: u.append("roles", {"role": r})
            u.insert(ignore_permissions=True)
        rev = lambda s: frappe.db.get_value("Petty Cash Sheet", s, "lines_revision")
        H = lambda s: frappe.db.get_value("Petty Cash Sheet", s, ["opening_balance", "expected_close", "cash_count_end", "variance", "opening_actual_balance",
                                                                 "opening_actual_source", "expected_actual_close", "cash_discrepancy",
                                                                 "cash_reconfirm_needed", "cash_approval_kind", "cash_baseline_set", "status"], as_dict=True)
        comments = lambda s: " || ".join(c.content or "" for c in frappe.get_all("Comment", {"reference_doctype": "Petty Cash Sheet", "reference_name": s}, ["content"], order_by="creation"))

        def new_week(i, opening=None):
            s = frappe.new_doc("Petty Cash Sheet")
            s.update({"week_ending": W[i][0], "float": "Cash", "custodian_name": "Test", "status": "Draft", "native_lines": 1})
            carried = s.carry_forward_opening()
            s.opening_balance = carried if (carried is not None and opening is None) else opening
            s.insert()
            return s.name

        def spend(sheet, i, amount, cash_in=0):
            f = dict(cash_in=1, recipient="Top up") if cash_in else dict(category="TE", pc_received=1, recipient="R")
            e = api.quick_entry(sheet, "voucher", txn_date=W[i][1], amount=amount, **f)["entry_id"]
            if not cash_in:
                vat.set_vat(e, "No VAT")
            entry_api.approve_entry(e)
            return e

        # ================= W1: the baseline =================
        frappe.set_user(FINANCE)
        w1 = new_week(0, opening=-27000)
        frappe.set_user(SIGNER)
        check("W1 only an Accounts Manager can record the opening actual balance", lambda: api.set_opening_count(w1, 3000, note="x"), "Only an Accounts Manager")
        frappe.set_user(FINANCE)
        e1 = spend(w1, 0, 1000); spend(w1, 0, 5000, cash_in=1)
        check("W1 cannot be closed before an opening actual balance is recorded", lambda: api.close_week(w1, 7000, revision=rev(w1)), "opening actual balance")
        check("W1 a negative opening count is refused", lambda: api.set_opening_count(w1, -27000, note="x"), "cannot be negative")
        check("W1 an opening count that differs from the book needs a note", lambda: api.set_opening_count(w1, 3000), "Say what is known about this difference")
        p = api.set_opening_count(w1, 3000, note="Book has run below the actual balance since March; top-ups not all recorded. Not yet investigated.")
        h = H(w1)
        check("W1 both figures kept: book opening -27,000 and actual opening 3,000; neither replaced the other",
              lambda: (h.opening_balance, h.opening_actual_balance, h.opening_actual_source, h.cash_baseline_set) == (-27000, 3000, "Supplied", 1))
        check("W1 historical difference recorded once: 30,000, with its week, note, who and when",
              lambda: p["historical_difference"] == 30000 and p["baseline"]["sheet"] == w1 and p["baseline"]["week_ending"] == W[0][0]
              and p["baseline"]["acknowledged_by"] == FINANCE and "March" in p["baseline"]["note"] and p["baseline"]["acknowledged_on"])
        check("W1 before the closing balance is supplied: expected actual balance = 3,000 + 5,000 - 1,000 = 7,000; book cash balance = -23,000",
              lambda: (p["expected_actual_close"], p["book_cash_balance"], p["supplied_balance"], p["current_week_discrepancy"]) == (7000, -23000, None, None))
        check("W1 the book figure (-23,000) cannot be entered as the balance", lambda: api.close_week(w1, -23000, revision=rev(w1)), "cannot be negative")
        r = api.close_week(w1, 7000, revision=rev(w1))
        c = r["cash"]
        check("W1 closed on the supplied balance 7,000: this week's discrepancy is 0",
              lambda: (c["supplied_balance"], c["expected_actual_close"], c["current_week_discrepancy"]) == (7000, 7000, 0))
        check("W1 book against actual is 30,000, all of it the historical difference; nothing new",
              lambda: (c["book_vs_actual"], c["historical_difference"], c["earlier_discrepancies_since_baseline"]) == (30000, 30000, 0))
        check("W1 NO acknowledgement is asked for, although the book balance is negative and 30,000 from the actual balance",
              lambda: H(w1).cash_reconfirm_needed == 0 and not c["exception_needs_acknowledging"])
        a = api.approve_week(w1, revision=rev(w1))
        check("W1 approved straight away, recorded as: cash agrees; historical difference carried",
              lambda: a["status"] == "Approved" and a["cash"]["approval_kind"] == "Cash agrees; historical difference carried"
              and "Historical difference carried, unchanged" in comments(w1))

        # ================= W2: a new discrepancy =================
        w2 = new_week(1)
        h = H(w2)
        check("W2 the BOOK opening carries the book close (-23,000), not the count (7,000)", lambda: h.opening_balance == -23000)
        check("W2 the opening count cannot be typed again: it is carried from W1's Saturday closing balance",
              lambda: api.set_opening_count(w2, 7000), "Saturday closing balance")
        spend(w2, 1, 2000)
        r = api.close_week(w2, 4880, revision=rev(w2))            # should be 5,000
        c = r["cash"]
        check("W2 opening actual balance carried automatically from W1's Saturday closing balance (7,000)",
              lambda: (H(w2).opening_actual_balance, H(w2).opening_actual_source) == (7000, "Carried from previous week"))
        check("W2 expected actual balance 5,000; supplied 4,880; this week's discrepancy -120",
              lambda: (c["expected_actual_close"], c["supplied_balance"], c["current_week_discrepancy"]) == (5000, 4880, -120))
        check("W2 book against actual 29,880 = historical 30,000 + this week -120; the historical figure did NOT move",
              lambda: (c["book_vs_actual"], c["historical_difference"], c["earlier_discrepancies_since_baseline"]) == (29880, 30000, 0)
              and c["book_vs_actual"] == c["historical_difference"] + c["earlier_discrepancies_since_baseline"] + c["current_week_discrepancy"])
        check("W2 the new discrepancy must be acknowledged: approval refused", lambda: api.approve_week(w2, revision=rev(w2)), "Acknowledge it")
        frappe.set_user(SIGNER)
        check("W2 a signer cannot acknowledge it", lambda: api.confirm_cash(w2, revision=rev(w2), reason="x"), "Only an Accounts Manager")
        frappe.set_user(FINANCE)
        check("W2 acknowledging with no reason is refused", lambda: api.confirm_cash(w2, revision=rev(w2)), "Reason")
        k = api.confirm_cash(w2, revision=rev(w2), reason="120 short. Shiro believes a parking slip was paid twice; being checked.", evidence="Slips P-221 and P-224 attached to the week")
        check("W2 acknowledged: reason, evidence, who and when recorded; the supplied balance is untouched and the discrepancy is still -120",
              lambda: k["cash"]["acknowledged_by"] == FINANCE and "parking slip" in k["cash"]["acknowledged_reason"] and "P-221" in k["cash"]["acknowledged_evidence"]
              and k["cash"]["acknowledged_on"] and k["cash"]["supplied_balance"] == 4880 and k["cash"]["current_week_discrepancy"] == -120)
        check("W2 the record says acknowledging is not reconciling", lambda: "this does not mean the balance reconciles" in comments(w2))
        a = api.approve_week(w2, revision=rev(w2))
        check("W2 approved, recorded as: NEW cash exception approved (not as the historical difference carried)",
              lambda: a["cash"]["approval_kind"] == "New cash exception approved" and "NEW cash exception of" in comments(w2) and "-120" in comments(w2).replace("(120", "-120"))

        # ================= W3: back to agreeing =================
        w3 = new_week(2)
        spend(w3, 2, 880)
        r = api.close_week(w3, 4000, revision=rev(w3))
        c = r["cash"]
        check("W3 opening actual balance 4,880 (W2's Saturday closing balance); book opening -25,000 (W2's book close)", lambda: (H(w3).opening_actual_balance, H(w3).opening_balance) == (4880, -25000))
        check("W3 this week's cash agrees: expected 4,000, supplied 4,000, discrepancy 0",
              lambda: (c["expected_actual_close"], c["supplied_balance"], c["current_week_discrepancy"]) == (4000, 4000, 0))
        check("W3 historical difference STILL 30,000; W2's -120 shown separately as an earlier discrepancy; total 29,880",
              lambda: (c["historical_difference"], c["earlier_discrepancies_since_baseline"], c["book_vs_actual"]) == (30000, -120, 29880))
        check("W3 nothing to acknowledge: the unchanged historical difference and W2's already-approved 120 are not asked about again",
              lambda: H(w3).cash_reconfirm_needed == 0)
        a = api.approve_week(w3, revision=rev(w3))
        check("W3 approved as: cash agrees; historical difference carried", lambda: a["cash"]["approval_kind"] == "Cash agrees; historical difference carried")
        check("the baseline was set once (on W1 only)", lambda: [H(x).cash_baseline_set for x in (w1, w2, w3)] == [1, 0, 0])

        # ================= a cash-moving change after the count =================
        api.reopen_week(w3)
        api.close_week(w3, 4000, revision=rev(w3))
        acct = frappe.db.get_value("Account", {"company": frappe.db.get_value("Petty Cash Sheet", w3, "company"), "is_group": 0, "root_type": "Expense"}, "name")
        e3 = frappe.get_all("Petty Cash Entry", {"week_sheet": w3}, pluck="name")[0]
        d = frappe.get_doc("Petty Cash Entry", e3); d.posting_account = acct; d.save()
        check("an account-only change on a Closed week asks for nothing", lambda: H(w3).cash_reconfirm_needed == 0 and H(w3).cash_discrepancy == 0)
        entry_api.withdraw_entry(e3, reason="amount wrong")
        d = frappe.get_doc("Petty Cash Entry", e3); d.amount = 900; d.save()          # 880 -> 900
        h = H(w3)
        check("an amount change after the balance was entered: the supplied balance stays 4,000, expected actual balance becomes 3,980, a NEW discrepancy of 20 appears and must be acknowledged",
              lambda: (h.cash_count_end, h.expected_actual_close, h.cash_discrepancy, h.cash_reconfirm_needed) == (4000, 3980, 20, 1))
        d = frappe.get_doc("Petty Cash Entry", e3); d.amount = 880; d.save()
        check("putting the amount back removes the discrepancy and the need to acknowledge", lambda: (H(w3).cash_discrepancy, H(w3).cash_reconfirm_needed) == (0, 0))

        # ================= a MONDAY opening balance, reviewed on WEDNESDAY =================
        # W4 ends Sat 31/07. W5 runs Sun 01/08 to Sat 07/08. On Wed 04/08 the exact balance as it
        # stood at the OPENING OF MONDAY 02/08 is supplied. It is used as it is. Saturday's
        # closing balance is reached from it through SUNDAY's movements only.
        W.extend([("2027-07-31", "2027-07-27"), ("2027-08-07", "2027-08-02")])
        entry_api.approve_entry(e3)                          # the earlier checks left this line unsigned
        api.approve_week(w3, revision=rev(w3))
        w4 = new_week(3)
        spend(w4, 3, 1000)                                    # W4: opens 4,000, pays 1,000 -> 3,000 at Saturday's close
        w5 = new_week(4)
        def pay(sheet, day, amount, cash_in=0):
            f = dict(cash_in=1, recipient="Top up") if cash_in else dict(category="TE", pc_received=1, recipient="R")
            e = api.quick_entry(sheet, "voucher", txn_date=day, amount=amount, **f)["entry_id"]
            if not cash_in: vat.set_vat(e, "No VAT")
            entry_api.approve_entry(e); return e
        pay(w5, "2027-08-01", 300); pay(w5, "2027-08-01", 100, cash_in=1)      # SUNDAY: paid 300, received 100
        pay(w5, "2027-08-02", 200); pay(w5, "2027-08-03", 5000, cash_in=1)     # Monday and Tuesday: AFTER the snapshot
        pay(w5, "2027-08-04", 150)                                             # Wednesday, the review day
        # Monday opening balance = 3,000 - 300 + 100 = 2,800, held as: cash in hand 300, corporate M-Pesa 2,000,
        # and a personal M-Pesa balance of 6,400 of which 500 is the float's.
        PARTS = [{"kind": "Cash in hand", "balance": 300}, {"kind": "Corporate M-Pesa", "balance": 2000, "label": "Till 55123"},
                 {"kind": "Personal M-Pesa", "balance": 6400, "float_amount": 500, "label": "Shiro"}]
        close4 = lambda **kw: api.close_week(w4, revision=rev(w4), **kw)["cash"]
        check("a personal M-Pesa balance with no float portion stated is refused (it is not assumed to be company money)",
              lambda: close4(components=[PARTS[0], PARTS[1], {"kind": "Personal M-Pesa", "balance": 6400}], balance_date="2027-08-02", reviewed_on="2027-08-04"),
              "how much of the personal M-Pesa balance")
        check("a float portion larger than the balance supplied is refused",
              lambda: close4(components=[{"kind": "Personal M-Pesa", "balance": 400, "float_amount": 500}], balance_date="2027-08-02", reviewed_on="2027-08-04"), "more than the balance supplied")
        check("a negative balance is refused", lambda: close4(components=[{"kind": "Cash in hand", "balance": -1}], balance_date="2027-08-02", reviewed_on="2027-08-04"), "cannot be negative")
        check("the balance date cannot be on or before the week end", lambda: close4(components=PARTS, balance_date="2027-07-31", reviewed_on="2027-08-04"), "opening of a day between")
        check("the review date cannot be before the balance date", lambda: close4(components=PARTS, balance_date="2027-08-02", reviewed_on="2027-08-01"), "cannot be before the date it is the balance of")
        r = close4(components=PARTS, balance_date="2027-08-02", reviewed_on="2027-08-04")
        check("two separate dates recorded: balance is as at the opening of Monday 02/08; received and reviewed Wednesday 04/08",
              lambda: (r["balance_effective_on"], r["balance_reviewed_on"]) == ("2027-08-02", "2027-08-04"))
        check("the supplied Monday balance is used as it is: 2,800 for this float (300 + 2,000 + 500)", lambda: r["supplied_balance"] == 2800)
        check("the components stay identifiable; the personal balance is 6,400 but only its 500 float portion counts",
              lambda: [(c["kind"], c["balance"], c["float_amount"]) for c in r["components"]] == [("Cash in hand", 300, 300), ("Corporate M-Pesa", 2000, 2000), ("Personal M-Pesa", 6400, 500)]
              and r["components"][1]["label"] == "Till 55123")
        check("the bridge uses SUNDAY only: paid 300, received 100. Monday's 200, Tuesday's 5,000 and Wednesday's 150 are not in it",
              lambda: (r["paid_before_balance_date"], r["received_before_balance_date"]) == (300, 100))
        check("Saturday closing balance = 2,800 + 300 - 100 = 3,000, which is what was expected: no discrepancy, nothing to acknowledge",
              lambda: (r["actual_balance_at_week_end"], r["expected_actual_close"], r["current_week_discrepancy"]) == (3000, 3000, 0) and H(w4).cash_reconfirm_needed == 0)
        check("the close is written on the week with both dates and the components",
              lambda: all(x in comments(w4) for x in ("as at the opening of", "received and reviewed", "Personal M-Pesa", "Corporate M-Pesa")))
        api.reopen_week(w4)
        r = api.close_week(w4, 2800, revision=rev(w4))["cash"]
        check("with no date given the balance is taken as the opening of the Monday after the week end (02/08)", lambda: r["balance_effective_on"] == "2027-08-02" and r["current_week_discrepancy"] == 0)
        api.reopen_week(w4)
        r = close4(components=[PARTS[0], PARTS[1], {"kind": "Personal M-Pesa", "balance": 6400, "float_amount": 440}], balance_date="2027-08-02", reviewed_on="2027-08-04")
        check("a real shortfall still shows: 2,740 supplied gives 2,940 at Saturday's close, a discrepancy of -60 that must be acknowledged",
              lambda: (r["supplied_balance"], r["actual_balance_at_week_end"], r["current_week_discrepancy"]) == (2740, 2940, -60) and H(w4).cash_reconfirm_needed == 1)
        late = pay(w5, "2027-08-01", 60)                      # a SUNDAY payment keyed late
        h4 = H(w4)
        check("a Sunday payment keyed after the balance was entered is picked up while the week is only Closed: the 60 is found and the discrepancy clears",
              lambda: (frappe.db.get_value("Petty Cash Sheet", w4, "paid_before_balance_date_amt"), h4.cash_discrepancy, h4.cash_reconfirm_needed) == (360, 0, 0)
              and "A line dated between this week's end and the date of its supplied balance changed" in comments(w4))
        mon = pay(w5, "2027-08-02", 40)                       # a MONDAY payment keyed late
        check("a Monday payment keyed late changes NOTHING on the earlier week: Monday is after the snapshot",
              lambda: (frappe.db.get_value("Petty Cash Sheet", w4, "paid_before_balance_date_amt"), H(w4).cash_discrepancy) == (360, 0))
        a4 = api.approve_week(w4, revision=rev(w4))
        check("W4 approved as: actual balance agrees; historical difference carried", lambda: a4["cash"]["approval_kind"] == "Cash agrees; historical difference carried")
        c5 = api.close_week(w5, 0, revision=rev(w5))["cash"]
        check("W5 opens with W4's SATURDAY closing balance (3,000): not the Monday figure (2,740), and not anything from Wednesday",
              lambda: c5["opening_actual_balance"] == 3000)
        out5 = frappe.db.get_value("Petty Cash Sheet", w5, ["total_out", "total_in"])
        check("Sunday is counted ONCE: W5's own movements include Sunday (paid 750 in all, received 5,100), so expected = 3,000 + 5,100 - 750 = 7,350",
              lambda: tuple(out5) == (750, 5100) and c5["expected_actual_close"] == 7350)
        api.reopen_week(w5)
        pay(w5, "2027-08-06", 25)
        check("a line keyed after W4 is APPROVED does not reopen W4's figures", lambda: (H(w4).cash_discrepancy, frappe.db.get_value("Petty Cash Sheet", w4, "paid_before_balance_date_amt")) == (0, 360))
        check("the historical difference is still 30,000 five weeks on", lambda: api.cash_position(w5)["historical_difference"] == 30000)

        # ================= the opening balance of the FIRST week is a Monday balance too =================
        # A separate float, so this first week has no earlier week kept as entries.
        f = frappe.new_doc("Petty Cash Sheet")
        f.update({"week_ending": "2027-09-11", "float": "Hauz-Pay", "custodian_name": "Test", "status": "Draft", "native_lines": 1, "opening_balance": 1000})
        f.insert(); fw = f.name
        api.quick_entry(fw, "voucher", txn_date="2027-09-05", amount=250, category="TE", pc_received=1, recipient="Sunday")     # Sunday of this week
        api.quick_entry(fw, "voucher", txn_date="2027-09-06", amount=90, category="TE", pc_received=1, recipient="Monday")      # Monday: after the snapshot
        o = api.set_opening_count(fw, components=[{"kind": "Corporate M-Pesa", "balance": 750}], balance_date="2027-09-06", reviewed_on="2027-09-08")
        check("first week: Monday opening 750 supplied; the week (which starts Sunday) opens at 750 + Sunday's 250 = 1,000; Monday's 90 is not in the bridge",
              lambda: (o["opening_supplied"]["amount"], o["opening_supplied"]["balance_effective_on"], o["opening_supplied"]["balance_reviewed_on"], o["opening_actual_balance"]) == (750, "2027-09-06", "2027-09-08", 1000))
        check("first week: it agrees with the book opening of 1,000, so there is no historical difference and no note was needed", lambda: o["historical_difference"] == 0)
        check("first week: expected at week end = 1,000 - 340 = 660; Sunday counted once", lambda: o["expected_actual_close"] == 660)

        # ================= summary label; weeks that keep rows are untouched =================
        sm = api.get_feed(w3)["summary"]
        check("on a week kept as entries the book figure is labelled 'Book cash balance'", lambda: sm["balance_label"] == "Book cash balance")
        real = frappe.get_all("Petty Cash Sheet", {"native_lines": 0}, pluck="name", order_by="week_ending desc", limit=1)[0]
        check("on a week that keeps rows the label is unchanged ('Cash remaining')", lambda: api.get_feed(real)["summary"]["balance_label"] == "Cash remaining")
        old = frappe.get_all("Petty Cash Sheet", {"native_lines": 0, "cash_count_end": ("!=", 0)}, ["name", "float", "week_ending", "cash_count_end"], order_by="week_ending desc", limit=1)[0]
        nxt = frappe.utils.add_days(old.week_ending, 1)
        info = PCS._prior_close(old.float, nxt)
        check("carry-forward from a week that keeps rows is exactly as before (the count, when one was entered)",
              lambda: info["prior_name"] == old.name and info["basis"] == "counted" and info["balance"] == round(old.cash_count_end, 2))
    finally:
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="native_step8")
        frappe.db.rollback()

    left = (frappe.db.count("Petty Cash Sheet", {"week_ending": (">=", "2027-01-01")}) + (frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")}) - BASE["native"])
            + frappe.db.count("User", {"name": ("like", "pc8-%")}))
    RESULTS.append(("rolled back: nothing left behind", left == 0, f"{left} left" if left else ""))
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} passed")
