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
        self.validate_week_link()
        self.lock_native_week()
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

    def validate_week_link(self):
        """Which week this entry belongs to — one link, one authority.

        An entry is exactly one of: MIRRORED (``origin_sheet`` set — it belongs to that
        sheet, whatever its date says), NATIVE (``week_sheet`` set), or UNLINKED
        (neither — none exist, and no new one is accepted). See ``weeks.py``.

        For a native entry ``week_sheet`` is the single authority:
          - the sheet must exist and keep its lines as entries;
          - the date is required and must fall in that sheet's Sun–Sat week;
          - ``float`` is the sheet's, copied here, never typed;
          - ``company`` only DEFAULTS from the sheet. It is a decision made on the line
            (one tin serves four companies), so a value already set is left alone;
          - the link is not editable. Changing a line's week goes through the
            controlled move, which sets ``flags.controlled_move``.
        """
        if self.origin_sheet and self.week_sheet:
            frappe.throw(
                _("An entry is linked to its week either by the sheet row it was copied from "
                  "or directly, never both."),
                title=_("Two week links"))

        if not self.week_sheet:
            if self.is_new() and not self.origin_sheet and not self.flags.allow_unlinked:
                frappe.throw(
                    _("A petty cash entry must belong to a week. Key it on a week's sheet."),
                    title=_("No week"))
            return

        sheet = frappe.db.get_value(
            "Petty Cash Sheet", self.week_sheet,
            ["name", "week_ending", "float", "company", "native_lines", "docstatus"], as_dict=True)
        if not sheet or sheet.docstatus == 2:
            frappe.throw(_("Week sheet {0} does not exist.").format(self.week_sheet))
        if not sheet.native_lines:
            frappe.throw(
                _("Week sheet {0} keeps its lines as rows on the sheet. A line cannot be "
                  "added to it as an entry.").format(sheet.name),
                title=_("Week keeps rows"))

        if not self.is_new() and not self.flags.controlled_move:
            was = frappe.db.get_value("Petty Cash Entry", self.name, "week_sheet")
            if was and was != self.week_sheet:
                frappe.throw(
                    _("A line is moved to another week with Move to week, not by editing "
                      "its week."),
                    title=_("Use Move to week"))

        if not self.txn_date:
            frappe.throw(_("A date is required. The week a line belongs to is checked against it."))
        if week_saturday(self.txn_date) != week_saturday(sheet.week_ending):
            frappe.throw(
                _("{0} is not in the week of sheet {1} (week ending {2}). Move the line to "
                  "the right week instead of changing its date.").format(
                    frappe.format(self.txn_date, {"fieldtype": "Date"}), sheet.name,
                    frappe.format(sheet.week_ending, {"fieldtype": "Date"})),
                title=_("Date is outside the week"))

        if self.float and self.float != sheet.float and not self.is_new():
            frappe.throw(
                _("This line belongs to the {0} float through its week sheet. The float is "
                  "not changed on the line.").format(sheet.float),
                title=_("Float comes from the week"))
        self.float = sheet.float
        if not self.company:
            self.company = sheet.company
        self.sync_state = "Native"

    def lock_native_week(self):
        """Native lines only: take the week's lock, then apply the change rules.

        Whatever saves a native entry — a keying action, the desk form, a REST call —
        this runs. It takes the row lock on the week sheet (and on the week the line
        is leaving, in name order) BEFORE anything is judged, so a change and a
        close/approve can never interleave. Then ``native.enforce_change_rules``
        decides, from who is asking, what changed, the line's state and the week's
        state, whether the change is allowed and what it does to the finance tick.

        Remembered for ``on_update``: which kinds of field changed, and the week the
        line was on before, so both weeks' stored totals are refreshed.
        """
        self.flags.native_groups = set()
        self.flags.native_was_on = None
        if not self.week_sheet:
            return
        from vcl_finance.petty_cash import native

        before = None if self.is_new() else self.get_doc_before_save()
        groups = native.changed_groups(self, before)
        if not groups:
            return
        if before and before.get("week_sheet") and before.get("week_sheet") != self.week_sheet:
            self.flags.native_was_on = before.get("week_sheet")
        locked = native.lock_sheets(self.week_sheet, self.flags.native_was_on)
        native.enforce_change_rules(self, before, groups, locked)
        # The rules may themselves clear the tick or raise the changed-after-sign-off flag.
        self.flags.native_groups = native.changed_groups(self, before)

    def on_update(self):
        """Native lines only: the week's stored totals and revision follow the entry,
        and a change Finance made to a reviewed line is written down in plain words."""
        groups = self.flags.get("native_groups") or set()
        if not self.week_sheet or not groups:
            return
        from vcl_finance.petty_cash import native
        cash = bool(groups & native.CASH_GROUPS)
        native.refresh_sheet(self.week_sheet, cash_changed=cash, because=self)
        if self.flags.get("native_was_on"):
            native.refresh_sheet(self.flags.native_was_on, cash_changed=True, because=self)
        for note in (self.flags.get("audit_notes") or []):
            self.add_comment("Info", note)
        self.flags.audit_notes = []

    def after_delete(self):
        if self.week_sheet:
            from vcl_finance.petty_cash import native
            native.lock_sheet(self.week_sheet)
            native.refresh_sheet(self.week_sheet)

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
