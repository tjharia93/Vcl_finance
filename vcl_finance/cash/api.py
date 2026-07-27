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
import json

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


# ---------------------------------------------------------------------------
# Daily cash & debt position
#
# The recon itself lives OUTSIDE Frappe (bank_rec/daily_position.py): it needs
# the bank-statement parsers, the QBO refresh tokens and the finance drive, none
# of which exist on Frappe Cloud. That job POSTs its result to record_position();
# Compass reads it back through daily_position(). Frappe stores and serves the
# number, it never derives one.
#
# Consequence worth stating plainly: this endpoint is only as fresh as the last
# job run. It reports its own as-at rather than implying "now".

CASH_POSITION_DOCTYPE = "Daily Cash Position"


def _bal(line, field, present_field):
    """Currency columns cannot be NULL, so a missing source reads as 0.0. The
    *_present flags carry that distinction; honour them or the card will show a
    confident zero for an account nobody has a statement for."""
    return float(getattr(line, field) or 0.0) if getattr(line, present_field) else None


@frappe.whitelist()
def daily_position(position_date: str | None = None):
    """The latest recorded position (or a specific date), shaped for Compass."""
    _assert_privileged()

    filters = {"position_date": position_date} if position_date else {}
    names = frappe.get_all(CASH_POSITION_DOCTYPE, filters=filters, pluck="name",
                           order_by="position_date desc", limit=1)
    if not names:
        return {
            "available": False,
            "note": ("No cash position has been recorded yet. It is written by the "
                     "daily bank recon job when a statement lands on the drive."),
        }

    doc = frappe.get_doc(CASH_POSITION_DOCTYPE, names[0])
    return {
        "available": True,
        "currency": doc.currency or "KES",
        "as_at": doc.as_at,
        "position_date": str(doc.position_date),
        "bank_range": doc.bank_range,
        "rows": [
            {
                "key": l.account_key,
                "account": l.account,
                "short": l.short_label,
                "ref": l.account_ref,
                "group": l.position_group,
                "bank": _bal(l, "bank_balance", "bank_present"),
                # What can actually be drawn today. Distinct from `bank` by
                # 1.85M across the KES accounts, so the card leads with this and
                # shows `bank` as the actual beneath it.
                "bank_available": _bal(l, "bank_available", "bank_available_present"),
                "bank_source": l.bank_source or None,
                "bank_as_at": l.bank_as_at or None,
                "qbo": _bal(l, "qbo_balance", "qbo_present"),
                "erp": _bal(l, "erp_balance", "erp_present"),
                "status": l.status,
                "note": l.line_note,
            }
            for l in doc.lines
        ],
        "covered": {
            "accounts": doc.covered_accounts or 0,
            "bank": float(doc.covered_bank or 0),
            "bank_available": float(doc.covered_bank_available or 0),
            "qbo": float(doc.covered_qbo or 0),
            "erp": float(doc.covered_erp or 0),
            "variance": float(doc.covered_variance or 0),
        },
        "debt_qbo": float(doc.debt_qbo or 0),
        "debt_erp": float(doc.debt_erp or 0),
        "note": doc.note,
    }


@frappe.whitelist(methods=["POST"])
def record_position(payload):
    """Upsert one day's position. Called by the recon job, never by a browser.

    Upsert rather than insert: the statements are re-downloaded through the day
    (10:29 and 12:54 on 25-Jul), and each download should refresh the day's
    position, not append another one.
    """
    _assert_privileged()

    if isinstance(payload, str):
        payload = json.loads(payload)
    if not payload.get("available"):
        frappe.throw(_("Refusing to record a position the job marked unavailable."))

    pdate = payload.get("as_at_date") or frappe.utils.today()
    cov = payload.get("covered") or {}

    name = frappe.db.exists(CASH_POSITION_DOCTYPE, {"position_date": pdate})
    doc = (frappe.get_doc(CASH_POSITION_DOCTYPE, name) if name
           else frappe.new_doc(CASH_POSITION_DOCTYPE))

    doc.position_date = pdate
    doc.as_at = payload.get("as_at")
    doc.bank_range = payload.get("bank_range")
    doc.currency = payload.get("currency") or "KES"
    doc.covered_accounts = cov.get("accounts") or 0
    doc.covered_bank = cov.get("bank") or 0
    doc.covered_bank_available = cov.get("bank_available") or 0
    doc.covered_qbo = cov.get("qbo") or 0
    doc.covered_erp = cov.get("erp") or 0
    doc.covered_variance = cov.get("variance") or 0
    doc.debt_qbo = payload.get("debt_qbo") or 0
    doc.debt_erp = payload.get("debt_erp") or 0
    doc.note = payload.get("note")
    doc.source_payload = json.dumps(payload, indent=1)

    doc.set("lines", [])
    for r in payload.get("rows") or []:
        doc.append("lines", {
            "account_key": r.get("key"),
            "account": r.get("account"),
            "short_label": r.get("short"),
            "account_ref": r.get("ref"),
            "position_group": r.get("group"),
            "bank_balance": r.get("bank") or 0,
            "bank_present": 1 if r.get("bank") is not None else 0,
            # Baroda USD carries no Available in the tracker, so `is not None`
            # rather than truthiness — a genuine 0.00 must stay a zero and a
            # missing figure must stay blank.
            "bank_available": r.get("bank_available") or 0,
            "bank_available_present": 1 if r.get("bank_available") is not None else 0,
            "bank_source": r.get("bank_source"),
            "bank_as_at": r.get("bank_as_at"),
            "qbo_balance": r.get("qbo") or 0,
            "qbo_present": 1 if r.get("qbo") is not None else 0,
            "erp_balance": r.get("erp") or 0,
            "erp_present": 1 if r.get("erp") is not None else 0,
            "status": r.get("status") or "open",
            "line_note": r.get("note"),
        })

    doc.save(ignore_permissions=False)
    frappe.db.commit()
    return {"ok": True, "name": doc.name, "position_date": str(doc.position_date),
            "lines": len(doc.lines), "updated": bool(name)}
