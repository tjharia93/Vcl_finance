"""Control totals — what the paper sheet says, against what was captured.

The custodian's handwritten sheet carries a total per category. Typing those in
and letting the machine subtract is the fastest way to find a row that was missed
or keyed into the wrong bucket, and it is the check that has always been done on
paper anyway.

Everything on the captured side already existed: the sheet controller totals by
category on every save and `_summary()` returns it. This only supplies the
declared figures and the subtraction.

**A missing key means NOT DECLARED, never zero.** A category you did not write
down and a category that genuinely came to nothing are different, and rendering
both as 0 would invent a variance against a figure nobody stated.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from vcl_finance.petty_cash.api import PETTY_PRIV
from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    CATEGORY_CODES, summary,
)

CATEGORY_LABEL = {
    "TG": "Transport — goods", "TE": "Transport — employee", "SE": "Service / repairs",
    "OA": "Office / admin", "FD": "Food / staff welfare", "GP": "Graphics / plates",
    "OT": "Other",
}



def _declared(doc):
    try:
        raw = json.loads(doc.control_totals or "{}")
        return {k: flt(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        # Unreadable JSON must not take the screen down — an unusable control
        # total is a nuisance, a blank sheet is a fault.
        return {}


@frappe.whitelist()
def control_check(sheet):
    """Declared against captured, line by line. Read-only."""
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    s = summary(doc.name)
    dec = _declared(doc)

    rows = []

    def add(key, label, captured, group):
        d = dec.get(key)
        rows.append({
            "key": key, "label": label, "group": group,
            "captured": round(flt(captured), 2),
            "declared": None if d is None else round(flt(d), 2),
            # None, not 0 — an undeclared line has no variance to report.
            "variance": None if d is None else round(flt(d) - flt(captured), 2),
        })

    cats = s["cat_out"] or {}
    for c in CATEGORY_CODES:
        add(c, f"{c}  {CATEGORY_LABEL.get(c, '')}".strip(), cats.get(c, 0), "Voucher categories")

    # Driven by the vehicles actually ON this sheet, not a hardcoded fleet — a
    # plate that changes should not need a code change.
    for plate, amt in sorted((s.get("parking_by_vehicle") or {}).items()):
        add(f"park:{plate}", plate, amt, "Parking, by car")

    add("bike", "Bike fuel", s.get("bike_total", 0), "Fuel")
    add("forklift", "Forklift", s.get("forklift_total", 0), "Fuel")
    # Wages by entry_type. summary() returns one wages_total, but the custodian's
    # sheet carries them as separate columns and a single figure cannot be checked
    # against four columns. Computed from the rows rather than adding another
    # field to summary(), which the editor's live totals also depend on.
    by_type = {}
    for w in doc.wages_entries:
        if w.cancelled:
            continue
        by_type[w.entry_type or "Wage"] = by_type.get(w.entry_type or "Wage", 0) + flt(w.amount)
    for t in ("Wage", "Overtime", "Piecework", "Commission"):
        add(f"wages:{t}", "Wages" if t == "Wage" else t, by_type.get(t, 0), "Wages and loans")
    # Any entry_type the list above does not know about, so a new one cannot go
    # unchecked just because nobody updated this file.
    for t, amt in sorted(by_type.items()):
        if t not in ("Wage", "Overtime", "Piecework", "Commission"):
            add(f"wages:{t}", t, amt, "Wages and loans")
    add("loans", "Loans issued", s.get("loans_total", 0), "Wages and loans")

    declared_rows = [r for r in rows if r["declared"] is not None]
    return {
        "sheet": doc.name, "week_ending": str(doc.week_ending), "float": doc.float,
        "rows": rows,
        "declared_total": round(sum(r["declared"] for r in declared_rows), 2),
        "captured_total": round(sum(r["captured"] for r in declared_rows), 2),
        "variance_total": round(sum(r["variance"] for r in declared_rows), 2),
        "declared_lines": len(declared_rows),
        "undeclared_lines": len(rows) - len(declared_rows),
        # The sheet's own out-total, for the case where every line ties but the
        # sheet does not — which means something is in a bucket with no control
        # total against it.
        "sheet_total_out": round(flt(doc.total_out), 2),
    }


@frappe.whitelist(methods=["POST"])
def set_control_totals(sheet, totals):
    """Store the declared figures. Blank or missing keys are REMOVED, not zeroed."""
    if not (set(frappe.get_roles()) & PETTY_PRIV):
        frappe.throw(_("Only Accounts Managers can set control totals."),
                     frappe.PermissionError)
    if isinstance(totals, str):
        totals = json.loads(totals)
    if not isinstance(totals, dict):
        frappe.throw(_("Control totals must be a set of category/amount pairs."))

    clean = {}
    for k, v in totals.items():
        if v is None or str(v).strip() == "":
            continue                      # cleared, so it goes back to undeclared
        clean[str(k)] = flt(v)

    # db.set_value rather than save: this is a note against the week, and saving
    # would re-run the whole sheet — including the mirror — for a figure that has
    # no bearing on any entry.
    frappe.db.set_value("Petty Cash Sheet", sheet, "control_totals",
                        json.dumps(clean, sort_keys=True), update_modified=False)
    frappe.db.commit()
    return control_check(sheet)
