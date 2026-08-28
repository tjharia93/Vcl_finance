"""Petty Cash Entry — the per-entry atom the weekly sheet is becoming a view over.

One record = one cash movement. The week is DERIVED from ``txn_date`` (PC-001), not
stored on a container: an entry knows which week it belongs to without asking its
sheet. Phase 1 is additive — the live ``Petty Cash Sheet`` and its five child tables
stay the capture path and keep working, and entries are a read-only mirror of them.
Nothing here posts to the GL; that is phase 5.

Two things here are deliberate and easy to "fix" wrongly:

1. ``week_saturday`` is IMPORTED from the sheet controller rather than reimplemented.
   It is the single definition of "which week does this date belong to" — week_dates,
   week_span, validate_row_weeks and api.quick_entry all route through it. A second
   copy would drift, and the drift would only show up as a reconciliation that never
   reaches zero.

2. ``txn_date`` is NOT reqd in practice for mirrored rows. 151 live child rows carry
   money with no date at all (147 parking, 3 vouchers, 1 loan) — legacy parking was a
   Sun–Sat × vehicle grid keyed on ``day_idx``, a weekday NAME, with ``txn_date`` left
   null by design. For those the container's week is the ONLY week information that
   exists, so ``origin_week_ending`` is the documented fallback.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    DAY_NAMES,
    _as_date,
    week_saturday,
)


# Fields a mirror run must never write. Approval is a human act; the mirror only ever
# reports that a source row MOVED after approval (``changed_after_approval``), and
# never decides what that means.
APPROVAL_FIELDS = (
    "status", "approved_by", "approved_on",
    "withdrawn_by", "withdrawn_on", "withdrawal_reason",
)


class PettyCashEntry(Document):
    def before_save(self):
        self.ensure_origin_row()
        self.derive_week_ending()
        self.validate_withdrawal()
        self.sync_void_stamps()

    # ------------------------------------------------------------------
    def ensure_origin_row(self):
        """A native entry stores its OWN docname in ``origin_row``.

        ``origin_row`` carries a UNIQUE index. MariaDB permits many NULLs but only a
        single empty string, so leaving native rows blank would let the FIRST one
        save and reject every one after it — and Frappe writes ``''`` rather than
        NULL for an untouched Data field, so "just leave it null" does not survive
        the ORM. Storing the docname sidesteps the NULL semantics entirely and reads
        honestly: the row this mirrors, or itself when it is native.

        ``self.name`` is already set here — ``insert()`` runs ``set_new_name()``
        before the before_save methods.
        """
        if not self.origin_row:
            self.origin_row = self.name
            if not self.sync_state:
                self.sync_state = "Native"

    def derive_week_ending(self):
        """``week_ending`` is derived, never typed. PC-001.

        Order:
          - ``txn_date`` set        → the Saturday ending its Sun–Sat week;
          - undated but mirrored    → the Saturday ending the SOURCE SHEET's week;
          - neither                 → left blank rather than guessed. A blank week is
                                      visible and fixable; a wrong one is neither.

        Note the fallback runs ``origin_week_ending`` through ``week_saturday`` too.
        The import-era sheets are labelled with Fridays and Sundays, so the raw value
        is often not a Saturday — normalising it puts an undated row in the same week
        as the dated rows on its own sheet, instead of a week of its own.
        """
        if self.txn_date:
            self.week_ending = week_saturday(self.txn_date)
        elif self.origin_week_ending:
            self.week_ending = week_saturday(self.origin_week_ending)
        else:
            self.week_ending = None

    def validate_withdrawal(self):
        if self.status == "Withdrawn" and not (self.withdrawal_reason or "").strip():
            frappe.throw(
                _("Say why the approval is being withdrawn. A withdrawal with no reason "
                  "is indistinguishable from a mistake."),
                title=_("Reason required"),
            )

    def sync_void_stamps(self):
        """Stamp who voided and when, the same way the child rows do.

        ``status = Void`` rides ALONGSIDE ``cancelled``, never instead of it: the
        sheet's totals filter on ``cancelled``, so clearing it would quietly put voided
        money back into a reconciled week.
        """
        if self.cancelled:
            if not self.cancelled_on:
                self.cancelled_on = now_datetime()
            if not self.cancelled_by:
                self.cancelled_by = frappe.session.user
        else:
            self.cancelled_on = None
            self.cancelled_by = None
            self.cancel_remark = None

    # ------------------------------------------------------------------
    # Helpers used by the mirror. Kept here so there is one definition of each.
    # ------------------------------------------------------------------
    @property
    def signed_amount(self):
        """What this entry does to the float. Cancelled rows move nothing."""
        if self.cancelled:
            return 0.0
        amt = abs(self.amount or 0)
        return amt if self.cash_in else -amt

    def payload_value(self, key, default=None):
        """Read one field back out of the JSON payload without exploding on bad JSON."""
        try:
            return (json.loads(self.payload or "{}") or {}).get(key, default)
        except (ValueError, TypeError):
            return default


def reconstruct_date(week_ending, day_idx):
    """Best-effort date for a legacy undated row that knows only its weekday NAME.

    Legacy parking rows carry ``day_idx`` ("Mon", "Tue", …) and no ``txn_date``.
    Given the sheet's week we can place them on a real day: walk back from that
    week's Saturday to its Sunday and pick the matching name.

    Returns ``None`` when either input is missing or ``day_idx`` is not one of
    DAY_NAMES — the caller then leaves ``txn_date`` empty and the entry falls back to
    the sheet's week, which is still correct, just less precise.
    """
    if not week_ending or day_idx not in DAY_NAMES:
        return None
    from datetime import timedelta

    saturday = week_saturday(_as_date(week_ending))
    sunday = saturday - timedelta(days=6)
    return sunday + timedelta(days=DAY_NAMES.index(day_idx))
