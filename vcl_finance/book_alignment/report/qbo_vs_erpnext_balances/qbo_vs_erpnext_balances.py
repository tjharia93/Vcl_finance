"""QBO vs ERPNext balances, against the signed accounts, as at a date.

QBO comes from `QBO Balance Snapshot` rows for that exact date, written by the
on-prem pull: Frappe Cloud cannot call QBO. No snapshot means no QBO column —
blank, never zeros that look real. ERPNext is computed live from the GL. The
audited figure shows only where an Audited FS Line has an amount for the date.

Every figure is on its line's natural side (see rules.py). Balance-sheet
accounts are cumulative to the date; P&L accounts run from the start of the
ERPNext fiscal year containing it — the same basis as ERPNext's Trial Balance,
which is why the ERPNext check row is not zero: prior-year P&L was never closed
(no Period Closing Voucher), so its trial balance does not balance.
"""
from collections import defaultdict

import frappe
from frappe.utils import flt, format_datetime, fmt_money, getdate

from vcl_finance.book_alignment import rules

DEFAULT_AS_AT = "2025-12-31"
DEFAULT_COMPANY = "Vimit Converters Limited"


def execute(filters=None):
    f = frappe._dict(filters or {})
    as_at = getdate(f.as_at or DEFAULT_AS_AT)
    company = f.company or DEFAULT_COMPANY
    prefer = f.erp_basis or rules.PREFER_SEED
    by_account = f.group_by == "Account"
    notes = []

    lines = frappe.get_all(
        "Audited FS Line", fields=["name", "label", "note", "section", "nature"], order_by="`order` asc")
    if not lines:
        frappe.throw("No Audited FS Lines yet. The seed patch runs on migrate.")
    natures = {l.name: l.nature for l in lines}

    audited = {
        r.parent: flt(r.amount)
        for r in frappe.get_all(
            "Audited FS Line Amount", filters={"as_at": as_at, "parenttype": "Audited FS Line"},
            fields=["parent", "amount"])
    }
    if not audited:
        notes.append(f"No audited amounts are recorded for {as_at}: the audited columns are blank.")

    crosswalk = frappe.get_all(
        "QBO ERP Account Crosswalk",
        fields=["qbo_account", "fs_line", "decision", "erp_account", "proposed_erp_account"])

    qbo, qbo_line_of, qbo_basis, qbo_names = qbo_side(as_at, crosswalk, notes)
    erp, erp_map = erp_side(as_at, company, crosswalk, prefer, notes)
    erp_line_of = {a: erp_map[a][0] if a in erp_map else rules.UNASSIGNED for a in erp}

    q_tot = rules.line_totals(qbo, qbo_line_of, natures) if qbo is not None else None
    e_tot = rules.line_totals(erp, erp_line_of, natures)

    data = []
    for section in ("Balance sheet", "P&L"):
        data.append({"line": section if section != "P&L" else f"Profit and loss - year to {as_at}",
                     "row_id": section, "indent": 0, "bold": 1})
        for l in (l for l in lines if l.section == section):
            erp_accts = sorted(a for a in erp if erp_line_of[a] == l.name)
            qbo_accts = sorted((a for a in (qbo or {}) if qbo_line_of[a] == l.name), key=lambda a: qbo_names[a])
            row = line_row(l, audited.get(l.name), q_tot and q_tot[l.name], e_tot[l.name])
            row.update(row_id=l.name, parent_row=section, indent=1,
                       erp_basis=count_basis(erp_map.get(a, ("", "Unassigned"))[1] for a in erp_accts),
                       qbo_basis=count_basis(qbo_basis[a] for a in qbo_accts))
            data.append(row)
            if by_account:
                sign = rules.natural(l.nature)
                for a in qbo_accts:
                    data.append({"line": f"QBO · {qbo_names[a]}", "row_id": f"q-{a}", "parent_row": l.name,
                                 "indent": 2, "qbo": sign * qbo[a], "qbo_basis": qbo_basis[a]})
                for a in erp_accts:
                    basis = erp_map.get(a, ("", "Unassigned", []))
                    via = f" via QBO {', '.join(basis[2])}" if basis[2] else ""
                    data.append({"line": f"ERPNext · {a}", "row_id": f"e-{a}", "parent_row": l.name,
                                 "indent": 2, "erpnext": sign * erp[a], "erp_basis": basis[1] + via})

    aud_pbt = rules.pbt(audited) if all(k in audited for k in rules.PBT_PLUS + rules.PBT_MINUS) else None
    data.append(dict(line_row(frappe._dict(label="Profit before tax"), aud_pbt,
                              q_tot and rules.pbt(q_tot), rules.pbt(e_tot)), row_id="pbt", indent=0, bold=1))
    data.append({"line": "Trial balance check (should be 0), Dr + / Cr -", "row_id": "tb", "indent": 0, "bold": 1,
                 "audited": None, "qbo": None if qbo is None else sum(qbo.values()), "erpnext": sum(erp.values())})

    tb = sum(erp.values())
    if abs(tb) > 0.5:
        notes.append(
            f"ERPNext's trial balance at {as_at} is out of balance by {fmt_money(tb, 2)} (debits exceed credits"
            " when positive). Shown, not hidden."
            + (" Known since 24-Sep-2026: the gap is already in the opening balances; prior-year P&L was never"
               " closed to reserves (inferred, not proven)." if str(as_at) == DEFAULT_AS_AT else ""))
    conflicts = sorted(a for a in erp if "conflict" in erp_map.get(a, ("", ""))[1])
    if conflicts:
        notes.append("QBO accounts disagree on the line for " + ", ".join(conflicts) +
                     ": the first in QBO-id order was used. A decision on the crosswalk settles it.")
    notes.append(f"ERPNext accounts are placed on lines by: {prefer}. Group by Account to see each one's basis.")

    return columns(), data, "<br>".join(frappe.utils.escape_html(n) for n in notes)


def qbo_side(as_at, crosswalk, notes):
    snaps = frappe.get_all(
        "QBO Balance Snapshot", filters={"as_at": as_at},
        fields=["qbo_account", "balance", "pulled_at"], order_by="pulled_at asc")
    if not snaps:
        notes.insert(0, f"NO QBO SNAPSHOT FOR {as_at}. The QBO columns are blank, not zero. Pull one on-prem:"
                        f" CommandCentre/tools/pull_qbo_tb_snapshot.py --as-at {as_at}")
        return None, {}, {}, {}
    notes.insert(0, f"QBO: {len(snaps)} trial-balance rows at {as_at}, pulled "
                    f"{format_datetime(snaps[-1].pulled_at)}.")
    qbo = {s.qbo_account: flt(s.balance) for s in snaps}
    accounts = {
        a.name: a for a in frappe.get_all(
            "QBO Account", filters={"name": ["in", list(qbo)]},
            fields=["name", "fully_qualified_name", "classification", "account_type"])
    }
    fs_line = {r.qbo_account: r.fs_line for r in crosswalk if r.fs_line}
    line_of, basis = {}, {}
    for a in qbo:
        if a in fs_line:
            line_of[a], basis[a] = fs_line[a], "Crosswalk"
        else:
            acct = accounts.get(a) or frappe._dict()
            line_of[a] = rules.qbo_line(acct.fully_qualified_name, acct.classification, acct.account_type)
            basis[a] = "Rule (no crosswalk line)"
    names = {a: (accounts[a].fully_qualified_name if a in accounts else a) for a in qbo}
    return qbo, line_of, basis, names


def erp_side(as_at, company, crosswalk, prefer, notes):
    from erpnext.accounts.utils import get_fiscal_year

    fy_start = get_fiscal_year(as_at, company=company)[1]
    if frappe.db.exists("Period Closing Voucher", {"company": company, "docstatus": 1,
                                                   "period_end_date": [">=", fy_start]}):
        notes.append("A Period Closing Voucher exists in this fiscal year: its entries are included, so P&L"
                     " lines may read zero after closing.")
    rows = frappe.db.sql(
        """
        select gle.account, sum(gle.debit) - sum(gle.credit)
        from `tabGL Entry` gle
        join `tabAccount` acc on acc.name = gle.account
        where gle.company = %(company)s and gle.is_cancelled = 0 and gle.posting_date <= %(as_at)s
          and (acc.report_type = 'Balance Sheet' or gle.posting_date >= %(fy_start)s)
        group by gle.account
        """,
        {"company": company, "as_at": as_at, "fy_start": fy_start},
    )
    erp = {a: flt(v) for a, v in rows if abs(flt(v)) >= 0.005}
    notes.append(f"ERPNext: {company}, live GL. Balance sheet to {as_at}; P&L from {fy_start}.")
    return erp, rules.erp_line_map(crosswalk, prefer=prefer)


def line_row(l, aud, q, e):
    return {
        "line": l.label, "note": l.get("note"), "audited": aud, "qbo": q, "erpnext": e,
        "qbo_less_erp": diff(q, e), "qbo_less_audited": diff(q, aud), "erp_less_audited": diff(e, aud),
    }


def diff(a, b):
    return None if a is None or b is None else a - b


def count_basis(bases):
    counts = defaultdict(int)
    for b in bases:
        counts[b] += 1
    return " · ".join(f"{b} {n}" for b, n in sorted(counts.items()))


def columns():
    money = lambda fieldname, label: {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "width": 150}
    return [
        {"fieldname": "line", "label": "Audited line", "fieldtype": "Data", "width": 380},
        {"fieldname": "note", "label": "Note", "fieldtype": "Data", "width": 70},
        money("audited", "Audited"),
        money("qbo", "QBO"),
        money("erpnext", "ERPNext"),
        money("qbo_less_erp", "QBO - ERPNext"),
        money("qbo_less_audited", "QBO - Audited"),
        money("erp_less_audited", "ERPNext - Audited"),
        {"fieldname": "erp_basis", "label": "ERPNext basis", "fieldtype": "Data", "width": 260},
        {"fieldname": "qbo_basis", "label": "QBO basis", "fieldtype": "Data", "width": 200},
    ]
