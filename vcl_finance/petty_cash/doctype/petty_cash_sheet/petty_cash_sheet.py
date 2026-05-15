from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.model.document import Document


VEHICLES = ["KAP 466", "KAY 635", "KCB 430", "KBQ 788", "KBT 972"]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
VOUCHER_ROWS = 18
WAGES_ROWS = 18
BIKE_ROWS = 6
FORKLIFT_ROWS = 4


class PettyCashSheet(Document):
    """Parent weekly petty-cash record. Owns the voucher / parking / misc / wages child tables."""

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def validate(self):
        self.validate_week_ending()
        self.derive_week_no()
        self.ensure_grid()
        self.compute_totals()

    def before_save(self):
        # `closing_balance` mirrors `expected_close` so legacy reports stay aligned.
        self.closing_balance = self.expected_close

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
        we = _as_date(self.week_ending)
        if we.weekday() != 4:  # Friday
            frappe.throw(_("Week Ending must be a Friday. Got {0} ({1}).").format(
                we.isoformat(), DAY_NAMES[we.weekday() if we.weekday() < 6 else 5]
            ))

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

    # ------------------------------------------------------------------
    # Totals — re-computed on every save so the form, list, and print
    # views always see the same numbers.
    # ------------------------------------------------------------------

    def compute_totals(self):
        cat = {"TG": 0.0, "TE": 0.0, "SE": 0.0, "OA": 0.0, "CM": 0.0, "OT": 0.0}
        total_in = 0.0
        for v in self.vouchers:
            cat["TG"] += v.amt_tg or 0
            cat["TE"] += v.amt_te or 0
            cat["SE"] += v.amt_se or 0
            cat["OA"] += v.amt_oa or 0
            cat["CM"] += v.amt_cm or 0
            cat["OT"] += v.amt_ot or 0
            total_in += v.amt_in or 0
        voucher_out = sum(cat.values())

        parking_out = sum((p.amount or 0) for p in self.parking_entries)
        misc_out = sum((m.amount or 0) for m in self.misc_entries)
        wages_out = sum((w.amount or 0) for w in self.wages_entries)

        self.total_out = voucher_out + parking_out + misc_out + wages_out
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
    cat = {"TG": 0.0, "TE": 0.0, "SE": 0.0, "OA": 0.0, "CM": 0.0, "OT": 0.0}
    cat_in = 0.0
    voucher_count = 0
    pc_count = 0
    etr_count = 0
    for v in doc.vouchers:
        cat["TG"] += v.amt_tg or 0
        cat["TE"] += v.amt_te or 0
        cat["SE"] += v.amt_se or 0
        cat["OA"] += v.amt_oa or 0
        cat["CM"] += v.amt_cm or 0
        cat["OT"] += v.amt_ot or 0
        cat_in += v.amt_in or 0
        if v.voucher_no or v.recipient or any([v.amt_tg, v.amt_te, v.amt_se, v.amt_oa, v.amt_cm, v.amt_ot, v.amt_in]):
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
    wages_total = sum((w.amount or 0) for w in doc.wages_entries if w.entry_type == "Wage")
    loans_total = sum((w.amount or 0) for w in doc.wages_entries if w.entry_type == "Loan")

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
def create_for_week(week_ending, custodian_name="Shiro", opening_balance=0, authorised_float=50000):
    """Convenience endpoint: create a new sheet for the given Friday, or return existing.

    Used by the Website Page "New Sheet" form so the custodian doesn't have to
    pick a Naming Series manually.
    """
    we = _as_date(week_ending)
    existing = frappe.db.get_value("Petty Cash Sheet", {"week_ending": we}, "name")
    if existing:
        return existing
    doc = frappe.new_doc("Petty Cash Sheet")
    doc.week_ending = we
    doc.custodian_name = custodian_name or "Shiro"
    doc.opening_balance = float(opening_balance or 0)
    doc.authorised_float = float(authorised_float or 50000)
    doc.status = "Draft"
    doc.insert()
    return doc.name
