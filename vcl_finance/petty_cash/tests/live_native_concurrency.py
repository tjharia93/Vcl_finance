"""Concurrency checks for native lines, with SEPARATE database sessions.

Each function below is one step run in its own ``bench execute`` process, so each has its
own connection and its own transaction. A shell driver starts two at once. Unlike the
other live checks this one has to COMMIT (another session cannot see uncommitted rows),
so ``setup`` creates a far-future test week and ``teardown`` deletes it again.
"""
import json
import time

import frappe

WEEK_A, WEEK_B, WEEK_C = "2027-03-13", "2027-03-20", "2027-03-27"
DAY_A, DAY_B = "2027-03-09", "2027-03-16"
CUSTODIAN = "pc-conc-custodian@test.invalid"


def _out(**kw):
    print("RESULT " + json.dumps(kw, default=str))


def _sheet(week):
    return frappe.db.get_value("Petty Cash Sheet", {"week_ending": week, "float": "Cash"}, "name")


def _head(sheet):
    return frappe.db.get_value("Petty Cash Sheet", sheet, ["total_out", "expected_close", "lines_revision", "status"], as_dict=True)


def setup():
    from vcl_finance.petty_cash import api
    frappe.set_user("Administrator")
    if not frappe.db.exists("User", CUSTODIAN):
        u = frappe.new_doc("User")
        u.update({"email": CUSTODIAN, "first_name": "PC conc", "send_welcome_email": 0, "enabled": 1})
        u.append("roles", {"role": "Petty Cash User"})
        u.insert(ignore_permissions=True)
    names = {}
    for week, day in ((WEEK_A, DAY_A), (WEEK_B, DAY_B)):
        s = frappe.new_doc("Petty Cash Sheet")
        s.update({"week_ending": week, "float": "Cash", "custodian_name": "Test", "status": "Draft",
                  "opening_balance": 10000, "native_lines": 1})
        s.insert()
        api.set_opening_count(s.name, 10000)
        ids = [api.quick_entry(s.name, "voucher", txn_date=day, category="TE", pc_received=1, amount=a, recipient=f"L{a}")["entry_id"]
               for a in (100, 200, 300)]
        names[week] = {"sheet": s.name, "entries": ids}
    frappe.db.commit()
    _out(**{"a": names[WEEK_A], "b": names[WEEK_B], "rev_a": _head(names[WEEK_A]["sheet"]).lines_revision})


def teardown():
    frappe.set_user("Administrator")
    for j in frappe.get_all("Journal Entry", {"cheque_no": ("like", f"PC-Cash-{WEEK_C}%")}, pluck="name"):
        frappe.db.set_value("Journal Entry", j, "docstatus", 0)
        frappe.delete_doc("Journal Entry", j, force=1, ignore_permissions=True)
    frappe.db.delete("QBO Petty Cash Journal", {"week_ending": WEEK_C})
    for week in (WEEK_A, WEEK_B, WEEK_C):
        sheet = _sheet(week)
        if not sheet:
            continue
        frappe.db.set_value("Petty Cash Sheet", sheet, "status", "Draft")
        for e in frappe.get_all("Petty Cash Entry", {"week_sheet": sheet}, pluck="name"):
            frappe.db.delete("Comment", {"reference_doctype": "Petty Cash Entry", "reference_name": e})
            frappe.db.delete("Version", {"ref_doctype": "Petty Cash Entry", "docname": e})
            frappe.db.delete("Petty Cash Entry", {"name": e})
        frappe.db.delete("Comment", {"reference_doctype": "Petty Cash Sheet", "reference_name": sheet})
        frappe.db.delete("Version", {"ref_doctype": "Petty Cash Sheet", "docname": sheet})
        frappe.db.delete("Petty Cash Sheet", {"name": sheet})
    if frappe.db.exists("User", CUSTODIAN):
        frappe.delete_doc("User", CUSTODIAN, force=True, ignore_permissions=True)
    frappe.db.commit()
    left = (frappe.db.count("Petty Cash Sheet", {"week_ending": (">=", "2027-01-01")}) + frappe.db.count("Petty Cash Entry", {"week_sheet": ("is", "set")})
            + frappe.db.count("User", {"name": CUSTODIAN}))
    _out(left_behind=left, orphans=frappe.db.count("Petty Cash Entry", {"sync_state": "Orphaned"}), entries=frappe.db.count("Petty Cash Entry"))


def _run(label, fn, user="Administrator", hold=0):
    """Run ``fn`` in this session, optionally holding the transaction open, then commit."""
    frappe.set_user(user)
    t0 = time.time()
    try:
        value = fn()
        took = time.time() - t0
        if hold:
            time.sleep(hold)
        frappe.db.commit()
        _out(step=label, ok=True, waited=round(took, 1), value=value)
    except Exception as e:      # noqa: BLE001
        frappe.db.rollback()
        _out(step=label, ok=False, waited=round(time.time() - t0, 1), error=str(e)[:160] or type(e).__name__)


def edit_amount(entry, amount, hold=0, user="Administrator"):
    def fn():
        d = frappe.get_doc("Petty Cash Entry", entry); d.amount = float(amount); d.save(); return d.amount
    _run("edit_amount", fn, user=user, hold=float(hold))


def set_account(entry, hold=0):
    def fn():
        d = frappe.get_doc("Petty Cash Entry", entry)
        d.posting_account = frappe.db.get_value("Account", {"company": d.company, "is_group": 0, "root_type": "Expense",
                                                            "name": ("!=", d.posting_account or "")}, "name")
        d.save(); return d.posting_account
    _run("set_account", fn, hold=float(hold))


def add_line(sheet, amount, hold=0, user="Administrator"):
    from vcl_finance.petty_cash import api
    day = DAY_A if frappe.db.get_value("Petty Cash Sheet", sheet, "week_ending").isoformat() == WEEK_A else DAY_B
    _run("add_line", lambda: api.quick_entry(sheet, "voucher", txn_date=day, category="TE", pc_received=1,
                                             amount=float(amount), recipient=f"C{amount}")["entry_id"],
         user=user, hold=float(hold))


def close(sheet, count, revision, hold=0):
    from vcl_finance.petty_cash import api
    _run("close", lambda: api.close_week(sheet, float(count), revision=int(revision)), hold=float(hold))


def approve(sheet, revision):
    from vcl_finance.petty_cash import api
    _run("approve", lambda: api.approve_week(sheet, revision=int(revision)))


def sign_all(sheet):
    from vcl_finance.petty_cash import entry_api
    def fn():
        for e in frappe.get_all("Petty Cash Entry", {"week_sheet": sheet, "cancelled": 0}, pluck="name"):
            entry_api.approve_entry(e)
        return _head(sheet).lines_revision
    _run("sign_all", fn)


def reopen(sheet):
    from vcl_finance.petty_cash import api
    _run("reopen", lambda: api.reopen_week(sheet)["status"])


def move(entry, txn_date, hold=0):
    from vcl_finance.petty_cash import api
    _run("move", lambda: api.move_entry_to_week(entry, txn_date)["sheet"], hold=float(hold))


def setup_post():
    """A committed, approved native week with lines for TWO companies, ready to post."""
    from vcl_finance.petty_cash import api, entry_api, vat
    frappe.set_user("Administrator")
    s = frappe.new_doc("Petty Cash Sheet")
    s.update({"week_ending": WEEK_C, "float": "Cash", "custodian_name": "Test", "status": "Draft",
              "opening_balance": 10000, "native_lines": 1})
    s.insert()
    api.set_opening_count(s.name, 10000)
    te = api.quick_entry(s.name, "voucher", txn_date="2027-03-23", category="TE", pc_received=1, amount=1000, recipient="V")["entry_id"]
    pw = api.quick_entry(s.name, "piecework", txn_date="2027-03-23", amount=5000, recipient="Gang B", reason="sets")["entry_id"]
    d = frappe.get_doc("Petty Cash Entry", pw); d.company = "BAHATI VENTURES LIMITED"; d.save()
    vat.set_vat(te, "No VAT")
    entry_api.approve_entry(te); entry_api.approve_entry(pw)
    rev = lambda: frappe.db.get_value("Petty Cash Sheet", s.name, "lines_revision")
    api.close_week(s.name, 4000, revision=rev()); api.approve_week(s.name, revision=rev())
    frappe.db.commit()
    _out(sheet=s.name, entries=[te, pw])


def post(hold=0):
    from vcl_finance.petty_cash import weekpost
    _run("post", lambda: {"created": [c["journal"] for c in weekpost.post_erp(WEEK_C, "Cash")["created"]]}, hold=float(hold))


def stage(hold=0):
    from vcl_finance.petty_cash import weekpost
    _run("stage", lambda: weekpost.stage_qbo(WEEK_C, "Cash")["queue_row"], hold=float(hold))


def post_state():
    frappe.set_user("Administrator")
    from vcl_finance.petty_cash import weekpost
    sheet = _sheet(WEEK_C)
    js = frappe.get_all("Journal Entry", {"cheque_no": ("like", f"PC-Cash-{WEEK_C}%")}, ["name", "company", "docstatus", "total_debit", "total_credit"], order_by="company")
    _out(journals=[(j.company[:6], j.name, j.docstatus, j.total_debit, j.total_credit) for j in js],
         posting_rows=len(weekpost._stored_postings(sheet)),
         lines_linked=frappe.db.count("Petty Cash Entry", {"week_sheet": sheet, "journal_entry": ("is", "set")}),
         staging_rows=frappe.db.count("QBO Petty Cash Journal", {"week_ending": WEEK_C, "float": "Cash"}))


def cancel(sheet, entry, hold=0):
    from vcl_finance.petty_cash import api
    _run("cancel", lambda: api.cancel_entry(sheet, entry, remark="conc test")["ok"], hold=float(hold))


def audit(entries):
    """Comments and entries per line: a retried action must not have written anything twice."""
    frappe.set_user("Administrator")
    out = {}
    for e in entries.split(","):
        out[e] = {"voided_comments": sum(1 for c in frappe.get_all("Comment", {"reference_doctype": "Petty Cash Entry", "reference_name": e}, ["content"]) if "Voided" in (c.content or "")),
                  "status": frappe.db.get_value("Petty Cash Entry", e, "status")}
    _out(**out)


def state(sheet):
    frappe.set_user("Administrator")
    from vcl_finance.petty_cash import native
    h = _head(sheet)
    rows = frappe.get_all("Petty Cash Entry", {"week_sheet": sheet}, ["name", "amount", "cancelled"])
    _out(status=h.status, stored_out=h.total_out, fresh_out=native.totals(sheet, 10000)["total_out"],
         revision=h.lines_revision, lines=len(rows))
