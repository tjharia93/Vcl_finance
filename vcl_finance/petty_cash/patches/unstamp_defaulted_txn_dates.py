"""Clear transaction dates that were never typed — they are the day the sync ran.

`txn_date` carried `default: "Today"`, so a source row with no date arrived with
whatever date the mirror happened to execute on. The controller's careful fallback
— undated but mirrored, use the SOURCE SHEET's week — could never run, because
Frappe had already filled the blank before the controller saw it.

The result is worse than a missing date. A May payment reads as an August one and
looks entirely authoritative: four rows all stamped 2026-08-28, two of them since
posted into an August journal and a QuickBooks Expense.

**How a defaulted date is identified.** Its `txn_date` equals the DATE PART OF ITS
OWN CREATION, and its source sheet belongs to a different week. Both conditions,
because either alone catches real entries: plenty of lines are legitimately keyed
on the day they happened, and plenty legitimately sit in a week other than their
sheet's label.

**What it does.** Clears `txn_date` and re-derives `week_ending` from the source
sheet, so the line lands in the week it was actually written up in and shows
visibly undated. It does NOT guess a day from neighbouring rows — the neighbours
make the likely date obvious to a person, and that is exactly the judgement a
patch should not make on their behalf.

**What it deliberately leaves alone.** A line already carrying a journal entry.
Re-dating it would move it out of a week that has been posted while the journal
still holds it, so those are reported and left for a human to unpost, re-date and
repost in that order.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "Petty Cash Entry"):
        return

    rows = frappe.get_all(
        "Petty Cash Entry",
        filters={"cancelled": 0, "txn_date": ("is", "set")},
        fields=["name", "txn_date", "creation", "origin_week_ending", "journal_entry",
                "recipient", "amount"],
        limit_page_length=0,
    )

    cleared, posted = 0, []
    for r in rows:
        if str(r.txn_date) != str(r.creation)[:10]:
            continue
        if not r.origin_week_ending:
            continue
        if str(r.origin_week_ending) == str(r.txn_date):
            continue                      # genuinely keyed on the day it happened

        if r.journal_entry:
            posted.append(f"{r.name} {r.recipient} {r.amount} -> {r.journal_entry}")
            continue

        doc = frappe.get_doc("Petty Cash Entry", r.name)
        doc.txn_date = None
        doc.flags.ignore_permissions = True
        doc.save()                        # before_save re-derives week_ending
        cleared += 1

    frappe.db.commit()
    frappe.logger().info(
        f"petty cash: cleared {cleared} defaulted txn_date(s); "
        f"{len(posted)} left alone because they are already posted: {posted}"
    )
