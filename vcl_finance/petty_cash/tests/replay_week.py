"""Replay a REAL week as a native week under the dev-only Trial float, and reconcile it.

    bench --site <site> execute vcl_finance.petty_cash.tests.replay_week.run

Unlike the live checks this is NOT rolled back: the Trial week stays on the dev copy so
it can be opened and clicked through next to the real one. ``teardown`` removes it.

The reconciliation is anchored on a FIXED SOURCE LIST: the real week's sheet rows,
exported to a file before anything is keyed. Every later stage is compared to that list,
not to another stage and not to the old week's preview.

    source list -> entries -> sheet totals -> posting preview -> draft journal -> QuickBooks staging
"""
import json
import os

import frappe
from frappe.utils import flt

REAL = "PCS-2026-00051"
TRIAL_FLOAT = "Trial"
OUT_DIR = "/home/tanujharia/projects/reports"
SRC_FILE = os.path.join(OUT_DIR, "petty-cash-replay-source-PCS-2026-00051.json")
RES_FILE = os.path.join(OUT_DIR, "petty-cash-replay-results-20261115.json")
RESULTS = []
TABLES = {"vouchers": "Petty Cash Voucher", "wages_entries": "Petty Cash Wages Entry", "loan_entries": "Petty Cash Loan Entry",
          "misc_entries": "Petty Cash Misc Entry", "parking_entries": "Petty Cash Parking Entry"}


def check(name, fn):
    try:
        out = fn()
        ok, detail = (out is not False), ("" if out is not False else "returned False")
        if isinstance(out, str):
            ok, detail = False, out
    except Exception as e:      # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {str(e)[:260]}"
    RESULTS.append((name, ok, detail))
    return ok


def money(v):
    return round(flt(v), 2)


def ensure_trial_float():
    """Dev copy only: add Trial to the float choices with Property Setters (not in code)."""
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter
    for dt in ("Petty Cash Sheet", "Petty Cash Entry"):
        opts = frappe.get_meta(dt).get_field("float").options or ""
        if TRIAL_FLOAT not in opts.split("\n"):
            make_property_setter(dt, "float", "options", opts.rstrip("\n") + "\n" + TRIAL_FLOAT, "Text")
    frappe.clear_cache(doctype="Petty Cash Sheet"); frappe.clear_cache(doctype="Petty Cash Entry")


def export_source():
    """The fixed source list: the real week's ROWS (not its entries), with each row's review state."""
    from vcl_finance.petty_cash.mirror import TABLE_MAP, is_real_row
    sheet = frappe.get_doc("Petty Cash Sheet", REAL)
    rows = []
    for table, dt in TABLES.items():
        for r in sheet.get(table):
            if not is_real_row(r, TABLE_MAP[table]["amount_field"]):
                continue
            e = frappe.db.get_value("Petty Cash Entry", {"origin_row": r.name, "sync_state": ("!=", "Orphaned")},
                                    ["name", "status", "approved_by", "withdrawal_reason", "vat_treatment", "vat_amount", "supplier_ref",
                                     "company", "posting_account", "qbo_account", "exclude_from_posting", "source_type", "source_key"], as_dict=True) or {}
            d = r.as_dict()
            rows.append({"table": table, "row": r.name, "txn_date": str(r.txn_date) if r.txn_date else None,
                         "amount": money(d.get("amount_issued") if table == "loan_entries" else d.get("amount")),
                         "cash_in": int(d.get("cash_in") or 0), "category": d.get("category") or "", "kind": d.get("kind") or "",
                         "entry_type": d.get("entry_type") or "", "vehicle": d.get("vehicle") or "", "voucher_no": d.get("voucher_no") or "",
                         "recipient": d.get("recipient") or "", "staff_id": d.get("staff_id") or "", "reason": d.get("reason") or "",
                         "notes": d.get("notes") or "", "pc_received": int(d.get("pc_received") or 0), "etr_received": int(d.get("etr_received") or 0),
                         "paye": int(d.get("paye") or 0), "receipt": d.get("receipt") or None, "cancelled": int(d.get("cancelled") or 0),
                         "cancel_remark": d.get("cancel_remark") or None, "entry": dict(e)})
    src = {"sheet": REAL, "week_ending": str(sheet.week_ending), "float": sheet.float, "company": sheet.company,
           "opening_balance": money(sheet.opening_balance), "exported_on": str(frappe.utils.now_datetime()), "rows": rows}
    json.dump(src, open(SRC_FILE, "w"), indent=1, default=str)
    return src


def kind_of(r):
    if r["table"] == "vouchers":
        return "voucher"
    if r["table"] == "wages_entries":
        return {"Wage": "wage", "Commission": "commission", "Piecework": "piecework", "Overtime": "overtime"}[r["entry_type"] or "Wage"]
    if r["table"] == "loan_entries":
        return "loan"
    if r["table"] == "misc_entries":
        return "bike" if r["kind"] == "Bike Fuel" else "forklift"
    return "parking"


def line_key(source_type, source_key, txn_date, amount, cash_in, recipient, voucher_no):
    return (source_type or "", source_key or "", str(txn_date or ""), money(amount), int(cash_in or 0), (recipient or "").strip(), (voucher_no or "").strip())


def teardown():
    frappe.set_user("Administrator")
    name = frappe.db.get_value("Petty Cash Sheet", {"float": TRIAL_FLOAT}, "name")
    while name:
        we = str(frappe.db.get_value("Petty Cash Sheet", name, "week_ending"))
        for j in frappe.get_all("Journal Entry", {"cheque_no": ("like", f"PC-{TRIAL_FLOAT}-{we}%")}, pluck="name"):
            frappe.db.set_value("Journal Entry", j, "docstatus", 0); frappe.delete_doc("Journal Entry", j, force=1, ignore_permissions=True)
        frappe.db.delete("QBO Petty Cash Journal", {"week_ending": we, "float": TRIAL_FLOAT})
        for e in frappe.get_all("Petty Cash Entry", {"week_sheet": name}, pluck="name"):
            frappe.db.delete("Comment", {"reference_name": e}); frappe.db.delete("Version", {"docname": e}); frappe.db.delete("Petty Cash Entry", {"name": e})
        frappe.db.delete("Comment", {"reference_name": name}); frappe.db.delete("Version", {"docname": name}); frappe.db.delete("Petty Cash Sheet", {"name": name})
        name = frappe.db.get_value("Petty Cash Sheet", {"float": TRIAL_FLOAT}, "name")
    frappe.db.commit()
    print("RESULT", json.dumps({"sheets": frappe.db.count("Petty Cash Sheet"), "entries": frappe.db.count("Petty Cash Entry")}))


def run():
    frappe.set_user("Administrator")
    from vcl_finance.petty_cash import api, entry_api, vat, weekpost, weeks, resolve
    from vcl_finance.petty_cash import post as P
    report = {}
    ensure_trial_float()
    real_before = _real_fingerprint()

    # ------------------------------------------------------------------ 0. source
    src = export_source()
    rows = src["rows"]
    live_rows = [r for r in rows if not r["cancelled"]]
    S_out = money(sum(r["amount"] for r in live_rows if not r["cash_in"]))
    S_in = money(sum(r["amount"] for r in live_rows if r["cash_in"]))
    S_cat = {}
    for r in live_rows:
        if r["table"] == "vouchers" and not r["cash_in"]:
            S_cat[r["category"]] = money(S_cat.get(r["category"], 0) + r["amount"])
    report["source"] = {"file": SRC_FILE, "rows": len(rows), "out_lines": sum(1 for r in live_rows if not r["cash_in"]),
                        "in_lines": sum(1 for r in live_rows if r["cash_in"]), "total_out": S_out, "total_in": S_in, "by_category": S_cat}
    check(f"source list fixed before the replay: {len(rows)} rows, out {S_out:,.2f}, in {S_in:,.2f}", lambda: len(rows) == 65 and S_out == 222576 and S_in == 25120)

    # ------------------------------------------------------------------ 1. key the week
    we = src["week_ending"]
    s = frappe.new_doc("Petty Cash Sheet")
    s.update({"week_ending": we, "float": TRIAL_FLOAT, "custodian_name": "Shiro (replay)", "status": "Draft",
              "opening_balance": src["opening_balance"], "native_lines": 1})
    s.insert()
    trial = s.name
    pairs = []          # (source row, trial entry)
    for r in rows:
        f = {k: r[k] for k in ("amount", "recipient", "voucher_no", "notes", "staff_id", "reason", "category", "vehicle")}
        f.update({"cash_in": r["cash_in"], "pc_received": r["pc_received"], "etr_received": r["etr_received"], "paye": r["paye"]})
        eid = api.quick_entry(trial, kind_of(r), txn_date=r["txn_date"], **f)["entry_id"]
        if r["receipt"]:
            api.attach_receipt(trial, row_name=eid, file_url=r["receipt"])
        pairs.append((r, eid))
    # the review state the real lines carry today, replicated line for line
    for r, eid in pairs:
        e = r["entry"]
        if e.get("company") and e["company"] != src["company"]:
            d = frappe.get_doc("Petty Cash Entry", eid); d.company = e["company"]; d.save()
        if e.get("vat_treatment"):
            vat.set_vat(eid, e["vat_treatment"], e.get("vat_amount") or None, e.get("supplier_ref"))
        if e.get("status") in ("Approved", "Withdrawn"):
            entry_api.approve_entry(eid)
        if e.get("status") == "Withdrawn":
            entry_api.withdraw_entry(eid, reason=e.get("withdrawal_reason") or "Replayed from the real week")
    frappe.db.commit()

    T = lambda: frappe.get_all("Petty Cash Entry", {"week_sheet": trial},
                               ["name", "source_type", "source_key", "category", "txn_date", "amount", "cash_in", "recipient", "voucher_no", "status",
                                "company", "receipt", "pc_received", "etr_received", "vat_treatment", "cancelled", "journal_entry", "qbo_queue"], limit_page_length=0)
    R = frappe.get_all("Petty Cash Entry", {"origin_sheet": REAL, "sync_state": ("!=", "Orphaned")},
                       ["name", "source_type", "source_key", "category", "txn_date", "amount", "cash_in", "recipient", "status", "company", "receipt",
                        "pc_received", "etr_received", "vat_treatment", "cancelled", "payload"], limit_page_length=0)

    # ------------------------------------------------------------------ 2. source -> entries
    te = T()
    src_keys = sorted((r["table"], r["txn_date"], r["amount"], r["cash_in"], r["recipient"].strip(), r["voucher_no"].strip(), r["category"],
                       r["kind"] or r["entry_type"] or r["vehicle"]) for r in rows)
    def ent_keys():
        out = []
        for r, eid in pairs:
            e = frappe.db.get_value("Petty Cash Entry", eid, ["txn_date", "amount", "cash_in", "recipient", "voucher_no", "category", "source_type", "source_key"], as_dict=True)
            sub = e.source_type if r["table"] == "misc_entries" else (e.source_key if r["table"] in ("wages_entries", "parking_entries") else "")
            out.append((r["table"], str(e.txn_date), money(e.amount), int(e.cash_in), (e.recipient or "").strip(), (e.voucher_no or "").strip(), e.category or "", sub))
        return sorted(out)
    check("source -> entries: one entry per source row (65), none extra", lambda: len(te) == len(rows) == len(pairs))
    check("source -> entries: every row's date, amount, direction, recipient, slip no., category and type carried exactly", lambda: ent_keys() == src_keys)
    check("source -> entries: total out and in equal the source list",
          lambda: (money(sum(e.amount for e in te if not e.cash_in and not e.cancelled)), money(sum(e.amount for e in te if e.cash_in and not e.cancelled))) == (S_out, S_in))
    check("source -> entries: receipts carried (same file on the same line)",
          lambda: all((frappe.db.get_value("Petty Cash Entry", eid, "receipt") or None) == r["receipt"] for r, eid in pairs))
    check("entries are native, with no rows stored on the Trial sheet",
          lambda: all(frappe.db.get_value("Petty Cash Entry", eid, "sync_state") == "Native" for _, eid in pairs)
          and sum(frappe.db.count(dt, {"parent": trial}) for dt in TABLES.values()) == 0)

    # line-for-line against the REAL week's entries (the second comparison, not the anchor)
    def shape(e, real):
        vno = (json.loads(e.payload or "{}").get("voucher_no") or "") if real else (e.voucher_no or "")
        return (e.source_type, e.source_key or "", e.category or "", str(e.txn_date), money(e.amount), int(e.cash_in), (e.recipient or "").strip(),
                (vno or "").strip(), e.status, e.company, e.receipt or None, int(e.pc_received or 0), int(e.etr_received or 0), e.vat_treatment or "", int(e.cancelled or 0))
    check("second comparison: every Trial entry equals a real entry on 15 facts, including sign-off state and VAT (65 of 65)",
          lambda: sorted(shape(e, False) for e in te) == sorted(shape(e, True) for e in R))

    # ------------------------------------------------------------------ 3. entries -> sheet totals
    h = frappe.db.get_value("Petty Cash Sheet", trial, ["total_out", "total_in", "expected_close", "opening_balance"], as_dict=True)
    rh = frappe.db.get_value("Petty Cash Sheet", REAL, ["total_out", "total_in", "expected_close", "opening_balance"], as_dict=True)
    check(f"entries -> sheet totals: stored totals equal the source list (out {S_out:,.2f}, in {S_in:,.2f})", lambda: (money(h.total_out), money(h.total_in)) == (S_out, S_in))
    check("entries -> sheet totals: expected close = opening - out + in, computed from the source list (-225,229.00)",
          lambda: money(h.expected_close) == money(src["opening_balance"] - S_out + S_in) == -225229)
    sm_t, sm_r = api._summary(frappe.get_doc("Petty Cash Sheet", trial)), api._summary(frappe.get_doc("Petty Cash Sheet", REAL))
    check("totals by category equal the source list", lambda: {k: money(v) for k, v in sm_t["cat_out"].items() if v} == S_cat)
    drop = ("native", "lines_revision", "cash_reconfirm_needed", "balance_label")
    check("second comparison: summary equals the real week's on every figure",
          lambda: {k: v for k, v in sm_t.items() if k not in drop} == {k: v for k, v in sm_r.items() if k not in drop})
    report["as_keyed"] = {"sheet": trial, "total_out": money(h.total_out), "total_in": money(h.total_in), "expected_close": money(h.expected_close), "by_category": S_cat}

    # ------------------------------------------------------------------ 4. posting preview, as the week stands today
    def classify(week_ending, float_name, real):
        rows_, b = weekpost._survey(week_ending, float_name)
        out = []
        for state, lines in b.items():
            for e in lines:
                vno = frappe.db.get_value("Petty Cash Entry", e["name"], "payload" if real else "voucher_no")
                vno = (json.loads(vno or "{}").get("voucher_no") or "") if real else (vno or "")
                out.append(line_key(e["source_type"], e.get("source_key"), e.get("txn_date"), e["amount"], e.get("cash_in"), e.get("recipient"), vno)
                           + (state, e.get("_account") or "", e.get("_reason") or "", e.get("_treatment") or "", money(e.get("_net")), money(e.get("_vat"))))
        return sorted(out), {k: len(v) for k, v in b.items()}
    ct, nt = classify(we, TRIAL_FLOAT, False)
    cr, nr = classify(we, "Cash", True)
    report["preview_as_keyed"] = {"trial": nt, "real": nr}
    check(f"posting preview, as the week stands: same count in each state as the real week ({nr})", lambda: nt == nr)
    check("posting preview: line for line the same state, account, reason, VAT treatment, net and VAT as the real week (65 of 65)", lambda: ct == cr)
    src_ready = lambda states: money(sum(x[3] for x in ct if x[7] in states and not x[4]))
    check("posting preview accounts for every source line exactly once (ready + held + excluded + posted = 65)",
          lambda: sum(nt.values()) == len(rows) and money(sum(x[3] for x in ct if not x[4])) == S_out)

    # ------------------------------------------------------------------ 5. take the TRIAL week to draft journals
    # Decisions made here are for the trial only and are recorded: what the real week needs
    # before it can post is not decided by a replay.
    decisions = {"signed_for_trial": [], "vat_set_for_trial": [], "excluded_for_trial": []}
    for e in T():
        if e.cancelled:
            continue
        if e.status != "Approved":
            entry_api.approve_entry(e.name); decisions["signed_for_trial"].append(e.name)
    rows_, b = weekpost._survey(we, TRIAL_FLOAT)
    for e in b["blocked"]:
        if e.get("_reason") and e["_reason"] != "Not approved yet":
            entry_api.set_exclude(e["name"], 1, reason="Replay only: " + e["_reason"])
            decisions["excluded_for_trial"].append({"entry": e["name"], "amount": money(e["amount"]), "why": e["_reason"]})
    rows_, b = weekpost._survey(we, TRIAL_FLOAT)
    for e in b["ready"]:
        if e.get("_vat_problem"):
            vat.set_vat(e["name"], "No VAT"); decisions["vat_set_for_trial"].append(e["name"])
    frappe.db.commit()
    rev = lambda: frappe.db.get_value("Petty Cash Sheet", trial, "lines_revision")
    report["cash"] = close_and_confirm(trial)
    check("cash: the negative expected close cannot be entered as the count", lambda: report["cash"]["negative_count_refused"])
    check("cash: closed on a count of 0, the variance left visible and explicitly confirmed, not made to disappear",
          lambda: report["cash"]["cash_counted"] == 0 and report["cash"]["variance"] == money(0 - h.expected_close) and report["cash"]["confirmed_by"])
    api.approve_week(trial, revision=rev())
    rows_, b = weekpost._survey(we, TRIAL_FLOAT)
    ready = b["ready"]
    P_ready = money(sum(flt(e["amount"]) for e in ready))
    P_excl = money(sum(flt(e["amount"]) for e in b["excluded"] if not e.get("cash_in")))
    P_blocked = money(sum(flt(e["amount"]) for e in b["blocked"]))
    report["decisions_for_trial_only"] = {k: (len(v) if k != "excluded_for_trial" else v) for k, v in decisions.items()}
    report["preview_before_posting"] = {"ready_lines": len(ready), "ready": P_ready, "excluded_out": P_excl, "held": P_blocked}
    check("preview before posting: ready + excluded + held (cash out) = the source list's total out",
          lambda: money(P_ready + P_excl + P_blocked) == S_out or f"{P_ready} + {P_excl} + {P_blocked} != {S_out}")
    check("preview before posting: nothing held", lambda: not b["blocked"])
    pv = weekpost.preview(we, TRIAL_FLOAT)
    made = weekpost.post_erp(we, TRIAL_FLOAT)["created"]
    js = frappe.get_all("Journal Entry", {"cheque_no": ("like", f"PC-{TRIAL_FLOAT}-{we}%")}, ["name", "company", "docstatus", "total_debit", "total_credit"])
    report["journals"] = [dict(j) for j in js]
    check("draft journal(s): one per company with ready lines, all drafts", lambda: len(js) == len({e.get("company") or resolve.COMPANY for e in ready}) and all(j.docstatus == 0 for j in js))
    check("draft journal(s): each balances", lambda: all(money(j.total_debit) == money(j.total_credit) for j in js))
    check(f"preview -> draft journal: journal debits total the ready lines from the preview ({P_ready:,.2f})",
          lambda: money(sum(j.total_debit for j in js)) == P_ready or f"{money(sum(j.total_debit for j in js))} != {P_ready}")
    linked = {e.name: e.journal_entry for e in T() if e.journal_entry}
    check("every ready line is on a journal and no other line is", lambda: set(linked) == {e["name"] for e in ready})
    # the journal's expense side, by account, against the source list routed through the Posting Map independently
    by_acct_src = {}
    for e in ready:
        by_acct_src[e["_account"]] = money(by_acct_src.get(e["_account"], 0) + flt(e["_net"] if e.get("_net") else e["amount"]))
    by_acct_je = {}
    for j in js:
        for a in frappe.get_all("Journal Entry Account", {"parent": j.name}, ["account", "debit_in_account_currency"]):
            if flt(a.debit_in_account_currency):
                by_acct_je[a.account] = money(by_acct_je.get(a.account, 0) + flt(a.debit_in_account_currency))
    report["journal_debits_by_account"] = by_acct_je
    check("draft journal: debits by account equal the ready lines grouped by their mapped account (VAT aside)",
          lambda: {k: v for k, v in by_acct_je.items() if not k.startswith("VAT")} == by_acct_src
          or f"journal {by_acct_je} vs lines {by_acct_src}")
    res = weekpost.posting_results(trial)
    check("posting record lists every company's journal; none hidden", lambda: {r["journal_entry"] for r in res["companies"]} == {j.name for j in js} and not res["companies_without_record"])

    # ------------------------------------------------------------------ 6. QuickBooks staging: every posted line has a disposition
    st = weekpost.stage_qbo(we, TRIAL_FLOAT)
    posted = {e["name"] for e in weekpost._survey(we, TRIAL_FLOAT)[1]["posted"]}
    staged = set(frappe.get_all("Petty Cash Entry", {"qbo_queue": st["queue_row"]}, pluck="name"))
    held = {n for x in st["held"] for n in x["entries"]}
    outside = {n for x in st["not_for_quickbooks"] for n in x["entries"]}
    report["quickbooks"] = {"row": st["queue_row"], "staged_lines": len(staged), "staged_amount": st["amount"],
                            "held": [{k: x[k] for k in ("reason", "lines")} for x in st["held"]],
                            "outside_scope": [{k: x[k] for k in ("reason", "lines", "amount")} for x in st["not_for_quickbooks"]], "approved": st["approved"]}
    check("QuickBooks: every ERPNext-posted line has exactly one disposition (staged, held with a reason, or outside QuickBooks' scope); none unexplained",
          lambda: staged | held | outside == posted and not (staged & held) and not (staged & outside) and not (held & outside))
    check("QuickBooks: staged amount equals the gross of the staged lines", lambda: money(st["amount"]) == money(sum(flt(e["amount"]) for e in ready if e["name"] in staged)))
    check("QuickBooks: staging row is unapproved (nothing is pushed)", lambda: st["approved"] is False)
    frappe.db.commit()

    # ------------------------------------------------------------------ 7. the real week was not touched; nothing orphaned
    check("the REAL week is byte-for-byte as it was (entries, statuses, totals)", lambda: _real_fingerprint() == real_before)
    check("no orphan created (still 1,276)", lambda: frappe.db.count("Petty Cash Entry", {"sync_state": "Orphaned"}) == 1276)
    rep = weeks.coverage_report()
    check("the week lookup still returns every entry exactly once", lambda: not rep["returned_twice"] and not rep["never_returned"])

    report["chain"] = {"source_out": S_out, "entries_out": money(sum(e.amount for e in T() if not e.cash_in and not e.cancelled)),
                       "sheet_total_out": money(frappe.db.get_value("Petty Cash Sheet", trial, "total_out")),
                       "preview_ready": P_ready, "preview_excluded_out": P_excl, "journal_debits": money(sum(j.total_debit for j in js)),
                       "quickbooks_staged": money(st["amount"])}
    report["checks"] = [{"name": n, "pass": ok, "detail": d} for n, ok, d in RESULTS]
    json.dump(report, open(RES_FILE, "w"), indent=1, default=str)
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} passed")
    print("CHAIN", json.dumps(report["chain"]))
    print("TRIAL SHEET", trial)


def close_and_confirm(trial):
    """Close the Trial week honestly.

    A replayed week has no tin, so there is NO physical count. The expected close is
    -225,229, which no count can equal. The week is closed on a count of 0 — stated here
    as a placeholder, not as cash anybody counted — so the whole negative book balance
    stands as a visible variance, which is then explicitly confirmed. It is a test
    decision, recorded with the count, the variance and who confirmed.
    """
    from vcl_finance.petty_cash import api
    rev = lambda: frappe.db.get_value("Petty Cash Sheet", trial, "lines_revision")
    expected = money(frappe.db.get_value("Petty Cash Sheet", trial, "expected_close"))
    refused = False
    try:
        api.close_week(trial, expected, revision=rev())
    except Exception as e:      # noqa: BLE001
        refused = "cannot be negative" in str(e)
    if not frappe.db.get_value("Petty Cash Sheet", trial, "opening_actual_source"):
        # No tin, so no opening count either. 0 is a stated placeholder; the whole book
        # opening balance therefore shows as the historical difference.
        api.set_opening_count(trial, 0, note="REPLAY ONLY. A replayed week has no tin: no opening count exists. 0 is a placeholder, "
                                             "so the book opening balance of (27,773) shows as the historical difference.")
    api.close_week(trial, 0, revision=rev())
    needed = frappe.db.get_value("Petty Cash Sheet", trial, "cash_reconfirm_needed")
    blocked = False
    try:
        api.approve_week(trial, revision=rev())
    except Exception as e:      # noqa: BLE001
        blocked = "Acknowledge it" in str(e)
    api.confirm_cash(trial, revision=rev(), reason="REPLAY ONLY. Test decision: no physical cash exists for a replayed week, so the "
                                                   "closing placeholder of 0 against movements of 197,456 out is not a real discrepancy.",
                     evidence="None. This acknowledgement proves the software path, not a reconciliation.")
    h = frappe.db.get_value("Petty Cash Sheet", trial, ["cash_count_end", "expected_close", "variance", "cash_confirmed_by", "cash_confirmed_on",
                                                        "closed_by", "expected_close_at_count", "opening_balance"], as_dict=True)
    frappe.db.commit()
    pos = api.cash_position(trial)
    return {"position": pos,
            "physical_count_exists": False, "cash_counted": money(h.cash_count_end), "cash_counted_is": "placeholder: a replayed week has no tin to count",
            "opening_balance": money(h.opening_balance), "expected_close": money(h.expected_close), "variance": money(h.variance),
            "negative_count_refused": refused, "approval_blocked_until_confirmed": blocked and bool(needed),
            "closed_by": h.closed_by, "confirmed_by": h.cash_confirmed_by, "confirmed_on": str(h.cash_confirmed_on),
            "confirmation_is": "a test decision, not a cash reconciliation"}


def reclose():
    """Redo the Trial week's close honestly on an already replayed week: reopen (which
    discards the draft journal and the staging row), close on a real figure, confirm,
    approve, post and stage again. Updates the results file."""
    from vcl_finance.petty_cash import api, weekpost
    frappe.set_user("Administrator")
    trial = frappe.db.get_value("Petty Cash Sheet", {"float": TRIAL_FLOAT}, "name")
    we = str(frappe.db.get_value("Petty Cash Sheet", trial, "week_ending"))
    before = frappe.db.get_value("Petty Cash Sheet", trial, ["cash_count_end", "variance", "status"], as_dict=True)
    discarded = api.reopen_week(trial)["discarded"]
    cash = close_and_confirm(trial)
    api.approve_week(trial, revision=frappe.db.get_value("Petty Cash Sheet", trial, "lines_revision"))
    made = weekpost.post_erp(we, TRIAL_FLOAT)["created"]
    st = weekpost.stage_qbo(we, TRIAL_FLOAT)
    frappe.db.commit()
    js = frappe.get_all("Journal Entry", {"cheque_no": ("like", f"PC-{TRIAL_FLOAT}-{we}%")}, ["name", "company", "docstatus", "total_debit", "total_credit"])
    report = json.load(open(RES_FILE))
    report["cash"] = cash
    report["cash_first_attempt"] = {"cash_counted": money(before.cash_count_end), "variance": money(before.variance),
                                    "what_was_wrong": "the expected close (-225,229) was entered as the count so that the variance read 0"}
    report["journals"] = [dict(j) for j in js]
    report["quickbooks"]["row"] = st["queue_row"]; report["quickbooks"]["staged_amount"] = st["amount"]
    report["chain"]["journal_debits"] = money(sum(j.total_debit for j in js)); report["chain"]["quickbooks_staged"] = money(st["amount"])
    json.dump(report, open(RES_FILE, "w"), indent=1, default=str)
    print("RESULT", json.dumps({"discarded": discarded, "cash": cash, "journals": [(j.name, j.docstatus, j.total_debit, j.total_credit) for j in js],
                                "staged": st["amount"], "status": frappe.db.get_value("Petty Cash Sheet", trial, "status")}, default=str))


def _real_fingerprint():
    import hashlib
    rows = frappe.get_all("Petty Cash Entry", {"origin_sheet": REAL}, ["name", "amount", "status", "sync_state", "origin_hash", "txn_date", "category",
                                                                      "journal_entry", "vat_treatment", "company", "modified"], order_by="name")
    head = frappe.db.get_value("Petty Cash Sheet", REAL, ["status", "total_out", "total_in", "expected_close", "modified"], as_dict=True)
    return hashlib.md5(json.dumps([rows, head], default=str, sort_keys=True).encode()).hexdigest()
