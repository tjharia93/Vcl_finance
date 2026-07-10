from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate


VEHICLES = ["KAP 466", "KAY 635", "KCB 430", "KBQ 788", "KBT 972"]
# Petty-cash week runs Sunday → Saturday (week_ending = Saturday). Day columns are
# ordered Sun..Sat so the last column IS the week_ending anchor.
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
CATEGORY_CODES = ["TG", "TE", "SE", "OA", "FD", "GP", "OT"]
VOUCHER_ROWS = 18
WAGES_ROWS = 18
LOAN_ROWS = 8
BIKE_ROWS = 6
FORKLIFT_ROWS = 4

# Whoever may unlock a row (and edit / delete a locked one). Mirrors api.PETTY_PRIV.
LOCK_OVERRIDE_ROLES = {"Accounts Manager", "System Manager"}

# The user-meaningful fields the row lock protects, per child table, with the type
# used to normalise each side before comparing.
#
# DELIBERATELY EXCLUDED: idx, row_idx, day_idx, slot — this controller rewrites all
# four *itself* on every save (autosort_vouchers_by_date / autosort_parking_by_date
# rewrite idx + row_idx; derive_parking_days rewrites day_idx + slot). Comparing
# them would make the guard fire on an ordinary no-op re-save of any sheet holding
# a locked row. Metadata (name/owner/creation/modified/parent*/docstatus/doctype)
# is excluded for the same reason. `cancelled_on` / `cancel_remark` ride along with
# `cancelled`, which IS compared.
LOCK_COMPARE_FIELDS = {
    "vouchers": (
        ("txn_date", "date"), ("voucher_no", "text"), ("recipient", "text"),
        ("category", "text"), ("amount", "money"), ("cash_in", "check"),
        ("pc_received", "check"), ("etr_received", "check"), ("receipt", "text"),
        ("notes", "text"), ("cancelled", "check"), ("locked", "check"),
    ),
    "wages_entries": (
        ("txn_date", "date"), ("entry_type", "text"), ("recipient", "text"),
        ("staff_id", "text"), ("reason", "text"), ("amount", "money"),
        ("paye", "check"), ("recipient_signed", "check"),
        ("authorised_signed", "check"), ("cancelled", "check"), ("locked", "check"),
    ),
    "loan_entries": (
        ("txn_date", "date"), ("recipient", "text"), ("staff_id", "text"),
        ("reason", "text"), ("amount_issued", "money"), ("amount_signed", "money"),
        ("paye", "check"), ("cancelled", "check"), ("locked", "check"),
    ),
    "parking_entries": (
        ("txn_date", "date"), ("vehicle", "text"), ("amount", "money"),
        ("cancelled", "check"), ("locked", "check"),
    ),
    "misc_entries": (
        ("kind", "text"), ("txn_date", "date"), ("amount", "money"),
        ("recipient_signed", "check"), ("notes", "text"),
        ("cancelled", "check"), ("locked", "check"),
    ),
}


# Every child table that carries a `txn_date`, with the label + amount field used
# when validate_row_weeks() names an offending row.
DATED_TABLES = (
    ("vouchers", "Voucher", "amount"),
    ("wages_entries", "Wages", "amount"),
    ("loan_entries", "Loan", "amount_issued"),
    ("misc_entries", "Misc", "amount"),
    ("parking_entries", "Parking", "amount"),
)


def week_saturday(d):
    """The Saturday that ENDS the Sun–Sat week CONTAINING ``d``.

    Python weekday(): Mon=0 … Sat=5, Sun=6, so ``(5 - wd) % 7`` is 0 when ``d`` IS
    a Saturday and 6 when it's a Sunday (which opens a fresh week). This is the
    single definition of "which week does this date belong to" — week_dates,
    week_span, validate_row_weeks and api.quick_entry all route through it.
    """
    d = _as_date(d)
    return d + timedelta(days=(5 - d.weekday()) % 7)


def week_bounds(d):
    """(sunday, saturday) of the Sun–Sat week containing ``d``."""
    sat = week_saturday(d)
    return sat - timedelta(days=6), sat


def _lock_norm(value, kind):
    """Canonicalise one field value so a no-op round-trip never looks like a change.

    The browser POSTs an ISO string where the DB holds a ``date``, ``0`` where the
    DB holds ``None``, ``250.0`` where the DB holds ``Decimal("250.00")``. Compare
    the meaning, not the representation.
    """
    if kind == "check":
        return 1 if cint(value) else 0
    if kind == "money":
        return flt(value, 2)
    if kind == "date":
        return getdate(value) if value else None
    return str(value).strip() if value is not None else ""


class PettyCashSheet(Document):
    """Parent weekly petty-cash record. Owns the voucher / parking / misc / wages child tables."""

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def validate(self):
        self.guard_locked_write()
        # MUST run before validate_week_ending / ensure_grid / derive_parking_days /
        # the autosorts / compute_totals: those steps mutate the child rows
        # themselves, so the incoming payload has to be compared against the DB
        # state while it is still exactly what the client sent.
        self.guard_locked_rows()
        # Same reason, same window: compare the INCOMING dates against the DB state
        # before ensure_grid / derive_parking_days / the autosorts touch any row.
        self.validate_row_weeks()
        self.validate_week_ending()
        self.validate_unique_float()
        self.derive_week_no()
        self.ensure_grid()
        self.derive_parking_days()
        self.autosort_vouchers_by_date()
        self.autosort_parking_by_date()
        self.compute_totals()

    def derive_parking_days(self):
        """Keep ``day_idx`` in lockstep with ``txn_date`` on every parking row.

        Parking is captured as dated single entries (like the Voucher Register) —
        the custodian never picks a weekday. The weekly print format still buckets
        parking into a Sun..Sat × vehicle grid off ``day_idx``, so every row MUST
        carry one of DAY_NAMES or its money silently vanishes from the filing copy.

        Rules, in order:
          - ``txn_date`` set  → day_idx = that date's day name (always in DAY_NAMES);
          - legacy undated row → its existing day_idx is left untouched;
          - neither            → fall back to the week-ending day so the row still prints.

        ``slot`` is legacy (the old 2-slots-per-day grid). New rows keep 1 so any
        code path that reads it sees a number rather than None.
        """
        for p in self.parking_entries:
            if p.txn_date:
                # Python weekday(): Mon=0 … Sun=6. DAY_NAMES is Sun-first, so shift by one.
                p.day_idx = DAY_NAMES[(_as_date(p.txn_date).weekday() + 1) % 7]
            elif p.day_idx not in DAY_NAMES:
                p.day_idx = DAY_NAMES[-1]
            if not p.slot:
                p.slot = 1

    def autosort_parking_by_date(self):
        """Parking rows sit in transaction-date order — same semantics as the
        Voucher Register: dated ascending, undated last keeping their order, ties
        stable on current idx. Frozen once the week is locked."""
        if self.is_locked():
            return

        def sort_key(p):
            d = _as_date(p.txn_date) if p.txn_date else None
            return (d is None, d or date.max, p.idx or 0)

        self.parking_entries.sort(key=sort_key)
        for new_idx, p in enumerate(self.parking_entries, start=1):
            p.idx = new_idx

    def autosort_vouchers_by_date(self):
        """Re-number the Voucher Register so rows sit in transaction-date order.

        Dated rows sort ascending. Undated rows (the blank grid rows and freshly
        appended lines) sink to the bottom keeping their existing order, ready to
        fill. Ties on the same date preserve the current row_idx, so saving twice
        never reshuffles same-date rows.

        Order is written to BOTH row_idx and idx: the Compass grid reads row_idx,
        while the print format iterates the child table in idx order — they must
        agree or the filing copy won't match what the editor shows.

        Locked weeks (Closed / Submitted / Approved) are frozen: an already-filed
        copy must never reorder under us.
        """
        if self.is_locked():
            return

        def sort_key(v):
            d = _as_date(v.txn_date) if v.txn_date else None
            return (d is None, d or date.max, v.row_idx or 0)

        self.vouchers.sort(key=sort_key)
        for new_idx, v in enumerate(self.vouchers, start=1):
            v.row_idx = new_idx
            v.idx = new_idx

    def guard_locked_write(self):
        """ORM-layer lock: a Closed/Submitted/Approved week may only be saved by an
        Accounts Manager. Covers direct doctype writes (the Compass grid) that bypass
        api._assert_can_write."""
        if self.is_new() or not self.is_locked():
            return
        if set(frappe.get_roles()) & LOCK_OVERRIDE_ROLES:
            return
        frappe.throw(_("This week is closed. Only an Accounts Manager can edit it."),
                     frappe.PermissionError)

    def guard_locked_rows(self):
        """ORM-layer per-ROW lock, orthogonal to the week-level ``guard_locked_write``.

        A row with ``locked = 1`` is frozen: live, counted, and part of the sheet —
        just immutable. The custodian may TICK a row to lock it; only an Accounts
        Manager may untick it, edit it, or delete it.

        This is the only real enforcement point. The Compass grid saves the FULL
        document via the Frappe REST API, bypassing every ``api.py`` helper, so a
        UI-only lock would be trivially defeated.

        Rules, per child row, matched on the child ``name``:
          - old row unlocked → anything goes, including ticking it locked;
          - old row locked + Accounts Manager → anything goes, including unticking;
          - old row locked + anyone else → the row must be byte-for-byte unchanged
            across LOCK_COMPARE_FIELDS, and must still be present (no delete).

        Stamps ``locked_by`` / ``locked_on`` on the 0 → 1 transition and clears them
        on 1 → 0. A row that stays locked keeps its ORIGINAL stamp: the fields are
        ``read_only`` in the UI only, so we restore them server-side rather than
        trust whatever the client echoed back.
        """
        before = self.get_doc_before_save()
        if before is None:
            return  # insert: nothing to compare against, nothing can be locked yet

        is_am = bool(set(frappe.get_roles()) & LOCK_OVERRIDE_ROLES)

        for table, fields in LOCK_COMPARE_FIELDS.items():
            old_rows = {r.name: r for r in (before.get(table) or []) if r.name}
            new_rows = {r.name: r for r in (self.get(table) or []) if r.name}

            for name, old in old_rows.items():
                if not cint(old.get("locked")):
                    continue  # unlocked yesterday → no protection today

                new = new_rows.get(name)
                if new is None:
                    if is_am:
                        continue  # an Accounts Manager may remove a locked row
                    frappe.throw(
                        _("This row is locked and cannot be deleted. "
                          "Only an Accounts Manager can remove it."),
                        frappe.PermissionError,
                    )
                if is_am:
                    continue

                for fieldname, kind in fields:
                    if _lock_norm(old.get(fieldname), kind) != _lock_norm(new.get(fieldname), kind):
                        frappe.throw(
                            _("This row is locked. Only an Accounts Manager can change it."),
                            frappe.PermissionError,
                        )
                # Unchanged, so still locked — pin the original stamp back on.
                new.locked_by = old.locked_by
                new.locked_on = old.locked_on

            for name, new in new_rows.items():
                was_locked = cint(old_rows[name].get("locked")) if name in old_rows else 0
                now_locked = cint(new.get("locked"))
                if now_locked and not was_locked:
                    new.locked_by = frappe.session.user
                    new.locked_on = frappe.utils.now()
                elif was_locked and not now_locked:
                    # Only reachable for an Accounts Manager — the loop above throws
                    # for anyone else before we get here.
                    new.locked_by = None
                    new.locked_on = None

    def validate_row_weeks(self):
        """A dated child row must fall inside this sheet's Sun–Sat span.

        A transaction belongs to the week that CONTAINS its date, never to the week
        that happened to be open when the custodian typed it in. Recording Sat 04/07
        on the sheet ending 11/07 files the money in the wrong week and duplicates
        it against the sheet that already holds it.

        SCOPE — only rows that are NEW or whose ``txn_date`` CHANGED in this save.
        Sheets already carrying an out-of-week row (PCS-2026-00018 holds 12) must
        stay saveable: a blanket check would brick them, and their cleanup is a
        separate, approval-gated job. So we diff against ``get_doc_before_save()``
        and grandfather every dated row we aren't touching. On insert there's
        nothing to diff against, so every dated row is checked.

        Skips undated rows (the blank scaffolding, legacy day_idx parking) and
        cancelled rows.
        """
        if not self.week_ending or self.docstatus == 2:
            return
        sunday, saturday = week_bounds(self.week_ending)
        before = self.get_doc_before_save()

        for table, label, amount_field in DATED_TABLES:
            old_rows = {r.name: r for r in (before.get(table) or []) if r.name} if before else {}

            for row in (self.get(table) or []):
                if cint(row.get("cancelled")):
                    continue

                old = old_rows.get(row.name) if row.name else None

                if not row.get("txn_date"):
                    # A line that carries money MUST be dated — the week is derived
                    # from the date, so an undated line has no week. But two kinds of
                    # undated row are legitimate and must never throw:
                    #   * the blank rows ensure_grid() scaffolds (no money on them);
                    #   * legacy parking rows recorded against a weekday alone, before
                    #     txn_date existed (PCS-2026-00016 holds 16 with money on them).
                    # So only NEW or newly-funded rows are required to carry a date.
                    if not flt(row.get(amount_field)):
                        continue
                    if (old is not None and not old.get("txn_date")
                            and flt(old.get(amount_field))
                            and flt(old.get(amount_field)) == flt(row.get(amount_field))):
                        # An ALREADY-FUNDED undated legacy row, left untouched. Grandfathered.
                        # A blank scaffold row that GAINS money, or a legacy row whose amount
                        # is edited, is a new fact and must carry a date.
                        continue
                    frappe.throw(_(
                        "{0} row for {1} (KES {2}) has no date. Every entry needs a "
                        "date — the week it belongs to is derived from it."
                    ).format(label, row.get("recipient") or "\u2014",
                             frappe.format_value(flt(row.get(amount_field)), {"fieldtype": "Currency"})))

                d = getdate(row.get("txn_date"))

                if old is not None:
                    old_d = getdate(old.txn_date) if old.txn_date else None
                    if old_d == d:
                        continue  # pre-existing date, untouched → grandfathered

                if sunday <= d <= saturday:
                    continue

                frappe.throw(_(
                    "{0} row dated {1} ({2} · KES {3}) does not belong to this week "
                    "({4} – {5}). It belongs to the sheet for the week ending {6} — "
                    "record it there."
                ).format(
                    label, d.strftime("%d/%m/%Y"), _row_who(table, row),
                    "{:,.2f}".format(flt(row.get(amount_field), 2)),
                    sunday.strftime("%d/%m/%Y"), saturday.strftime("%d/%m/%Y"),
                    week_saturday(d).strftime("%d/%m/%Y"),
                ), title=_("Entry is in the wrong week"))

    def check_if_locked(self):
        # Frappe Cloud's shared filesystem can leave a "phantom" document lock:
        # os.path.exists() reports the .lock file present, but stat() 404s, so the
        # core lock-age check (file_lock.lock_age) crashes every save with
        # FileNotFoundError. Swallow only that specific glitch so a transient stale
        # lock file can't block petty-cash saves; a genuine DocumentLockedError still
        # propagates.
        try:
            super().check_if_locked()
        except FileNotFoundError:
            pass

    def before_save(self):
        # `closing_balance` mirrors `expected_close` so legacy reports stay aligned.
        self.closing_balance = self.expected_close

    # ------------------------------------------------------------------
    # Carry-forward — opening balance from the prior week's close
    # ------------------------------------------------------------------

    def carry_forward_opening(self):
        """Set ``opening_balance`` from the most recent prior sheet for the SAME float.

        Basis (Tanuj 2026-06-10):
          - counted cash (``cash_count_end``) if a physical count was entered;
          - otherwise the expected close (opening − out + in);
          - a NEGATIVE close is carried **as-is** — it's a real signal the float is
            overdrawn / awaiting reimbursement, never clamped to zero.

        Returns the chosen opening (or ``None`` when there's no prior sheet, i.e.
        the first week for this float). Only mutates ``opening_balance``; the
        caller decides when to persist.
        """
        info = _prior_close(self.float, self.week_ending, exclude=self.name)
        if info is None:
            return None
        self.opening_balance = info["balance"]
        return info["balance"]

    def is_locked(self):
        """A week is locked once closed (or historically Submitted/Approved)."""
        return self.status in ("Closed", "Submitted", "Approved")

    def on_submit(self):
        self.status = "Submitted"

    def on_cancel(self):
        self.status = "Draft"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_week_ending(self):
        if not self.week_ending:
            return
        if self.backfill:
            # Historical sheets imported from the intranet keep their exact
            # recorded week-ending even when it isn't a Saturday. New sheets still
            # enforce the Sun->Sat rule below.
            return
        we = _as_date(self.week_ending)
        if we.weekday() != 5:  # Saturday
            frappe.throw(_("Week Ending must be a Saturday. Got {0} ({1}).").format(
                we.isoformat(), we.strftime("%a")
            ))

    def validate_unique_float(self):
        """Composite uniqueness on (week_ending, float).

        Frappe's per-field ``unique`` flag can't express a composite key, so we
        enforce it here: reject a second non-cancelled sheet with the same Saturday
        and float. Cash and Hauz-Pay on the same Saturday are allowed.
        """
        if not self.week_ending or not self.float:
            return
        we = _as_date(self.week_ending)
        clash = frappe.db.get_value(
            "Petty Cash Sheet",
            {
                "week_ending": we,
                "float": self.float,
                "name": ("!=", self.name or ""),
                "docstatus": ("<", 2),
            },
            "name",
        )
        if clash:
            frappe.throw(_(
                "A Petty Cash Sheet for {0} on the {1} float already exists ({2})."
            ).format(we.isoformat(), self.float, clash))

    def derive_week_no(self):
        if self.week_ending:
            we = _as_date(self.week_ending)
            self.week_no = we.isocalendar()[1]

    # ------------------------------------------------------------------
    # Grid scaffolding — ensures every editor cell has a backing row
    # ------------------------------------------------------------------

    def ensure_grid(self):
        """Idempotently back-fill empty child rows so the editor UI renders cleanly.

        Skips on submitted documents so they stay frozen.

        Parking is deliberately absent: parking rows are dated single entries created
        on demand (like vouchers), not a pre-scaffolded 7×5×2 grid. Blank parking rows
        left on existing sheets by the old scaffolding are harmless (amount 0) and are
        never deleted here.
        """
        if self.docstatus == 1:
            return

        # Vouchers — 18 rows
        existing = {(v.row_idx or 0) for v in self.vouchers}
        for i in range(1, VOUCHER_ROWS + 1):
            if i not in existing:
                self.append("vouchers", {"row_idx": i})

        # Misc — 6 bike + 4 forklift
        existing_bike = {(m.kind, m.row_idx) for m in self.misc_entries if m.kind == "Bike Fuel"}
        for i in range(1, BIKE_ROWS + 1):
            if ("Bike Fuel", i) not in existing_bike:
                self.append("misc_entries", {"kind": "Bike Fuel", "row_idx": i})
        existing_fl = {(m.kind, m.row_idx) for m in self.misc_entries if m.kind == "Forklift"}
        for i in range(1, FORKLIFT_ROWS + 1):
            if ("Forklift", i) not in existing_fl:
                self.append("misc_entries", {"kind": "Forklift", "row_idx": i})

        # Wages — 18 rows
        existing_wages = {(w.row_idx or 0) for w in self.wages_entries}
        for i in range(1, WAGES_ROWS + 1):
            if i not in existing_wages:
                self.append("wages_entries", {"row_idx": i, "entry_type": "Wage"})

        # Loans — 8 rows
        existing_loans = {(l.row_idx or 0) for l in self.loan_entries}
        for i in range(1, LOAN_ROWS + 1):
            if i not in existing_loans:
                self.append("loan_entries", {"row_idx": i})

    # ------------------------------------------------------------------
    # Totals — re-computed on every save so the form, list, and print
    # views always see the same numbers.
    # ------------------------------------------------------------------

    def compute_totals(self):
        # Cancelled (voided) rows stay on the sheet for audit but never count
        # toward any total, expected close, variance, or posting.
        cat = {c: 0.0 for c in CATEGORY_CODES}
        voucher_out = 0.0
        total_in = 0.0
        for v in self.vouchers:
            if v.cancelled:
                continue
            amt = v.amount or 0
            if v.cash_in:
                total_in += amt
            else:
                voucher_out += amt
                if v.category in cat:
                    cat[v.category] += amt

        parking_out = sum((p.amount or 0) for p in self.parking_entries if not p.cancelled)
        misc_out = sum((m.amount or 0) for m in self.misc_entries if not m.cancelled)
        wages_out = sum((w.amount or 0) for w in self.wages_entries if not w.cancelled)
        # Only the cash actually issued leaves the float.
        loans_out = sum((l.amount_issued or 0) for l in self.loan_entries if not l.cancelled)

        self.total_out = voucher_out + parking_out + misc_out + wages_out + loans_out
        self.total_in = total_in
        self.expected_close = (self.opening_balance or 0) - self.total_out + self.total_in
        self.variance = (self.cash_count_end or 0) - self.expected_close


# ----------------------------------------------------------------------
# Helpers — re-used by website pages / print format
# ----------------------------------------------------------------------

def _as_date(v):
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return datetime.fromisoformat(str(v)).date()


def _row_who(table, row):
    """Who/what a child row is about — for error messages. Parking has a vehicle,
    misc has a kind + notes, everything else has a recipient."""
    if table == "parking_entries":
        return row.get("vehicle") or "parking"
    if table == "misc_entries":
        return (row.get("kind") or "misc") + (f" · {row.get('notes')}" if row.get("notes") else "")
    return (row.get("recipient") or "").strip() or (row.get("voucher_no") or "").strip() or "no recipient"


def _closing_cash(doc):
    """The cash to carry out of a sheet → (balance, basis).

    Prefer the physical count if one was entered; otherwise the expected close.
    Negative is carried as-is (Tanuj 2026-06-10).
    """
    counted = doc.cash_count_end or 0
    if counted:
        return round(counted, 2), "counted"
    return round(doc.expected_close or 0, 2), "expected"


def _prior_close(float_name, week_ending, exclude=None):
    """Carry-forward decision from the most recent sheet for ``float_name`` ending
    strictly before ``week_ending``. Returns None when there's no prior sheet.

    Skips cancelled sheets (docstatus 2). ``exclude`` drops a sheet by name (the
    sheet we're computing for, on re-save).
    """
    if not float_name or not week_ending:
        return None
    we = _as_date(week_ending)
    filters = {
        "float": float_name,
        "week_ending": ("<", we),
        "docstatus": ("<", 2),
    }
    if exclude:
        filters["name"] = ("!=", exclude)
    rows = frappe.get_all(
        "Petty Cash Sheet",
        filters=filters,
        fields=["name", "week_no", "week_ending"],
        order_by="week_ending desc",
        limit=1,
    )
    if not rows:
        return None
    prev = frappe.get_doc("Petty Cash Sheet", rows[0]["name"])
    bal, basis = _closing_cash(prev)
    return {
        "balance": bal,
        "basis": basis,
        "prior_name": prev.name,
        "label": f"Wk{prev.week_no} · {_as_date(prev.week_ending).isoformat()} · {prev.float}",
    }


@frappe.whitelist()
def carry_forward(float_name="Cash", week_ending=None, before=None):
    """Whitelisted: what opening should a new ``float_name`` sheet carry into the
    week ending ``week_ending`` (or ``before``)? Used by the New-sheet form to
    pre-fill Opening Balance. Returns ``{}`` when there's no prior sheet."""
    we = week_ending or before
    info = _prior_close(float_name or "Cash", we)
    return info or {}


@frappe.whitelist()
def week_dates(week_ending):
    """Sun–Sat ISO date strings for the Sun–Sat week that CONTAINS ``week_ending``.

    Derivation-only (never edits the doc): rolls the stored date forward to the
    Saturday that ends its containing week, then walks back to Sunday. Works for
    ANY stored week_ending — new Saturday-anchored sheets AND historical
    Friday-anchored ones — so every sheet displays/prints as Sun–Sat without
    touching its recorded week_ending or carry-forward chain.
    """
    sunday, _saturday = week_bounds(week_ending)
    return [(sunday + timedelta(days=i)).isoformat() for i in range(7)]


@frappe.whitelist()
def week_span(week_ending):
    """The derived Sun–Sat span that CONTAINS ``week_ending`` — shared by the print
    format + Compass UI so every sheet (historical Friday-anchored included) renders
    a Sunday-start / Saturday-end week without editing the stored week_ending.

    Returns ``{"sunday", "saturday", "week_no", "dates": [Sun..Sat]}``. ``saturday``
    is the display/print week-ending; ``week_no`` is the ISO week of that Saturday.
    """
    sunday, saturday = week_bounds(week_ending)
    return {
        "sunday": sunday.isoformat(),
        "saturday": saturday.isoformat(),
        "week_no": saturday.isocalendar()[1],
        "dates": [(sunday + timedelta(days=i)).isoformat() for i in range(7)],
    }


@frappe.whitelist()
def summary(name):
    """Return a JSON-friendly summary block for a sheet — used by the editor's live totals.

    Re-computes server-side rather than trusting the client. Safe to call repeatedly.
    """
    doc = frappe.get_doc("Petty Cash Sheet", name)
    cat = {c: 0.0 for c in CATEGORY_CODES}
    cat_in = 0.0
    voucher_count = 0
    pc_count = 0
    etr_count = 0
    for v in doc.vouchers:
        if v.cancelled:  # voided rows excluded from all totals + counts
            continue
        amt = v.amount or 0
        if v.cash_in:
            cat_in += amt
        elif v.category in cat:
            cat[v.category] += amt
        if v.voucher_no or v.recipient or amt:
            voucher_count += 1
        if v.pc_received:
            pc_count += 1
        if v.etr_received:
            etr_count += 1

    parking_by_vehicle = {v: 0.0 for v in VEHICLES}
    parking_by_day = {d: 0.0 for d in DAY_NAMES}
    for p in doc.parking_entries:
        if p.cancelled:
            continue
        parking_by_vehicle[p.vehicle] = parking_by_vehicle.get(p.vehicle, 0) + (p.amount or 0)
        parking_by_day[p.day_idx] = parking_by_day.get(p.day_idx, 0) + (p.amount or 0)

    bike_total = sum((m.amount or 0) for m in doc.misc_entries if m.kind == "Bike Fuel" and not m.cancelled)
    forklift_total = sum((m.amount or 0) for m in doc.misc_entries if m.kind == "Forklift" and not m.cancelled)
    wages_total = sum((w.amount or 0) for w in doc.wages_entries if not w.cancelled)
    loans_total = sum((l.amount_issued or 0) for l in doc.loan_entries if not l.cancelled)

    return {
        "cat_out": cat,
        "cat_in": cat_in,
        "voucher_total_out": sum(cat.values()),
        "voucher_count": voucher_count,
        "pc_count": pc_count,
        "etr_count": etr_count,
        "parking_by_vehicle": parking_by_vehicle,
        "parking_by_day": parking_by_day,
        "parking_total": sum(parking_by_vehicle.values()),
        "bike_total": bike_total,
        "forklift_total": forklift_total,
        "wages_total": wages_total,
        "loans_total": loans_total,
        "total_out": doc.total_out,
        "total_in": doc.total_in,
        "expected_close": doc.expected_close,
        "variance": doc.variance,
        "status": doc.status,
        "docstatus": doc.docstatus,
    }


@frappe.whitelist()
def create_for_week(week_ending, custodian_name="Shiro", opening_balance=0, authorised_float=50000, float_name="Cash"):
    """Convenience endpoint: create a new sheet for the given Saturday + float, or return existing.

    Used by the Website Page "New Sheet" form so the custodian doesn't have to
    pick a Naming Series manually. Idempotent on the (week_ending, float) key.
    """
    we = _as_date(week_ending)
    float_name = float_name or "Cash"
    existing = frappe.db.get_value(
        "Petty Cash Sheet", {"week_ending": we, "float": float_name}, "name"
    )
    if existing:
        return existing
    doc = frappe.new_doc("Petty Cash Sheet")
    doc.week_ending = we
    doc.float = float_name
    doc.custodian_name = custodian_name or "Shiro"
    doc.authorised_float = float(authorised_float or 50000)
    doc.status = "Draft"

    # Opening balance: always carry forward the prior week's close for this float
    # (negative carried as-is — real signal, never clamped). The passed
    # opening_balance is only used as a fallback when there is no prior sheet
    # for this float (i.e. the very first week).
    try:
        fallback = float(opening_balance)
    except (TypeError, ValueError):
        fallback = 0.0
    carried = doc.carry_forward_opening()
    doc.opening_balance = carried if carried is not None else fallback

    doc.insert()
    return doc.name
