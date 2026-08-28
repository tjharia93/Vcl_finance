"""One-way mirror: Petty Cash Sheet child rows -> Petty Cash Entry.

Phase 1 is ADDITIVE. The sheet stays the capture path and the system of record; the
entries are a read-only shadow of it. The arrow only reverses in phase 4, and only
once reconciliation has proved the shadow faithful.

Four properties this file exists to guarantee:

1. **It can never block a save.** The whole run is wrapped. A mirror failure is logged
   and swallowed — a custodian typing up Friday's vouchers must never see a traceback
   from a shadow system they don't know exists.

2. **It is whole-sheet, not row-level.** Row-level upserts cannot see a DELETION.
   Every run reconciles the full set for that sheet, and a row that has vanished from
   the source is marked ``Orphaned`` and dropped from the totals. Nothing is ever
   hard-deleted; an entry that was real yesterday stays visible and explains itself.

3. **It is resumable.** Two Frappe Cloud deploys landed on 2026-08-28 alone and the
   site logged 168 ``SessionStopped`` errors in a fortnight, so a run dying halfway is
   ordinary, not exceptional. Each row is committed on its own terms and ``origin_hash``
   lets the next run skip what already matches, so a half-finished run leaves consistent
   partial state and the next one completes it.

4. **It never approves anything.** The mirror writes source facts. When a row changes
   underneath an approval it raises ``changed_after_approval`` and stops there — it
   does not clear the approval, because "this changed" and "this is no longer approved"
   are different statements and only a human gets to make the second one.
"""

import hashlib
import json

import frappe
from frappe.utils import flt

from vcl_finance.petty_cash.doctype.petty_cash_entry.petty_cash_entry import (
    reconstruct_date,
)

LOG_TITLE = "Petty Cash Mirror"

# How each child table maps onto an entry. ``source_type`` reuses Posting Map's
# vocabulary verbatim so phase 5 posting is a direct (company, source_type, source_key)
# lookup rather than a translation table nobody maintains.
#
# amount_field matters: for loans only ``amount_issued`` leaves the float —
# ``amount_signed`` is what the recipient acknowledged and rides in the payload.
TABLE_MAP = {
    "vouchers": {
        "doctype": "Petty Cash Voucher",
        "source_type": "Voucher Category",
        "amount_field": "amount",
        "key_field": "category",
    },
    "parking_entries": {
        "doctype": "Petty Cash Parking Entry",
        "source_type": "Parking",
        "amount_field": "amount",
        "key_field": "vehicle",
    },
    "misc_entries": {
        "doctype": "Petty Cash Misc Entry",
        "source_type": None,          # taken from the row's own ``kind``
        "amount_field": "amount",
        "key_field": "kind",
    },
    "wages_entries": {
        "doctype": "Petty Cash Wages Entry",
        "source_type": "Wages Entry",
        "amount_field": "amount",
        "key_field": "entry_type",
    },
    "loan_entries": {
        "doctype": "Petty Cash Loan Entry",
        "source_type": "Loan",
        "amount_field": "amount_issued",
        "key_field": None,
    },
}

# Everything the typed columns do not carry. Kept as JSON so no source fact is lost
# on the way across, and so phase 4 can rebuild a child row from an entry.
PAYLOAD_FIELDS = (
    "voucher_no", "vehicle", "day_idx", "slot", "kind", "row_idx",
    "staff_id", "reason", "paye", "amount_signed",
    "recipient_signed", "authorised_signed", "locked",
)


def is_real_row(row, amount_field="amount"):
    """THE shared definition of "this child row is a real entry, not scaffolding".

    ``_ensure_grid()`` pads every sheet to 18 vouchers / 18 wages / 8 loans / 6 bike /
    4 forklift / a 60-cell parking grid, so **64% of the 3,435 live child rows are
    blank placeholders**. Both the mirror and the reconciliation MUST call this one
    function: if they disagree by even one row about what counts, the reconciliation
    carries a permanent non-zero that means nothing and the gate can never pass.

    A row is real if it carries money OR any identifying content. Money alone is not
    enough — a dated, named row with a zero amount is a real (if odd) record, and a
    row someone typed a recipient into is not scaffolding.
    """
    if flt(row.get(amount_field)):
        return True
    for field in ("recipient", "voucher_no", "notes", "reason", "staff_id"):
        if (row.get(field) or "").strip():
            return True
    return False


def row_hash(row, amount_field):
    """Fingerprint of the source facts. Lets a sweep skip rows that have not moved."""
    material = {f: str(row.get(f) or "") for f in (
        "txn_date", "recipient", "category", "cancelled", "cancel_remark", "notes",
        "pc_received", "etr_received", "receipt", "entry_type",
    ) + PAYLOAD_FIELDS}
    material["amount"] = f"{flt(row.get(amount_field)):.2f}"
    return hashlib.md5(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _source_type_for(table, row):
    """Which Posting Map family this row belongs to.

    Two rows do not take it straight from the table:

    - **Misc** carries its family on the row itself (``kind``: Bike Fuel / Forklift).
    - **A cash-IN voucher is a Replenishment, not a Voucher Category.** Money coming
      back into the float is not spend, and filing it under a spend category would put
      a top-up on the wrong side of the posting in phase 5. Posting Map offers
      ``Float`` and ``Replenishment`` for this; ``Replenishment`` is the topping-up of
      an existing float, which is what every cash-IN row on these sheets is.

      ASSUMPTION, flagged for confirmation: if ``Float`` is meant to carry top-ups and
      ``Replenishment`` something else, this is the single line to change.
    """
    if table == "misc_entries":
        return row.get("kind") or "Bike Fuel"
    if table == "vouchers" and row.get("cash_in"):
        return "Replenishment"
    return TABLE_MAP[table]["source_type"]


def _entry_values(sheet, table, row):
    """Build the entry payload for one child row. Pure — writes nothing."""
    spec = TABLE_MAP[table]
    amount_field = spec["amount_field"]
    key_field = spec["key_field"]

    txn_date = row.get("txn_date")
    payload = {f: row.get(f) for f in PAYLOAD_FIELDS if row.get(f) not in (None, "")}

    # Legacy parking knows only a weekday NAME. Reconstructing a real date inside the
    # sheet's own week gives the queue something to sort by and lands the entry in the
    # same week the fallback would have chosen anyway. Flagged in the payload so
    # nothing downstream mistakes a derived date for one somebody actually typed.
    if not txn_date and row.get("day_idx"):
        rebuilt = reconstruct_date(sheet.week_ending, row.get("day_idx"))
        if rebuilt:
            txn_date = rebuilt
            payload["date_reconstructed"] = True

    return {
        "doctype": "Petty Cash Entry",
        "txn_date": txn_date,
        "float": sheet.get("float") or "Cash",
        "source_type": _source_type_for(table, row),
        "source_key": (row.get(key_field) or "") if key_field else "",
        "category": row.get("category") if table == "vouchers" else None,
        "recipient": row.get("recipient"),
        "amount": flt(row.get(amount_field)),
        "cash_in": 1 if row.get("cash_in") else 0,
        "receipt": row.get("receipt"),
        "pc_received": 1 if row.get("pc_received") else 0,
        "etr_received": 1 if row.get("etr_received") else 0,
        "notes": row.get("notes"),
        "cancelled": 1 if row.get("cancelled") else 0,
        "cancel_remark": row.get("cancel_remark"),
        "origin_doctype": spec["doctype"],
        "origin_row": row.get("name"),
        "origin_sheet": sheet.name,
        "origin_week_ending": sheet.week_ending,
        "origin_hash": row_hash(row, amount_field),
        "sync_state": "Mirrored",
        "payload": json.dumps(payload, sort_keys=True, default=str),
    }


def mirror_sheet(sheet, raise_on_error=False):
    """Reconcile every entry for ``sheet`` against its current child rows.

    Returns a dict of counts. Never raises unless ``raise_on_error`` — the patch and
    the tests want the traceback; ``on_update`` never does.
    """
    stats = {"created": 0, "updated": 0, "unchanged": 0, "orphaned": 0, "skipped": 0}
    try:
        existing = {
            e["origin_row"]: e
            for e in frappe.get_all(
                "Petty Cash Entry",
                filters={"origin_sheet": sheet.name},
                fields=["name", "origin_row", "origin_hash", "status", "sync_state"],
            )
        }
        seen = set()

        for table, spec in TABLE_MAP.items():
            for row in (sheet.get(table) or []):
                if not is_real_row(row, spec["amount_field"]):
                    stats["skipped"] += 1
                    continue
                seen.add(row.get("name"))
                values = _entry_values(sheet, table, row)
                prior = existing.get(row.get("name"))

                if prior is None:
                    doc = frappe.get_doc(values)
                    doc.flags.ignore_permissions = True   # permlevel-1 origin_* fields
                    doc.insert(ignore_permissions=True)
                    stats["created"] += 1
                    continue

                if prior["origin_hash"] == values["origin_hash"] and prior["sync_state"] == "Mirrored":
                    stats["unchanged"] += 1
                    continue

                doc = frappe.get_doc("Petty Cash Entry", prior["name"])
                # A source row that moved under an approval raises a flag and nothing
                # more. Clearing the approval is a human decision, not a sweep's.
                if doc.status == "Approved" and prior["origin_hash"] != values["origin_hash"]:
                    values["changed_after_approval"] = 1
                for field, value in values.items():
                    if field != "doctype":
                        doc.set(field, value)
                doc.flags.ignore_permissions = True
                doc.save(ignore_permissions=True)
                stats["updated"] += 1

        # Deletions. A row that has left the sheet keeps its entry but stops counting.
        for origin_row, prior in existing.items():
            if origin_row in seen or prior["sync_state"] == "Orphaned":
                continue
            frappe.db.set_value("Petty Cash Entry", prior["name"], "sync_state", "Orphaned")
            stats["orphaned"] += 1

    except Exception:
        if raise_on_error:
            raise
        frappe.log_error(
            title=LOG_TITLE,
            message=f"sheet={getattr(sheet, 'name', '?')}\n\n{frappe.get_traceback()}",
        )
        stats["error"] = True
    return stats


def on_sheet_update(doc, method=None):
    """``doc_events`` hook. Synchronous by design — no enqueue, no scheduler.

    The scheduler is not a dependency we can take: Frappe Cloud pauses it during every
    deploy, and the sheet controller already recomputes every total on every save, so
    doing the mirror in the same transaction costs almost nothing at this data scale
    (~600 real rows per float-year).
    """
    mirror_sheet(doc)
