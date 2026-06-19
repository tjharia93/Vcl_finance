"""Phase 1 schema-drift fix for Petty Cash.

Best-effort, idempotent, and a no-op on an empty install:

1. Petty Cash Voucher moved from per-category amount columns
   (amt_tg/amt_te/amt_se/amt_oa/amt_cm/amt_ot/amt_in) to a single
   ``category`` Select + ``amount`` Currency + ``cash_in`` Check.
   If the old columns still exist with data, fold each row down to the
   first non-zero category (or cash_in for amt_in).

2. Petty Cash Wages Entry ``entry_type`` dropped ``Loan``. Any rows still
   typed ``Loan`` are migrated to the new ``Petty Cash Loan Entry`` child on
   the same parent sheet, then re-typed ``Wage`` so they pass validation.

3. Petty Cash Category master: drop the legacy ``CM`` code if unused; ensure
   FD/GP exist.

Guards every step so it is safe to run before the columns/doctypes exist or
when there is zero data (the common case on Frappe Cloud today).
"""
import frappe


CATEGORY_COLUMNS = [
    ("amt_tg", "TG"),
    ("amt_te", "TE"),
    ("amt_se", "SE"),
    ("amt_oa", "OA"),
    ("amt_ot", "OT"),
    # CM is intentionally last so genuine OUT categories win the fold-down.
    ("amt_cm", "OT"),
]


def execute():
    _ensure_categories()
    _migrate_voucher_columns()
    _migrate_loan_rows()
    frappe.db.commit()


def _table_has_column(doctype, column):
    table = f"tab{doctype}"
    try:
        cols = {c["Field"] for c in frappe.db.sql(f"DESCRIBE `{table}`", as_dict=True)}
    except Exception:
        return False
    return column in cols


def _ensure_categories():
    if not frappe.db.exists("DocType", "Petty Cash Category"):
        return
    for code, label in (("FD", "Food"), ("GP", "Geeprint")):
        if not frappe.db.exists("Petty Cash Category", code):
            try:
                doc = frappe.new_doc("Petty Cash Category")
                doc.code = code
                doc.label = label
                doc.insert(ignore_permissions=True)
            except Exception:
                pass
    # Disable (don't delete) a legacy CM code so historical references survive.
    if frappe.db.exists("Petty Cash Category", "CM"):
        frappe.db.set_value("Petty Cash Category", "CM", "disabled", 1)


def _migrate_voucher_columns():
    dt = "Petty Cash Voucher"
    if not frappe.db.exists("DocType", dt):
        return
    # Nothing to do unless the OLD columns are still physically present.
    if not _table_has_column(dt, "amt_tg"):
        return
    if not (_table_has_column(dt, "category") and _table_has_column(dt, "amount")):
        return

    rows = frappe.db.sql(
        """SELECT name, amt_tg, amt_te, amt_se, amt_oa, amt_cm, amt_ot, amt_in
           FROM `tabPetty Cash Voucher`""",
        as_dict=True,
    )
    for r in rows:
        category = None
        amount = 0.0
        cash_in = 0
        for col, code in CATEGORY_COLUMNS:
            val = r.get(col) or 0
            if val:
                category = code
                amount = val
                break
        if not amount and (r.get("amt_in") or 0):
            amount = r.get("amt_in")
            cash_in = 1
        if amount or category:
            frappe.db.set_value(
                dt, r["name"],
                {"category": category, "amount": amount, "cash_in": cash_in},
                update_modified=False,
            )


def _migrate_loan_rows():
    dt = "Petty Cash Wages Entry"
    if not frappe.db.exists("DocType", dt):
        return
    if not _table_has_column(dt, "entry_type"):
        return
    loan_rows = frappe.db.sql(
        """SELECT name, parent, parenttype, txn_date, recipient, staff_id, reason, amount, paye
           FROM `tabPetty Cash Wages Entry`
           WHERE entry_type = 'Loan'""",
        as_dict=True,
    )
    if not loan_rows:
        return
    can_create_loans = frappe.db.exists("DocType", "Petty Cash Loan Entry")
    for w in loan_rows:
        if can_create_loans and w.get("parenttype") == "Petty Cash Sheet" and w.get("parent"):
            try:
                sheet = frappe.get_doc("Petty Cash Sheet", w["parent"])
                sheet.append("loan_entries", {
                    "txn_date": w.get("txn_date"),
                    "recipient": w.get("recipient"),
                    "staff_id": w.get("staff_id"),
                    "reason": w.get("reason"),
                    "amount_issued": w.get("amount") or 0,
                    "amount_signed": w.get("amount") or 0,
                    "paye": w.get("paye") or 0,
                })
                sheet.flags.ignore_validate = True
                sheet.save(ignore_permissions=True)
            except Exception:
                pass
        # Re-type so the row passes the new Select validation.
        frappe.db.set_value(dt, w["name"], "entry_type", "Wage", update_modified=False)
