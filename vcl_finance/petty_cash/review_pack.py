"""Email the week's workbook when the sheet is submitted for review.

Submitting is the moment the custodian says "this week is finished". That is the
last point before sign-off at which somebody looks at the whole week, so the pack
goes out then rather than on a schedule — a Monday email about a week nobody has
finished is noise, and one about a week already approved is too late.

**It sends once.** `review_emailed_on` is the guard. A sheet gets saved many times
after submission — a lock ticked, a note added — and every one of those is an
`on_update`. A second review pack is worse than none: two workbooks land in the
same inbox and nobody knows which is current.

**It never blocks the submit.** If the mail fails, the failure is logged and the
sheet still submits. Refusing to record a finished week because an SMTP host was
down would be the wrong trade every time.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

# Who reviews. Deliberately a constant rather than a setting: this is a two-person
# sign-off, and a recipient list that can drift silently is how a review pack ends
# up going to somebody who left.
REVIEW_RECIPIENTS = ["jituharia@yahoo.com", "accounts@vimit.com"]


def maybe_send(doc, method=None):
    """Called from on_update. Fires only on the transition INTO Submitted."""
    if doc.status != "Submitted" or doc.review_emailed_on:
        return
    # before_save has already run, so the value in the DB is the PREVIOUS status.
    was = frappe.db.get_value("Petty Cash Sheet", doc.name, "status")
    if was == "Submitted":
        return                      # already submitted; this save is something else

    frappe.enqueue(
        "vcl_finance.petty_cash.review_pack.send_now",
        queue="short", timeout=300, enqueue_after_commit=True, sheet=doc.name,
    )


def send_now(sheet):
    """Build the workbook and mail it. Safe to call twice — the guard is re-checked."""
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if doc.review_emailed_on:
        return
    try:
        # The same builder the download button uses, called the same way, so the
        # emailed workbook is byte-identical to the one anybody would export by
        # hand. A second assembly path would drift and nobody would notice until
        # the two disagreed in front of a reviewer.
        from vcl_finance.petty_cash import report_xlsx
        from vcl_finance.petty_cash.api import _report_checks
        from frappe.utils import get_url

        sheets = [doc.as_dict()]
        data = report_xlsx.build_workbook(
            sheets, from_date=doc.week_ending, to_date=doc.week_ending,
            source=get_url(), checks=_report_checks(sheets))
        fname = report_xlsx.filename(sheets, doc.week_ending, doc.week_ending)

        frappe.sendmail(
            recipients=REVIEW_RECIPIENTS,
            subject=f"Petty cash for review — {doc.float}, week ending {doc.week_ending}",
            message=_body(doc),
            attachments=[{"fname": fname, "fcontent": data}],
            reference_doctype="Petty Cash Sheet", reference_name=doc.name,
            now=True,
        )
    except Exception:
        # Logged, never raised. A week that is finished must still record as
        # finished when the mail server is not answering.
        frappe.log_error(title=f"Petty cash review pack failed for {sheet}",
                         message=frappe.get_traceback())
        return

    frappe.db.set_value("Petty Cash Sheet", sheet, {
        "review_emailed_on": now_datetime(),
        "review_emailed_to": ", ".join(REVIEW_RECIPIENTS),
    }, update_modified=False)
    frappe.db.commit()


def _body(doc):
    esc = frappe.utils.escape_html
    money = lambda n: f"{(n or 0):,.0f}"
    variance = doc.variance or 0
    return "".join([
        f"<p>Petty cash <b>{esc(doc.float)}</b>, week ending <b>{esc(str(doc.week_ending))}</b>, ",
        f"submitted for review by {esc(doc.custodian_name or doc.modified_by or '')}.</p>",
        "<table cellpadding='4' style='border-collapse:collapse;font-family:monospace'>",
        f"<tr><td>Opening</td><td align='right'>{money(doc.opening_balance)}</td></tr>",
        f"<tr><td>Paid out</td><td align='right'>{money(doc.total_out)}</td></tr>",
        f"<tr><td>Received</td><td align='right'>{money(doc.total_in)}</td></tr>",
        f"<tr><td>Expected close</td><td align='right'>{money(doc.expected_close)}</td></tr>",
        f"<tr><td>Cash counted</td><td align='right'>"
        f"{money(doc.cash_count_end) if doc.cash_count_end else '— not counted —'}</td></tr>",
        f"<tr><td><b>Variance</b></td><td align='right'><b>{money(variance)}</b></td></tr>",
        "</table>",
        "<p>The full week is attached. This is the last look before sign-off.</p>"
        if abs(variance) < 0.5 else
        f"<p><b>The float does not reconcile — off by {money(abs(variance))}.</b> "
        "The full week is attached.</p>",
        f"<p style='color:#667'>{esc(doc.name)} · sent automatically on submission.</p>",
    ])
