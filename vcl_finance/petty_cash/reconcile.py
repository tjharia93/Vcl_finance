"""Reconciliation — the instrument the phase-1 gate is read off.

Per SHEET, never per week. Matching on ``origin_sheet`` is what lets the import-era
sheets reconcile cleanly with no exclusion rule and no weekday test: 12 sheets are
labelled with a Friday or Sunday rather than a Saturday, and 263 of their child rows
(KES 1.83m) carry dates outside the Sun-Sat week their own label implies. Per-week
matching would light all of that up as a failure. Per-sheet matching sees it for what
it is — a labelling convention, not a discrepancy — and defers the real question to
phase 4, where regenerating sheets from entries genuinely does move those rows.

Three rules, each of which exists because breaking it produces a number that looks
like a defect and isn't:

- **Sums, never counts.** 64% of child rows are blank scaffolding. Where a count is
  needed, both sides call ``mirror.is_real_row`` — one predicate, no daylight.
- **``total_out`` and ``total_in`` only.** ``expected_close`` and ``variance`` also
  depend on ``opening_balance`` and ``cash_count_end``, which no child row determines,
  so an entry-side sum can never reproduce them.
- **Never per category.** ``compute_totals`` drops a voucher from the category
  breakdown when its category falls outside the seven codes while still counting it in
  ``voucher_out``, so a per-category comparison can legitimately under-sum against a
  total that ties perfectly.

The opening chain is REPORTED and never repaired. The breaks it surfaces are real and
already known — 6 of 17 links on Cash, 7 of 11 on Hauz-Pay, and a live 280,037 between
w/e 15 Aug and w/e 22 Aug. Seeing them is the mirror proving it reads reality. Fixing
them is phase 4 (PC-005), and a sweep that silently corrected an opening balance would
be rewriting the books to make its own report look better.
"""

import frappe
from frappe.utils import flt

TOLERANCE = 0.005      # half a cent; these are 2dp currency columns


def _entry_totals(sheet_name):
    """Cash out / cash in for one sheet, summed from its entries.

    Orphaned entries are excluded — the source row is gone, so the money is gone with
    it. Cancelled entries are excluded to match ``compute_totals``, which skips voided
    rows for every total, the expected close and the posting.
    """
    rows = frappe.get_all(
        "Petty Cash Entry",
        filters={"origin_sheet": sheet_name, "cancelled": 0, "sync_state": ("!=", "Orphaned")},
        fields=["amount", "cash_in"],
        limit_page_length=0,
    )
    out = sum(flt(r["amount"]) for r in rows if not r["cash_in"])
    into = sum(flt(r["amount"]) for r in rows if r["cash_in"])
    return round(out, 2), round(into, 2), len(rows)


@frappe.whitelist()
def reconcile_sheet(sheet_name):
    """One sheet: stored totals vs the sum of its entries."""
    sheet = frappe.db.get_value(
        "Petty Cash Sheet", sheet_name,
        ["name", "week_ending", "float", "total_out", "total_in", "status"],
        as_dict=True,
    )
    if not sheet:
        frappe.throw(f"No such sheet: {sheet_name}")

    out, into, n = _entry_totals(sheet_name)
    d_out = round(out - flt(sheet.total_out), 2)
    d_in = round(into - flt(sheet.total_in), 2)
    return {
        "sheet": sheet.name,
        "week_ending": str(sheet.week_ending),
        "float": sheet.get("float"),
        "status": sheet.status,
        "sheet_out": flt(sheet.total_out), "entry_out": out, "delta_out": d_out,
        "sheet_in": flt(sheet.total_in), "entry_in": into, "delta_in": d_in,
        "entry_rows": n,
        "clean": abs(d_out) < TOLERANCE and abs(d_in) < TOLERANCE,
    }


@frappe.whitelist()
def reconcile_all(float_name=None):
    """Every non-cancelled sheet. THE gate instrument — assessed PER FLOAT.

    Returns per-sheet rows plus a per-float verdict. The gate is zero drift across all
    sheets for two consecutive weeks of ordinary use, judged for Cash and Hauz-Pay
    separately: Hauz-Pay's chain is materially weaker than Cash's, and an aggregate
    number would let Cash carry a float that has not earned it.
    """
    filters = {"docstatus": ("<", 2)}
    if float_name:
        filters["float"] = float_name
    sheets = frappe.get_all(
        "Petty Cash Sheet", filters=filters,
        fields=["name", "float"], order_by="week_ending asc", limit_page_length=0,
    )
    rows = [reconcile_sheet(s["name"]) for s in sheets]

    by_float = {}
    for r in rows:
        bucket = by_float.setdefault(r["float"], {"sheets": 0, "clean": 0, "worst_out": 0.0, "worst_in": 0.0})
        bucket["sheets"] += 1
        bucket["clean"] += 1 if r["clean"] else 0
        bucket["worst_out"] = max(bucket["worst_out"], abs(r["delta_out"]))
        bucket["worst_in"] = max(bucket["worst_in"], abs(r["delta_in"]))
    for bucket in by_float.values():
        bucket["passes"] = bucket["clean"] == bucket["sheets"]

    return {"rows": rows, "by_float": by_float,
            "passes": all(b["passes"] for b in by_float.values()) if by_float else False}


@frappe.whitelist()
def opening_chain(float_name="Cash"):
    """Walk the carry-forward chain for one float, computing closes FROM ENTRIES.

    Reports only. Every break it finds is a real, already-diagnosed defect:
    ``opening_balance`` is a creation-time snapshot that is never recomputed, so
    editing a week after the next sheet exists silently breaks the link and nothing
    warns. Repairing that is phase 4.
    """
    sheets = frappe.get_all(
        "Petty Cash Sheet",
        filters={"docstatus": ("<", 2), "float": float_name},
        fields=["name", "week_ending", "opening_balance", "cash_count_end", "total_out", "total_in"],
        order_by="week_ending asc", limit_page_length=0,
    )
    links, prior = [], None
    for s in sheets:
        out, into, _ = _entry_totals(s["name"])
        shadow_close = round(flt(s["opening_balance"]) - out + into, 2)
        if prior is not None:
            # Same basis the controller carries: a physical count wins, else the close.
            carried = flt(prior["cash_count_end"]) or prior["shadow_close"]
            gap = round(flt(s["opening_balance"]) - round(carried, 2), 2)
            if abs(gap) >= 1:
                links.append({
                    "from": prior["name"], "from_week": str(prior["week_ending"]),
                    "to": s["name"], "to_week": str(s["week_ending"]),
                    "carried": round(carried, 2),
                    "opening": flt(s["opening_balance"]), "gap": gap,
                })
        prior = dict(s, shadow_close=shadow_close)
    return {"float": float_name, "sheets": len(sheets),
            "links": max(len(sheets) - 1, 0), "breaks": links}


def summary(float_name=None):
    """Human-readable one-shot for a bench console or a report. Never writes."""
    result = reconcile_all(float_name)
    lines = [f"{'sheet':17} {'float':9} {'d_out':>12} {'d_in':>12}  status"]
    for r in result["rows"]:
        mark = "ok" if r["clean"] else "DRIFT"
        lines.append(f"{r['sheet']:17} {r['float']:9} {r['delta_out']:12,.2f} {r['delta_in']:12,.2f}  {mark}")
    for name, bucket in sorted(result["by_float"].items()):
        lines.append(f"\n{name}: {bucket['clean']}/{bucket['sheets']} clean "
                     f"-> {'PASSES' if bucket['passes'] else 'DOES NOT PASS'}")
    for name in sorted(result["by_float"]):
        chain = opening_chain(name)
        lines.append(f"{name} opening chain: {len(chain['breaks'])} broken of {chain['links']} links "
                     f"(reported, never repaired)")
    return "\n".join(lines)
