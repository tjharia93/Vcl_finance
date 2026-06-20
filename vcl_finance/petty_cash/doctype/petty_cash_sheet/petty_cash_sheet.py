from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.model.document import Document


VEHICLES = ["KAP 466", "KAY 635", "KCB 430", "KBQ 788", "KBT 972"]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
CATEGORY_CODES = ["TG", "TE", "SE", "OA", "FD", "GP", "OT"]
VOUCHER_ROWS = 18
WAGES_ROWS = 18
LOAN_ROWS = 8
BIKE_ROWS = 6
FORKLIFT_ROWS = 4


class PettyCashSheet(Document):
    """Parent weekly petty-cash record. Owns the voucher / parking / misc / wages child tables."""

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def validate(self):
        self.validate_week_ending()
        self.validate_unique_float()
        self.derive_week_no()
        self.ensure_grid()
        self.compute_totals()

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
            # recorded week-ending even when it isn't a Friday. New sheets still
            # enforce the Sat->Fri rule below.
            return
        we = _as_date(self.week_ending)
        if we.weekday() != 4:  # Friday
            frappe.throw(_("Week Ending must be a Friday. Got {0} ({1}).").format(
                we.isoformat(), DAY_NAMES[we.weekday() if we.weekday() < 6 else 5]
            ))

    def validate_unique_float(self):
        """Composite uniqueness on (week_ending, float).

        Frappe's per-field ``unique`` flag can't express a composite key, so we
        enforce it here: reject a second non-cancelled sheet with the same Friday
        and float. Cash and Hauz-Pay on the same Friday are allowed.
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
        """
        if self.docstatus == 1:
            return

        # Vouchers — 18 rows
        existing = {(v.row_idx or 0) for v in self.vouchers}
        for i in range(1, VOUCHER_ROWS + 1):
            if i not in existing:
                self.append("vouchers", {"row_idx": i})

        # Parking — 6 days × 5 vehicles × 2 slots
        existing_parking = {(p.day_idx, p.vehicle, p.slot) for p in self.parking_entries}
        for d_idx, d_name in enumerate(DAY_NAMES):
            for vehicle in VEHICLES:
                for slot in (1, 2):
                    if (d_name, vehicle, slot) not in existing_parking:
                        self.append("parking_entries", {
                            "day_idx": d_name,
                            "vehicle": vehicle,
                            "slot": slot,
                            "amount": 0,
                        })

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
        cat = {c: 0.0 for c in CATEGORY_CODES}
        voucher_out = 0.0
        total_in = 0.0
        for v in self.vouchers:
            amt = v.amount or 0
            if v.cash_in:
                total_in += amt
            else:
                voucher_out += amt
                if v.category in cat:
                    cat[v.category] += amt

        parking_out = sum((p.amount or 0) for p in self.parking_entries)
        misc_out = sum((m.amount or 0) for m in self.misc_entries)
        wages_out = sum((w.amount or 0) for w in self.wages_entries)
        # Only the cash actually issued leaves the float.
        loans_out = sum((l.amount_issued or 0) for l in self.loan_entries)

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
    """Mon–Sat ISO date strings for the week that ends on the given Friday."""
    we = _as_date(week_ending)
    monday = we - timedelta(days=4)
    return [(monday + timedelta(days=i)).isoformat() for i in range(6)]


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
        parking_by_vehicle[p.vehicle] = parking_by_vehicle.get(p.vehicle, 0) + (p.amount or 0)
        parking_by_day[p.day_idx] = parking_by_day.get(p.day_idx, 0) + (p.amount or 0)

    bike_total = sum((m.amount or 0) for m in doc.misc_entries if m.kind == "Bike Fuel")
    forklift_total = sum((m.amount or 0) for m in doc.misc_entries if m.kind == "Forklift")
    wages_total = sum((w.amount or 0) for w in doc.wages_entries)
    loans_total = sum((l.amount_issued or 0) for l in doc.loan_entries)

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
    """Convenience endpoint: create a new sheet for the given Friday + float, or return existing.

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

    # Opening balance: honour an explicit value; otherwise carry forward the prior
    # week's close for this float (negative carried as-is). A carried value of 0
    # only happens when the prior sheet truly closed at 0.
    try:
        explicit = float(opening_balance)
    except (TypeError, ValueError):
        explicit = 0.0
    if explicit:
        doc.opening_balance = explicit
    else:
        carried = doc.carry_forward_opening()
        doc.opening_balance = carried if carried is not None else 0.0

    doc.insert()
    return doc.name
