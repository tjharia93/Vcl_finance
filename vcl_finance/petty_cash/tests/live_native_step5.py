"""Steps 4 and 5 for native lines — the change rules, with REAL ROLES. Rolled back.

    bench --site <site> execute vcl_finance.petty_cash.tests.live_native_step5.run

Three users are created inside the transaction and used with ``frappe.set_user``:
  custodian  Petty Cash User                      keys lines
  signer     Petty Cash User + Petty Cash Approver signs and codes lines, cannot tick or close
  finance    Accounts Manager                     everything, including tick, close, approve
Nothing here runs as Administrator except the set-up.
"""
import frappe

RESULTS = []
BASE = {}
WEEK, DAY = "2027-02-13", "2027-02-09"
USERS = {"custodian": ["Petty Cash User"],
         "signer": ["Petty Cash User", "Petty Cash Approver"],
         "finance": ["Accounts Manager", "Accounts User"]}


def check(name, fn, expect_error=None):
    try:
        out = fn()
        ok = expect_error is None and out is not False
        detail = "" if ok else ("expected refusal, got none" if expect_error else f"returned {out!r}")
    except Exception as e:      # noqa: BLE001
        msg = str(e) or type(e).__name__
        ok = expect_error is not None and expect_error.lower() in msg.lower()
        detail = "" if ok else f"{type(e).__name__}: {msg[:200]}"
    RESULTS.append((name, ok, detail))
    return ok


# The people who actually do this, where their accounts exist on the site: the shared
# petty cash login keys lines, and Shiro's own account (Petty Cash User + Petty Cash
# Approver, plus whatever else she holds) signs and codes. Finance is a plain Accounts
# Manager made for the test, so the result does not lean on any one person's extra roles.
REAL = {"custodian": "pettycash@vimit.com", "signer": "shiro@vimit.com"}
WHO = {}


def as_(who):
    frappe.set_user(WHO[who])


def make_users():
    for who, roles in USERS.items():
        real = REAL.get(who)
        if real and frappe.db.exists("User", real) and set(roles) <= set(frappe.get_roles(real)):
            WHO[who] = real
            continue
        WHO[who] = f"pc-{who}@test.invalid"
        u = frappe.new_doc("User")
        u.update({"email": f"pc-{who}@test.invalid", "first_name": f"PC {who}", "send_welcome_email": 0, "enabled": 1})
        for r in roles:
            u.append("roles", {"role": r})
        u.insert(ignore_permissions=True)


def E(name):
    return frappe.get_doc("Petty Cash Entry", name)


def head(sheet):
    return frappe.db.get_value("Petty Cash Sheet", sheet, ["total_out", "expected_close", "variance", "lines_revision",
                                                          "cash_count_end", "cash_reconfirm_needed", "status",
                                                          "expected_close_at_count", "cash_confirmed_by"], as_dict=True)


def edit(name, confirm=False, **values):
    d = E(name)
    d.update(values)
    if confirm:
        d.flags.confirm_tick = True
    d.save()
    return d


def run():
    # What is already on the site (a replayed Trial week may be kept there on purpose).
    BASE["native"] = frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")})
    # These checks run inside ONE transaction that is rolled back at the end, so the actions
    # must not start a transaction of their own (the separate-session runs test that path).
    frappe.flags.pc_keep_transaction = True
    from vcl_finance.petty_cash import api, entry_api, vat
    frappe.set_user("Administrator")
    frappe.db.savepoint("native_step5")
    try:
        make_users()
        s = frappe.new_doc("Petty Cash Sheet")
        s.update({"week_ending": WEEK, "float": "Cash", "custodian_name": "Test", "status": "Draft",
                  "opening_balance": 10000, "native_lines": 1})
        s.insert()
        sheet = s.name
        api.set_opening_count(sheet, 10000)          # the tin holds what the book says: no historical difference
        company = s.company
        other_company = frappe.db.get_value("Company", {"name": ("!=", company)}, "name")
        acct = frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Expense"}, "name")
        acct2 = frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Expense", "name": ("!=", acct)}, "name")

        # ================= OPEN week =================
        as_("custodian")
        key = lambda **f: api.quick_entry(sheet, "voucher", txn_date=DAY, category="TE", pc_received=1, **f)["entry_id"]
        a, b, c, d = key(amount=100, recipient="A", voucher_no="1"), key(amount=200, recipient="B", voucher_no="2"), \
                     key(amount=300, recipient="C", voucher_no="3"), key(amount=400, recipient="D", voucher_no="4")
        check("OPEN custodian keys lines", lambda: head(sheet).total_out == 1000)
        check("OPEN custodian edits an unsigned line", lambda: edit(a, amount=110).amount == 110)
        check("OPEN custodian cannot sign a line", lambda: entry_api.approve_entry(a), "Only Finance can approve")
        # The tick and the accounts are permission-level-1 fields. A user without that level
        # cannot write them at all: the framework drops the value before our rules even run.
        check("OPEN custodian cannot tick a line (the value is not saved)", lambda: edit(a, reconciled=1).reconciled == 0 and E(a).reconciled == 0)
        check("OPEN custodian cannot set VAT", lambda: vat.set_vat(a, "No VAT"), "Only Finance")
        check("OPEN custodian cannot set an account (the value is not saved)", lambda: not edit(a, posting_account=acct).posting_account and not E(a).posting_account)
        # Refused either by our rule or, for an account limited to one company, by the
        # framework's own permission check before our rule is reached. Any refusal counts.
        check("OPEN custodian cannot change the company", lambda: edit(a, company=other_company), "")
        check("  ...and the company did not change", lambda: E(a).company == company)
        check("OPEN custodian cannot close the week", lambda: api.close_week(sheet, 9000, revision=head(sheet).lines_revision), "Only an Accounts Manager")

        as_("signer")
        entry_api.approve_entry(a); entry_api.approve_entry(b); entry_api.approve_entry(c)
        check("OPEN signer signs lines", lambda: E(a).status == "Approved" and E(a).approved_by == WHO["signer"])
        check("OPEN signer sets VAT and account on a signed line",
              lambda: (vat.set_vat(a, "No VAT"), edit(a, posting_account=acct))[1].posting_account == acct)
        check("OPEN signer cannot tick (Finance only)", lambda: edit(a, reconciled=1), "Only Finance can mark")
        check("OPEN signer cannot close the week", lambda: api.close_week(sheet, 9000, revision=head(sheet).lines_revision), "Only an Accounts Manager")

        as_("custodian")
        check("SIGNED line: custodian cannot change the amount", lambda: edit(a, amount=999), "signed off")
        check("SIGNED line: custodian cannot change the slip number", lambda: edit(a, voucher_no="X"), "Only Finance can change the slip")
        check("SIGNED line: custodian cannot void it", lambda: api.cancel_entry(sheet, a), "Only Finance can void")
        check("SIGNED line: custodian can add the receipt photo; line is flagged",
              lambda: (api.attach_receipt(sheet, row_name=a, file_url="/files/a.jpg"), E(a))[1].changed_after_approval == 1)
        check("UNSIGNED line: custodian can still void and reinstate it",
              lambda: (api.cancel_entry(sheet, d), api.reinstate_entry(sheet, d), E(d))[2].cancelled == 0)

        as_("finance")
        check("SIGNED line: even Finance must withdraw before changing the amount", lambda: edit(a, amount=999), "signed off")
        check("Finance ticks a line; stamped with who and when",
              lambda: (edit(a, reconciled=1), E(a))[1].reconciled_by == WHO["finance"] and E(a).reconciled_on)
        edit(b, reconciled=1); edit(c, reconciled=1)

        # ================= what a change does to a TICKED line =================
        rev = head(sheet).lines_revision
        tot = head(sheet).total_out
        as_("signer")
        x = edit(a, posting_account=acct2)
        check("TICKED: an account-only change clears the tick", lambda: x.reconciled == 0 and not x.reconciled_by)
        check("  ...and moves the week revision while count and total stay the same",
              lambda: head(sheet).lines_revision > rev and head(sheet).total_out == tot)
        check("  ...and is written down on the line",
              lambda: any("ERPNext account" in (c_.content or "") for c_ in frappe.get_all("Comment", {"reference_doctype": "Petty Cash Entry", "reference_name": a}, ["content"])))
        check("TICKED: signer cannot keep the tick by 'confirming' (only Finance can)",
              lambda: edit(b, confirm=True, supplier_ref="INV-1").reconciled == 0)
        as_("finance")
        edit(a, reconciled=1); edit(b, reconciled=1)
        x = edit(a, confirm=True, vat_treatment="Exempt")
        check("TICKED: Finance changes VAT and confirms in the same action; tick kept, re-stamped",
              lambda: x.reconciled == 1 and x.reconciled_by == WHO["finance"])
        x = edit(a, vat_treatment="No VAT")
        check("TICKED: Finance changes VAT without confirming; tick cleared", lambda: x.reconciled == 0)
        edit(a, reconciled=1)
        check("TICKED: company change refused while the account belongs to the old company",
              lambda: edit(a, company=other_company), "belongs to") if other_company else None
        if other_company:
            x = edit(a, confirm=True, company=other_company, posting_account=None)
            check("TICKED: company change ALWAYS clears the tick, even when confirmed", lambda: x.reconciled == 0 and x.company == other_company)
            edit(a, company=company, posting_account=acct); edit(a, reconciled=1)
        x = (api.attach_receipt(sheet, row_name=b, file_url="/files/b2.jpg"), E(b))[1]
        check("TICKED: replacing the receipt clears the tick", lambda: x.reconciled == 0 and x.receipt == "/files/b2.jpg")
        edit(b, reconciled=1)
        entry_api.withdraw_entry(c, reason="wrong slip")
        check("TICKED: withdrawing the sign-off clears the tick", lambda: E(c).reconciled == 0 and E(c).status == "Withdrawn")
        check("  ...and the line can then be corrected and signed again",
              lambda: (edit(c, amount=310), entry_api.approve_entry(c), E(c))[2].status == "Approved")
        edit(c, reconciled=1)
        entry_api.approve_entry(d); edit(d, reconciled=1)

        # ================= CLOSED week =================
        h = head(sheet)
        check("close refused on a stale version (the week changed since it was opened)",
              lambda: api.close_week(sheet, 8980, revision=h.lines_revision - 1), "changed since you opened")
        check("close refused with no version at all", lambda: api.close_week(sheet, 8980), "from its week page")
        r = api.close_week(sheet, 8980, revision=h.lines_revision)
        h = head(sheet)
        check("Finance closes the week: totals recomputed from the entries (out 1,020; expected 8,980; variance 0)",
              lambda: h.status == "Closed" and h.total_out == 1020 and h.expected_close == 8980 and h.variance == 0 and h.expected_close_at_count == 8980)

        as_("custodian")
        check("CLOSED custodian cannot key a line", lambda: key(amount=5, recipient="late"), "closed")
        check("CLOSED custodian cannot attach a receipt", lambda: api.attach_receipt(sheet, row_name=d, file_url="/files/d.jpg"), "closed")
        check("CLOSED custodian cannot change a slip number", lambda: edit(d, voucher_no="Z"), "Only Finance")
        as_("signer")
        check("CLOSED signer can still code a line (account)", lambda: edit(b, posting_account=acct).posting_account == acct)
        check("CLOSED signer cannot key a line", lambda: key(amount=5, recipient="late"), "closed")
        as_("finance")
        edit(b, reconciled=1)
        check("CLOSED Finance signs, ticks and codes (review continues after close)",
              lambda: edit(d, confirm=True, supplier_ref="RCT-9").reconciled == 1)

        # ---- Finance changes the cash figures on a Closed week ----
        check("CLOSED nothing has needed re-confirming so far", lambda: head(sheet).cash_reconfirm_needed == 0)
        entry_api.withdraw_entry(d, reason="amount keyed wrong")
        edit(d, amount=450)                       # 400 -> 450
        h = head(sheet)
        check("CLOSED amount change: physical count untouched, variance recomputed, re-confirmation required",
              lambda: h.cash_count_end == 8980 and h.expected_close == 8930 and h.variance == 50
              and h.expected_close_at_count == 8980 and h.cash_reconfirm_needed == 1)
        check("  ...and the sheet's history says what changed against which count",
              lambda: any("Cash reconciliation needs confirming" in (c_.content or "") for c_ in frappe.get_all("Comment", {"reference_doctype": "Petty Cash Sheet", "reference_name": sheet}, ["content"])))
        entry_api.approve_entry(d); edit(d, reconciled=1)
        check("approval refused until the new variance is confirmed",
              lambda: api.approve_week(sheet, revision=head(sheet).lines_revision), "Acknowledge it")
        as_("signer")
        check("signer cannot confirm the cash reconciliation", lambda: api.confirm_cash(sheet, revision=head(sheet).lines_revision, reason="test: counted again"), "Only an Accounts Manager")
        as_("finance")
        r = api.confirm_cash(sheet, revision=head(sheet).lines_revision, reason="test: counted again")
        h = head(sheet)
        check("Finance confirms: variance 50 accepted, count still 8,980, who and when recorded",
              lambda: r["variance"] == 50 and h.cash_count_end == 8980 and h.cash_reconfirm_needed == 0 and h.cash_confirmed_by == WHO["finance"])
        api.cancel_entry(sheet, b, remark="duplicate of line A")
        check("CLOSED voiding a line also requires re-confirmation", lambda: head(sheet).cash_reconfirm_needed == 1 and head(sheet).variance == -150)
        api.reinstate_entry(sheet, b); entry_api.approve_entry(b); edit(b, reconciled=1)
        check("CLOSED reinstating it brings the variance back (50) and still needs confirming",
              lambda: head(sheet).variance == 50 and head(sheet).cash_reconfirm_needed == 1)
        check("an account-only change on a Closed week does NOT ask for cash re-confirmation",
              lambda: (api.confirm_cash(sheet, revision=head(sheet).lines_revision, reason="test: counted again"), edit(a, confirm=True, posting_account=acct2), head(sheet))[2].cash_reconfirm_needed == 0)

        # ================= APPROVE =================
        entry_api.withdraw_entry(c, reason="test")
        check("approval refused while a line is unsigned, naming the line",
              lambda: api.approve_week(sheet, revision=head(sheet).lines_revision), c)
        entry_api.approve_entry(c)
        stale = head(sheet).lines_revision
        as_("signer"); edit(a, posting_account=acct); as_("finance")
        check("approval refused on a stale version after an ACCOUNT-ONLY change (count and total unchanged)",
              lambda: api.approve_week(sheet, revision=stale), "changed since you opened")
        as_("signer")
        check("signer cannot approve the week", lambda: api.approve_week(sheet, revision=head(sheet).lines_revision), "Only an Accounts Manager")
        as_("finance")
        r = api.approve_week(sheet, revision=head(sheet).lines_revision)
        check("Finance approves the week on the current version; stamped", lambda: r["status"] == "Approved" and r["approved_by"] == WHO["finance"])

        # ================= APPROVED week: frozen for everyone =================
        for who in ("custodian", "signer", "finance"):
            as_(who)
            if who == "custodian":
                check("APPROVED custodian cannot change an account (the value is not saved)",
                      lambda: edit(a, posting_account=acct2).posting_account == acct and E(a).posting_account == acct)
            else:
                check(f"APPROVED {who} cannot change an account", lambda: edit(a, posting_account=acct2), "approved")
            check(f"APPROVED {who} cannot attach a receipt", lambda: api.attach_receipt(sheet, row_name=a, file_url="/files/z.jpg"), "")
        as_("finance")
        check("APPROVED Finance cannot tick, void or withdraw either",
              lambda: edit(b, reconciled=0), "approved")
        check("APPROVED Finance cannot withdraw a sign-off", lambda: entry_api.withdraw_entry(a, reason="x"), "approved")
        as_("signer")
        check("signer cannot reopen the week", lambda: api.reopen_week(sheet), "Only an Accounts Manager")

        # ================= journals: draft vs submitted =================
        as_("finance")
        je = frappe.get_doc({"doctype": "Journal Entry", "company": company, "posting_date": WEEK, "voucher_type": "Journal Entry",
                             "cheque_no": "PC-TEST", "cheque_date": WEEK, "user_remark": "test",
                             "accounts": [{"account": acct, "debit_in_account_currency": 110},
                                          {"account": acct2, "credit_in_account_currency": 110}]})
        frappe.set_user("Administrator"); je.insert(ignore_permissions=True)
        frappe.db.set_value("Petty Cash Entry", a, "journal_entry", je.name)
        as_("finance")
        frappe.db.set_value("Petty Cash Sheet", sheet, "status", "Closed")      # isolate the line rule from the week rule
        check("line on a DRAFT journal: change refused, pointing to reopening the week",
              lambda: edit(a, posting_account=acct2), "draft journal")
        frappe.db.set_value("Journal Entry", je.name, "docstatus", 1)
        check("line on a SUBMITTED journal: change refused, pointing to cancelling it in ERPNext",
              lambda: edit(a, posting_account=acct2), "cancelling or reversing")
        frappe.db.set_value("Petty Cash Sheet", sheet, "status", "Approved")
        check("reopen refused while a submitted journal exists", lambda: api.reopen_week(sheet), "submitted journal")
        frappe.db.set_value("Journal Entry", je.name, "docstatus", 0)
        r = api.reopen_week(sheet)
        check("with only a DRAFT journal, Finance reopens the week and the draft is discarded",
              lambda: r["status"] == "Draft" and r["discarded"]["journals"] == [je.name] and not frappe.db.exists("Journal Entry", je.name)
              and not frappe.db.get_value("Petty Cash Entry", a, "journal_entry"))
        check("lines are editable again after the reopen",
              lambda: (entry_api.withdraw_entry(d, reason="reopen"), edit(d, amount=400))[1].amount == 400)
    finally:
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="native_step5")
        frappe.db.rollback()

    left = (frappe.db.count("Petty Cash Sheet", {"week_ending": (">=", "2027-01-01")}) + (frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")}) - BASE["native"])
            + frappe.db.count("User", {"name": ("like", "pc-%@test.invalid")}))
    RESULTS.append(("rolled back: no test sheet, entry or user left behind", left == 0, f"{left} left" if left else ""))
    print("ran as: " + ", ".join(f"{k} = {v} [{', '.join(sorted(r for r in frappe.get_roles(v) if r not in ('All', 'Guest', 'Desk User')))}]"
                                 if frappe.db.exists("User", v) else f"{k} = {v} (test user: {', '.join(USERS[k])})" for k, v in WHO.items()))
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} passed")
