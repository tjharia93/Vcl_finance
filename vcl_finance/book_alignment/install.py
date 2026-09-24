"""Seed the audited lines and one crosswalk row per QBO account.

Runs from the patch (sites where vcl_finance is already installed) and from
after_install (fresh sites, where install_app stamps patches done without running
them). Idempotent and additive: it only creates what is missing and never
touches a row that exists, because once seeded these rows are Tanuj's to edit.
"""
import json
import os

import frappe

from vcl_finance.book_alignment import rules

PROPOSALS = os.path.join(os.path.dirname(__file__), "seed", "crosswalk_proposals_2026-09-24.json")


def seed():
    seed_fs_lines()
    seed_crosswalk()
    frappe.db.commit()


def seed_fs_lines():
    for order, (code, label, note, amount, nature, section) in enumerate(rules.FS_LINES_FY2025, 1):
        if frappe.db.exists("Audited FS Line", code):
            doc = frappe.get_doc("Audited FS Line", code)
            if any(str(r.as_at) == rules.FY2025_AS_AT for r in doc.amounts):
                continue
        else:
            doc = frappe.get_doc({
                "doctype": "Audited FS Line", "code": code, "label": label, "note": note,
                "section": section, "nature": nature, "order": order,
            })
        doc.append("amounts", {"as_at": rules.FY2025_AS_AT, "amount": amount, "source": rules.FY2025_SOURCE})
        if doc.is_new():
            doc.insert(ignore_permissions=True)
        else:
            doc.save(ignore_permissions=True)


def seed_crosswalk():
    """One row per QBO Account with no row yet: the rule's audited line, and the
    workbook's proposed ERPNext account where it still exists live. Decision and
    the decided erp_account stay blank — those are Tanuj's."""
    with open(PROPOSALS) as f:
        proposals = json.load(f)
    have = set(frappe.get_all("QBO ERP Account Crosswalk", pluck="qbo_account"))
    lines = set(frappe.get_all("Audited FS Line", pluck="name"))
    for acct in frappe.get_all("QBO Account", fields=["name", "fully_qualified_name", "classification", "account_type"]):
        if acct.name in have:
            continue
        line = rules.qbo_line(acct.fully_qualified_name, acct.classification, acct.account_type)
        proposed = proposals.get(acct.name)
        if proposed and not frappe.db.exists("Account", proposed):
            proposed = None
        frappe.get_doc({
            "doctype": "QBO ERP Account Crosswalk",
            "qbo_account": acct.name,
            "fs_line": line if line in lines else None,
            "proposed_erp_account": proposed,
        }).insert(ignore_permissions=True)
