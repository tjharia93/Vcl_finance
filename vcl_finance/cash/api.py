"""Whitelisted JSON API for live cash-account positions.

Currently exposes the MPESA float only, for the System-Manager-only card on the
Compass launchpad.

The balance is read from the LEDGER, deliberately matching the `Cash Report`
server script (the 17:30 "Daily Cash Control" email) line for line — same
account, same `is_cancelled = 0` filter, no date bound. Two readers of the same
number must not drift: if this ever disagrees with the email, the cause should
be *when* it was read, never *how*.

Do NOT reimplement this by summing Payment Entries on ``mode_of_payment``. That
was the old basis and it silently dropped the 2026-07-06 sweep of KES 4,864,833
because the field was left blank (`= 'MPESA'` never matches NULL), so the float
only ever grew. It is also blind to Journal Entries. The account is the single
source of truth: receipts debit it, sweeps to BOB credit it, adjustments post
through it.
"""
import frappe
from frappe import _

# The MPESA float account. Receipts have self-segregated here since the
# Mode of Payment default was repointed on 2026-07-16; before that they were
# commingled in "1110 - Cash - VCL" and were reclassed out by ACC-JV-2026-00485.
MPESA_ACCOUNT = "Cash - MPESA - VCL"

# System Manager only — this is a live treasury number, not a general KPI.
CASH_PRIV = {"System Manager"}


def _assert_privileged():
    if not (set(frappe.get_roles()) & CASH_PRIV):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


@frappe.whitelist()
def mpesa_balance():
    """Live MPESA float: SUM(debit - credit) over every uncancelled GL Entry.

    Returns the account name alongside the figure so the caller can never
    render a number without saying what it is a balance *of*.
    """
    _assert_privileged()

    rows = frappe.db.sql(
        """
        SELECT ROUND(SUM(gle.debit - gle.credit), 2) AS balance
        FROM `tabGL Entry` gle
        WHERE gle.account = %s
          AND gle.is_cancelled = 0
        """,
        (MPESA_ACCOUNT,),
        as_dict=True,
    )
    balance = (rows[0].balance if rows and rows[0].balance else 0.0)

    return {
        "account": MPESA_ACCOUNT,
        "currency": "KES",
        "balance": float(balance),
        "as_at": frappe.utils.now(),
    }
