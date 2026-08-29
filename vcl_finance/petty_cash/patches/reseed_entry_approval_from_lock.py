"""Backfill approval status onto entries that were mirrored before the lock meant it.

The mirror originally copied the sheet's per-row ``locked`` tick into the JSON
payload and nowhere else, so 1,239 entries arrived ``Unapproved`` — including the
322 whose rows Finance had already signed off, and the 45 that were voided. This
puts that right in one pass, so nobody re-approves work they approved once.

**Why the ordinary backfill cannot do this.** ``mirror_sheet`` skips any row whose
``origin_hash`` still matches, and these source rows have not changed — only our
reading of them has. Re-running the backfill, marker or no marker, would skip every
one of them. So this reaches the entries directly.

**Safety.** It touches ONLY entries still sitting at ``Unapproved``: a decision made
on the approvals screen is never overwritten, and running the patch twice changes
nothing the second time. It reads each entry's own source row rather than reloading
the sheets, so an entry whose row has since been deleted is simply skipped.

**Why db.set_value and not doc.save().** Saving would fire ``before_save`` on every
one of 1,239 documents and rewrite ``modified`` across the whole table, burying the
one timestamp that matters — the moment Finance actually signed. A patch is the
sanctioned place for a targeted data repair, and this is one.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "Petty Cash Entry"):
        return

    entries = frappe.get_all(
        "Petty Cash Entry",
        filters={"status": "Unapproved"},
        fields=["name", "origin_doctype", "origin_row"],
        limit_page_length=0,
    )

    approved = voided = skipped = 0
    for e in entries:
        if not e.origin_doctype or not e.origin_row:
            skipped += 1
            continue
        try:
            src = frappe.db.get_value(
                e.origin_doctype, e.origin_row,
                ["locked", "locked_by", "locked_on", "cancelled"],
                as_dict=True,
            )
        except Exception:
            src = None
        if not src:
            # The source row is gone. The mirror marks that Orphaned on its next
            # run; it is not this patch's job to decide.
            skipped += 1
            continue

        if src.get("cancelled"):
            # Void rides ALONGSIDE `cancelled`, never instead of it — every total
            # filters on the flag, and a voided row reading Unapproved sat in the
            # queue's counts as though someone still owed it a decision.
            frappe.db.set_value("Petty Cash Entry", e.name, "status", "Void",
                                update_modified=False)
            voided += 1
        elif src.get("locked"):
            frappe.db.set_value("Petty Cash Entry", e.name, {
                "status": "Approved",
                # Blank for the 263 rows locked before `locked_by` was recorded.
                # A missing approver is honest; a guessed one is not.
                "approved_by": src.get("locked_by") or None,
                "approved_on": src.get("locked_on") or None,
            }, update_modified=False)
            approved += 1

    frappe.db.commit()
    frappe.logger().info(
        f"petty cash reseed: approved={approved} voided={voided} skipped={skipped} "
        f"of {len(entries)} unapproved entries"
    )
