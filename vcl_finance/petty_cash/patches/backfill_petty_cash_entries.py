"""Backfill Petty Cash Entry from every existing sheet.

Patches are the only sanctioned way to change production data here, so the phase-1
backfill is a patch rather than a script run against the site from outside — it is
reviewable, it is repeatable, and it runs inside the app's own context.

**Idempotent by construction.** It does not track "have I run"; it re-mirrors, and
``mirror_sheet`` skips any row whose ``origin_hash`` already matches. Running it twice
changes nothing, which is the same property that makes it resumable: two Frappe Cloud
deploys landed on 2026-08-28 alone and the site logged 168 ``SessionStopped`` errors in
a fortnight, so a run dying halfway is ordinary. Each sheet is committed on its own, so
a death leaves consistent partial state and the next run finishes the job.

**Reachable twice, deliberately.** ``install_app()`` ends with
``set_all_patches_as_completed()``, which writes every line of ``patches.txt`` into the
Patch Log *unexecuted* — and migrate never retries a patch the log says is done. On a
fresh site this patch would therefore be marked complete having never run. So
``install.after_install`` calls it too. Both routes may fire; because the work is
idempotent, that is harmless.
"""

import frappe

from vcl_finance.petty_cash.mirror import mirror_sheet


def execute():
    backfill()


def backfill(raise_on_error=False):
    """Mirror every non-cancelled sheet, oldest first. Returns aggregate counts."""
    if not frappe.db.exists("DocType", "Petty Cash Entry"):
        # Nothing to write into yet — a migrate where the doctype has not synced.
        return {}
    if not frappe.db.exists("DocType", "Petty Cash Sheet"):
        return {}

    sheets = frappe.get_all(
        "Petty Cash Sheet",
        filters={"docstatus": ("<", 2)},
        fields=["name"],
        order_by="week_ending asc",
        limit_page_length=0,
    )

    totals = {"sheets": 0, "created": 0, "updated": 0, "unchanged": 0,
              "orphaned": 0, "skipped": 0, "failed": 0}

    for row in sheets:
        try:
            sheet = frappe.get_doc("Petty Cash Sheet", row["name"])
            stats = mirror_sheet(sheet, raise_on_error=True)
            for key in ("created", "updated", "unchanged", "orphaned", "skipped"):
                totals[key] += stats.get(key, 0)
            totals["sheets"] += 1
            # Commit per sheet: this is what makes a half-finished run safe to resume
            # rather than something that has to be unwound.
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            totals["failed"] += 1
            if raise_on_error:
                raise
            frappe.log_error(
                title="Petty Cash Mirror backfill",
                message=f"sheet={row['name']}\n\n{frappe.get_traceback()}",
            )

    frappe.logger().info(f"petty cash backfill: {totals}")
    return totals
