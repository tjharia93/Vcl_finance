"""Native lines: a petty cash line that IS its Petty Cash Entry.

On a sheet with ``native_lines`` on, nothing is keyed as a row. Every line is created
here as an entry linked by ``week_sheet`` and the sheet is only the week header.

Three ideas hold this module together:

1. **One mapping, both ways.** A native line is built by making the same row a custodian
   would have typed on the sheet and running it through ``mirror._entry_values`` — the
   function that has turned sheet rows into entries since phase 1. So a native entry has
   exactly the source type, key, category and payload a mirrored one would, and routes
   through the Posting Map identically. ``rows_from_entries`` is that mapping reversed:
   it rebuilds the rows from the entries so everything that READS a sheet's rows (the
   feed, the summary, the print, the Excel report) works on a native week unchanged,
   after one call to ``hydrate``.

2. **Lock first.** Every change to a line takes a row lock on its week sheet BEFORE it
   checks the week is open, and holds it until the transaction ends. Close, approve and
   reopen take the same lock. An edit and a close can therefore never interleave: the
   second one to take the lock sees what the first did. A move locks both sheets in
   name order so two moves cannot deadlock.

3. **Identity survives.** Cancelling, reinstating, attaching a receipt and moving a line
   between weeks all act on the SAME entry. Even a move between a native week and a week
   that still keeps rows re-links the one entry rather than voiding it and making
   another, so its receipt, sign-off history, VAT and comments stay attached.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

SHEET = "Petty Cash Sheet"
ENTRY = "Petty Cash Entry"

# entry_kind -> the child table that kind would have been keyed into.
KIND_TABLE = {
    "voucher": "vouchers",
    "wage": "wages_entries", "commission": "wages_entries",
    "piecework": "wages_entries", "overtime": "wages_entries",
    "loan": "loan_entries",
    "bike": "misc_entries", "forklift": "misc_entries",
    "parking": "parking_entries",
}
WAGE_TYPE = {"wage": "Wage", "commission": "Commission", "piecework": "Piecework", "overtime": "Overtime"}

# What kind of change is this? Every reviewed field belongs to exactly one group, and the
# change rules below are written per group. A change to any of them moves the week's
# revision: a supplier, an account or a receipt can change without moving a count or a
# total, which is exactly why close and approve compare the revision, not the totals.
GROUPS = {
    "content": ("amount", "txn_date", "entry_kind", "source_type", "source_key", "category",
                "recipient", "cash_in", "notes", "payload"),
    "receipt": ("receipt", "receipt_etr"),
    "refs": ("voucher_no", "supplier_ref", "etr_cu_invoice_no", "pc_received", "etr_received"),
    "vat": ("vat_treatment", "vat_amount", "net_amount"),
    "coding": ("posting_account", "qbo_account", "exclude_from_posting"),
    "company": ("company",),
    "sign": ("status",),
    "tick": ("reconciled",),
    "void": ("cancelled",),
    "move": ("week_sheet",),
}
REVIEWED_FIELDS = tuple(f for fields in GROUPS.values() for f in fields)
# Changes that move cash, so they move the expected close and the variance.
CASH_FIELDS = ("amount", "cash_in", "cancelled", "week_sheet")
CASH_GROUPS = {"content_cash", "void", "move", "new"}
# Finance may keep its tick on these if it confirms the revised line in the same action.
CONFIRMABLE = {"refs", "vat", "coding"}
LABELS = {
    "amount": "Amount", "txn_date": "Date", "entry_kind": "Kind", "category": "Category",
    "recipient": "Recipient", "cash_in": "Cash in", "voucher_no": "Slip no.",
    "supplier_ref": "Supplier / receipt ref", "etr_cu_invoice_no": "ETR invoice no.",
    "pc_received": "PC slip", "etr_received": "ETR slip", "vat_treatment": "VAT treatment",
    "vat_amount": "VAT amount", "net_amount": "Net amount", "posting_account": "ERPNext account",
    "qbo_account": "QuickBooks account", "exclude_from_posting": "Excluded from posting",
    "company": "Company", "receipt": "Receipt photo", "receipt_etr": "ETR photo",
    "status": "Sign-off", "cancelled": "Voided",
}


class Row(frappe._dict):
    """A sheet row rebuilt from an entry. Attribute and ``.get`` access, like a child doc."""


# ----------------------------------------------------------------------
# Locking and the stored totals
# ----------------------------------------------------------------------

def lock_sheet(name):
    """Take the row lock on a week sheet and return its current state.

    Held until the transaction commits or rolls back. Everything that changes a line,
    and everything that closes, approves or reopens a week, calls this FIRST.
    """
    try:
        rows = frappe.db.sql(
            "select name, status, docstatus, native_lines, week_ending, `float`, company, lines_revision "
            "from `tabPetty Cash Sheet` where name = %s for update", (name,), as_dict=True)
    except Exception as e:      # noqa: BLE001
        if (isinstance(e, (frappe.QueryDeadlockError, frappe.QueryTimeoutError))
                or frappe.db.is_deadlocked(e) or frappe.db.is_timedout(e)):
            # Someone else changed this week while we waited for it. Said in words a
            # person can act on; ``retrying`` recognises the type and runs the action
            # again, so through the normal actions nobody ever sees this.
            raise frappe.QueryDeadlockError(
                _("Someone else changed this week at the same moment. Reload it and try again.")) from e
        raise
    if not rows:
        frappe.throw(_("Week sheet {0} does not exist.").format(name))
    return rows[0]


def lock_sheets(*names):
    """Lock several sheets in name order (a fixed order, so two moves cannot deadlock)."""
    return {n: lock_sheet(n) for n in sorted({n for n in names if n})}


def assert_week_open(sheet, for_finance=None):
    """Refuse a change to a line on a week that is no longer open.

    Approved or Submitted: closed to everyone until it is reopened.
    Closed: the custodian's cash count is in. Only Finance (an Accounts Manager) may
    still change a line — that is when Finance reviews it.
    """
    from vcl_finance.petty_cash.api import _is_accounts_manager
    if sheet["docstatus"] != 0 or sheet["status"] in ("Submitted", "Approved"):
        frappe.throw(
            _("The week ending {0} is approved. Reopen it before changing a line.").format(
                frappe.format_value(sheet["week_ending"], {"fieldtype": "Date"})),
            title=_("Week is approved"))
    if sheet["status"] == "Closed":
        is_finance = _is_accounts_manager() if for_finance is None else for_finance
        if not is_finance:
            frappe.throw(
                _("The week ending {0} is closed. Please speak to Finance to have this "
                  "entry posted.").format(frappe.format_value(sheet["week_ending"], {"fieldtype": "Date"})),
                frappe.PermissionError)


def retrying(fn):
    """Run a whole action again if another session got in first.

    Two people acting on the same week at the same moment are made to take turns by the
    week lock. The one who waited may then find the database moved under its feet (a
    deadlock, a lock wait timeout, or on newer MariaDB "record has changed since last
    read"). That is not an error the user caused: the action is rolled back and run
    again from the top, so it is judged on the week as it NOW is — and is then either
    done or refused for a reason a person can read.

    Only for actions that are the whole of their request, so rolling back loses nothing.
    """
    import functools

    @functools.wraps(fn)
    def run(*args, **kwargs):
        for attempt in range(4):
            try:
                return fn(*args, **kwargs)
            except Exception as e:      # noqa: BLE001
                conflict = isinstance(e, (frappe.QueryDeadlockError, frappe.QueryTimeoutError)) or (
                    frappe.db.is_deadlocked(e) or frappe.db.is_timedout(e))
                if not conflict or attempt == 3:
                    raise
                frappe.db.rollback()
                frappe.local.message_log = []
    return run


def lock_first(sheet=None, entry=None, week_ending=None, float_name=None):
    """Take the week lock using LOCKING reads only, as the first statements of a transaction.

    Why this exists. Under REPEATABLE READ (what the live database runs) a transaction
    sees the data as it was at its FIRST ordinary read, for as long as it lasts. A request
    that reads anything and only then waits for the week lock will, once it gets the lock,
    still be looking at the week as it was BEFORE it waited. It cannot see the journal, the
    line or the staging row that the request ahead of it has just committed, and so it
    makes a second one. Found by running three posting requests at once with the
    database set as live has it: three pairs of draft journals instead of one.

    A locking read does not start that fixed view, and always sees what is committed now.
    So the lock is taken first, with locking reads only, and every ordinary read the
    action then makes sees the week as the previous holder of the lock left it.
    """
    names = []
    if entry:
        row = frappe.db.sql("select week_sheet, origin_sheet from `tabPetty Cash Entry` where name = %s for update", (entry,))
        if row:
            names += [x for x in row[0] if x]
    if sheet:
        names.append(sheet)
    if week_ending and float_name:
        rows = frappe.db.sql("select name from `tabPetty Cash Sheet` where week_ending = %s and `float` = %s and docstatus < 2 for update",
                             (week_ending, float_name))
        names += [r[0] for r in rows]
    for name in sorted(set(names)):
        frappe.db.sql("select name from `tabPetty Cash Sheet` where name = %s for update", (name,))


def exclusive_call(fn, args, kwargs):
    """Run a whole action in a FRESH transaction that takes the week lock first, and run it
    again if another session got in first.

    The request's transaction so far has only read (who is logged in, permissions), so it
    is ended; the week lock is then the first thing the new transaction does
    (``lock_first``), and the action runs. ``flags.pc_keep_transaction`` leaves the current
    transaction alone: for a caller that is already inside one of its own, such as a test
    that rolls everything back.
    """
    import inspect
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs).arguments
    except TypeError:
        bound = dict(kwargs)
    extra = bound.get("kwargs") or {}
    where = {"sheet": bound.get("sheet"), "entry": bound.get("entry"), "week_ending": bound.get("week_ending"),
             "float_name": bound.get("float_name") or extra.get("float") or kwargs.get("float")}

    def attempt():
        if not frappe.flags.get("pc_keep_transaction"):
            frappe.db.commit()
            lock_first(**where)
        return fn(*args, **kwargs)
    return retrying(attempt)()


def totals(sheet_name, opening_balance=0, cash_count_end=0):
    """Totals for a native week, summed from its live entries, read under lock."""
    from vcl_finance.petty_cash import weeks
    out = into = 0.0
    for e in weeks.live_amounts_locked(sheet_name):
        if e["cash_in"]:
            into += flt(e["amount"])
        else:
            out += flt(e["amount"])
    expected = flt(opening_balance) - out + into
    return {"total_out": out, "total_in": into, "expected_close": expected,
            "closing_balance": expected, "variance": flt(cash_count_end) - expected}


def refresh_sheet(sheet_name, bump=True, cash_changed=False, because=None):
    """Recompute a native sheet's stored totals from its entries and move its revision.

    Called with the sheet lock already held. Writes the columns directly: saving the
    whole sheet document here would run its validation, the mirror hook and the review
    email for what is only a refreshed copy of numbers the entries already hold.
    """
    head = frappe.db.get_value(
        SHEET, sheet_name,
        ["opening_balance", "cash_count_end", "native_lines", "status", "expected_close", "variance"], as_dict=True)
    if not head or not cint(head.native_lines):
        return None
    values = totals(sheet_name, head.opening_balance, head.cash_count_end)
    frappe.db.set_value(SHEET, sheet_name, values, update_modified=False)
    cash = refresh_cash(sheet_name)
    if cash_changed:
        refresh_previous_week_cash(sheet_name)
    if cash_changed and head.status == "Closed" and flt(values["expected_close"], 2) != flt(head.expected_close, 2):
        # The week was closed against a supplied balance. A line that moves cash has
        # changed since, so that reconciliation no longer stands. The count itself is
        # history and is left exactly as it was entered; what changes is the variance,
        # and Finance must accept the new one before the week can be approved.
        # ...unless the week's discrepancy is now exactly nil, in which case there is no
        # exception left to acknowledge.
        still = bool(flt(cash["cash_discrepancy"], 2)) if cash else True
        frappe.db.set_value(SHEET, sheet_name, {
            "cash_reconfirm_needed": 1 if still else 0, "cash_confirmed_by": None, "cash_confirmed_on": None,
            "cash_confirm_reason": None, "cash_confirm_evidence": None},
            update_modified=False)
        fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})
        frappe.get_doc(SHEET, sheet_name).add_comment("Info", _(
            "Cash reconciliation needs confirming. {0} changed after the count of {1}: expected close "
            "{2} -> {3}, variance {4} -> {5}.").format(
                because.name if because is not None else _("A line"), fmt(head.cash_count_end),
                fmt(head.expected_close), fmt(values["expected_close"]),
                fmt(head.variance), fmt(values["variance"])))
    if bump:
        frappe.db.sql("update `tabPetty Cash Sheet` set lines_revision = ifnull(lines_revision, 0) + 1 "
                      "where name = %s", (sheet_name,))
    return values


# ----------------------------------------------------------------------
# Building a line
# ----------------------------------------------------------------------

def build_row(kind, fields, txn_date):
    """The row a custodian would have keyed for this kind → ``(table, row)``.

    Carries the same checks ``api.quick_entry`` makes on the row path, so a line is
    accepted or refused for the same reasons whichever way the week keeps its lines.
    """
    from vcl_finance.petty_cash.api import _truthy
    from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import CATEGORY_CODES, VEHICLES

    kind = (kind or "").lower()
    amount = flt(fields.get("amount"))
    table = KIND_TABLE.get(kind)
    if not table:
        frappe.throw(_("Unknown entry kind '{0}'.").format(kind))

    if kind == "voucher":
        cash_in = _truthy(fields.get("cash_in"))
        cat = (fields.get("category") or "").upper()
        if not cash_in and cat not in CATEGORY_CODES:
            frappe.throw(_("Pick a spend category (or tick Cash-in)."))
        if not cash_in and not (_truthy(fields.get("pc_received")) or _truthy(fields.get("etr_received"))):
            frappe.throw(_("Tick PC or ETR — a spend voucher needs a receipt slip recorded."))
        row = {
            "txn_date": txn_date, "voucher_no": fields.get("voucher_no") or "",
            "recipient": fields.get("recipient") or "", "notes": fields.get("notes") or "",
            "pc_received": 1 if _truthy(fields.get("pc_received")) else 0,
            "etr_received": 1 if _truthy(fields.get("etr_received")) else 0,
            "cash_in": 1 if cash_in else 0, "category": "" if cash_in else cat, "amount": amount,
        }
    elif kind in WAGE_TYPE:
        row = {
            "txn_date": txn_date, "entry_type": WAGE_TYPE[kind],
            "recipient": fields.get("recipient") or "", "staff_id": fields.get("staff_id") or "",
            "reason": fields.get("reason") or "", "amount": amount,
            "paye": 1 if _truthy(fields.get("paye")) else 0,
            # The paper-signature ticks a wages row always carries (0 until ticked).
            "recipient_signed": 0, "authorised_signed": 0,
        }
    elif kind == "loan":
        row = {
            "txn_date": txn_date, "recipient": fields.get("recipient") or "",
            "staff_id": fields.get("staff_id") or "", "reason": fields.get("reason") or "",
            "amount_issued": amount, "amount_signed": amount,
            "paye": 1 if _truthy(fields.get("paye")) else 0,
        }
    elif kind in ("bike", "forklift"):
        row = {"kind": "Bike Fuel" if kind == "bike" else "Forklift", "txn_date": txn_date,
               "amount": amount, "notes": fields.get("notes") or "", "recipient_signed": 0}
    else:   # parking
        vehicle = fields.get("vehicle")
        if vehicle not in VEHICLES:
            frappe.throw(_("Pick a vehicle for the parking entry."))
        if not txn_date:
            frappe.throw(_("Pick a date for the parking entry."))
        row = {"vehicle": vehicle, "amount": amount, "slot": 1, "txn_date": txn_date}
    return table, Row(row)


def _entry_values_for(sheet, table, row, kind):
    """``mirror._entry_values`` for the row, re-linked as a native entry."""
    from vcl_finance.petty_cash.mirror import _entry_values
    values = _entry_values(sheet, table, row)
    for origin in ("origin_row", "origin_sheet", "origin_week_ending", "origin_hash",
                   "locked", "locked_by", "locked_on"):
        values.pop(origin, None)
    values.update({
        "week_sheet": sheet.name, "sync_state": "Native", "entry_kind": kind,
        "voucher_no": row.get("voucher_no") or None,
    })
    return values


def create_line(sheet_doc, kind, fields, txn_date):
    """Create one native line on ``sheet_doc``. Returns the saved entry."""
    kind = (kind or "").lower()
    sheet = lock_sheet(sheet_doc.name)
    assert_week_open(sheet)
    table, row = build_row(kind, fields, txn_date)
    doc = frappe.get_doc(_entry_values_for(sheet_doc, table, row, kind))
    doc.flags.sheet_locked = True
    doc.flags.ignore_permissions = True      # permlevel-1 fields; the sheet permission was checked
    doc.insert(ignore_permissions=True)
    return doc


def edit_line(sheet_name, entry_id, fields):
    """Change what was keyed on a native line (the entry page's Edit).

    ``fields`` are the same names ``quick_entry`` takes; anything not sent keeps its
    current value. The line is rebuilt through the same row-to-entry mapping as when
    it was keyed, so an edited line is exactly what a freshly keyed one would be. A date
    in another week moves the line there first (``move_line``).
    """
    from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import week_saturday

    lock_sheet(sheet_name)
    doc = get_line(sheet_name, entry_id)
    kind = doc.entry_kind or "voucher"
    table, row = _row_from_entry({f: doc.get(f) for f in ROW_FIELDS if f != "creation"})
    current = dict(row)
    if table == "loan_entries":
        current["amount"] = current.get("amount_issued")
    merged = {**current, **{k: v for k, v in (fields or {}).items() if v is not None}}
    txn_date = getdate(merged.get("txn_date")) if merged.get("txn_date") else None
    if not txn_date:
        frappe.throw(_("A date is required for every entry."))

    sheet_week = frappe.db.get_value(SHEET, sheet_name, "week_ending")
    if week_saturday(txn_date) != week_saturday(getdate(sheet_week)):
        move_line(doc.name, txn_date)
        doc.reload()

    sheet_doc = frappe.get_doc(SHEET, doc.week_sheet)
    table, new_row = build_row(kind, merged, txn_date)
    values = _entry_values_for(sheet_doc, table, new_row, kind)
    for f in GROUPS["content"] + ("voucher_no", "pc_received", "etr_received"):
        if f in values:
            doc.set(f, values[f])
    doc.save(ignore_permissions=True)
    return doc


def get_line(sheet_name, entry_id):
    """The native entry ``entry_id`` of this sheet, or a refusal."""
    if not frappe.db.exists(ENTRY, entry_id):
        frappe.throw(_("That entry no longer exists on this sheet."))
    doc = frappe.get_doc(ENTRY, entry_id)
    if doc.week_sheet != sheet_name:
        frappe.throw(_("That entry no longer exists on this sheet."))
    return doc


def cancel_line(sheet_name, entry_id, remark=None):
    """Void a native line. It stays on record and stops counting."""
    lock_sheet(sheet_name)
    doc = get_line(sheet_name, entry_id)
    doc.cancelled = 1
    doc.cancel_remark = (remark or "").strip() or None
    doc.status = "Void"
    doc.flags.sheet_locked = True
    doc.save(ignore_permissions=True)
    return doc


def reinstate_line(sheet_name, entry_id):
    """Reverse a mistaken void. The line counts again and needs signing again."""
    lock_sheet(sheet_name)
    doc = get_line(sheet_name, entry_id)
    doc.cancelled = 0
    doc.cancelled_on = None
    doc.cancelled_by = None
    doc.cancel_remark = None
    if doc.status == "Void":
        # A reinstated line is a line nobody has signed in its current state.
        doc.status = "Unapproved"
        doc.approved_by = None
        doc.approved_on = None
    doc.flags.sheet_locked = True
    doc.save(ignore_permissions=True)
    return doc


def attach_receipt_line(sheet_name, entry_id, file_url):
    """Set the receipt photo on a native line."""
    lock_sheet(sheet_name)
    doc = get_line(sheet_name, entry_id)
    doc.receipt = file_url
    doc.flags.sheet_locked = True
    doc.save(ignore_permissions=True)
    return doc


def _assert_not_posted(doc):
    if doc.journal_entry or doc.qbo_queue:
        frappe.throw(
            _("This line is on journal {0}. It cannot be changed here.").format(
                doc.journal_entry or doc.qbo_queue),
            title=_("Line is on a journal"))


# ----------------------------------------------------------------------
# Rows rebuilt from entries (so every row reader works on a native week)
# ----------------------------------------------------------------------

ROW_FIELDS = ["name", "txn_date", "amount", "cash_in", "category", "source_type", "source_key",
              "recipient", "notes", "receipt", "pc_received", "etr_received", "cancelled",
              "cancelled_on", "cancel_remark", "status", "approved_by", "approved_on",
              "reconciled", "reconciled_by", "reconciled_on", "entry_kind", "voucher_no",
              "payload", "creation"]


def _row_from_entry(e):
    """One entry → ``(table, Row)``: ``mirror._entry_values`` run backwards."""
    kind = e.get("entry_kind") or "voucher"
    table = KIND_TABLE.get(kind, "vouchers")
    try:
        payload = json.loads(e.get("payload") or "{}")
    except ValueError:
        payload = {}
    signed = e.get("status") == "Approved"
    row = Row({
        "name": e["name"], "txn_date": e.get("txn_date"), "receipt": e.get("receipt"),
        "cancelled": cint(e.get("cancelled")), "cancelled_on": e.get("cancelled_on"),
        "cancel_remark": e.get("cancel_remark"),
        "locked": 1 if signed else 0,
        "locked_by": e.get("approved_by") if signed else None,
        "locked_on": e.get("approved_on") if signed else None,
        "reconciled": cint(e.get("reconciled")), "reconciled_by": e.get("reconciled_by"),
        "reconciled_on": e.get("reconciled_on"),
        "row_idx": payload.get("row_idx"), "entry_status": e.get("status"),
    })
    amount = flt(e.get("amount"))
    if table == "vouchers":
        row.update({"voucher_no": e.get("voucher_no") or payload.get("voucher_no") or "",
                    "recipient": e.get("recipient"), "category": e.get("category") or "",
                    "amount": amount, "cash_in": cint(e.get("cash_in")), "notes": e.get("notes"),
                    "pc_received": cint(e.get("pc_received")), "etr_received": cint(e.get("etr_received"))})
    elif table == "wages_entries":
        row.update({"entry_type": e.get("source_key") or WAGE_TYPE.get(kind, "Wage"),
                    "recipient": e.get("recipient"), "staff_id": payload.get("staff_id"),
                    "reason": payload.get("reason"), "amount": amount, "paye": cint(payload.get("paye")),
                    "recipient_signed": cint(payload.get("recipient_signed")),
                    "authorised_signed": cint(payload.get("authorised_signed"))})
    elif table == "loan_entries":
        row.update({"recipient": e.get("recipient"), "staff_id": payload.get("staff_id"),
                    "reason": payload.get("reason"), "amount_issued": amount,
                    "amount_signed": flt(payload.get("amount_signed")), "paye": cint(payload.get("paye"))})
    elif table == "misc_entries":
        row.update({"kind": e.get("source_type") or ("Bike Fuel" if kind == "bike" else "Forklift"),
                    "amount": amount, "notes": e.get("notes"),
                    "recipient_signed": cint(payload.get("recipient_signed"))})
    else:
        from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import DAY_NAMES
        d = getdate(e["txn_date"]) if e.get("txn_date") else None
        row.update({"vehicle": e.get("source_key"), "slot": payload.get("slot") or 1, "amount": amount,
                    # Sun-first names, exactly as the sheet derives them from the date.
                    "day_idx": DAY_NAMES[(d.weekday() + 1) % 7] if d else payload.get("day_idx")})
    return table, row


def rows_from_entries(sheet_name):
    """Every native line of a sheet as rows, grouped by the table it would sit in."""
    from vcl_finance.petty_cash import weeks
    out = {t: [] for t in ("vouchers", "wages_entries", "loan_entries", "misc_entries", "parking_entries")}
    for e in weeks.week_entries(sheet_name, fields=ROW_FIELDS):
        table, row = _row_from_entry(e)
        out[table].append(row)
    return out


# What a client may NOT change on a row of a native week by saving the sheet. If any of
# these differs from what the entry says, the save is refused: the row is a view of the
# entry, and the entry is changed through its own actions.
ROW_COMPARE = {
    "vouchers": ("txn_date", "voucher_no", "recipient", "category", "amount", "cash_in", "pc_received",
                 "etr_received", "receipt", "notes", "cancelled", "locked", "reconciled"),
    "wages_entries": ("txn_date", "entry_type", "recipient", "staff_id", "reason", "amount", "paye",
                      "receipt", "cancelled", "locked", "reconciled"),
    "loan_entries": ("txn_date", "recipient", "staff_id", "reason", "amount_issued", "receipt",
                     "cancelled", "locked", "reconciled"),
    "misc_entries": ("kind", "txn_date", "amount", "notes", "receipt", "cancelled", "locked", "reconciled"),
    "parking_entries": ("txn_date", "vehicle", "amount", "receipt", "cancelled", "locked", "reconciled"),
}
MANAGE_MSG = "Manage transactions from the entry page."


def hydrate(doc):
    """Fill a native sheet document's row tables, IN MEMORY, from its entries.

    The rows are real child documents (so the desk form, a print format, a REST read and
    every report see a native week exactly as they see any other) but they exist only in
    memory: each carries the NAME of the entry it shows, and ``strip_rows`` takes them
    out again before the sheet is written. Nothing is ever stored in the row tables of a
    native week.
    """
    if not cint(doc.get("native_lines")) or doc.flags.get("hydrated") or doc.is_new():
        return doc
    shown = set()
    for table, rows in rows_from_entries(doc.name).items():
        doc.set(table, [])
        # Number the rows the way a keyed sheet numbers them: in date order, from 1 (bike
        # and forklift each in their own run), so the print and the reports lay a native
        # week out in the same slots.
        counters = {}
        rows.sort(key=lambda r: (str(r.get("txn_date") or "9999"), str(r.get("name"))))
        for row in rows:
            row.pop("entry_status", None)
            if table != "parking_entries":
                run = row.get("kind") if table == "misc_entries" else table
                counters[run] = counters.get(run, 0) + 1
                row["row_idx"] = counters[run]
            doc.append(table, dict(row))
            shown.add(row["name"])
    if doc.docstatus == 0:
        # ...and pad with the same blank slots a keyed sheet carries, in memory only.
        doc.ensure_grid()
        # Bike slots then forklift slots, each in slot order, as on a keyed sheet.
        doc.misc_entries.sort(key=lambda m: ((m.kind or "") != "Bike Fuel", cint(m.row_idx)))
        for i, m in enumerate(doc.misc_entries, start=1):
            m.idx = i
    # A stored row always has its numbers (0, not empty). Give the in-memory ones the same,
    # so a print format that adds amounts up never meets an empty cell.
    for table in ROW_COMPARE:
        for row in doc.get(table):
            for df in row.meta.fields:
                if df.fieldtype in ("Currency", "Float", "Int", "Check") and row.get(df.fieldname) is None:
                    row.set(df.fieldname, 0)
    doc.flags.hydrated = True
    doc.flags.hydrated_names = shown      # the lines this copy of the sheet was shown
    return doc


def strip_rows(doc):
    """Take the in-memory rows of a native week out again before the sheet is saved.

    A row whose name is one of this week's entries is the view of that entry. If the
    client sent it back unchanged it is simply dropped. If the client changed it, or
    left one out (a delete), the save is refused: transactions on this week are managed
    from the entry page. A row that is NOT one of the entries is left for
    ``guard_native_switch`` to judge (blank scaffolding is harmless, a real row is
    refused).
    """
    if not cint(doc.get("native_lines")) or doc.is_new():
        return
    expected = {}
    for table, rows in rows_from_entries(doc.name).items():
        for row in rows:
            expected[row["name"]] = (table, row)
    seen = set()
    for table, fields in ROW_COMPARE.items():
        keep = []
        for row in (doc.get(table) or []):
            name = row.get("name")
            if name not in expected:
                # Not one of the entries. A blank slot is in-memory padding and is dropped;
                # a real row is kept for ``guard_native_switch`` to refuse.
                from vcl_finance.petty_cash.mirror import TABLE_MAP, is_real_row
                if is_real_row(row, TABLE_MAP[table]["amount_field"]):
                    keep.append(row)
                continue
            seen.add(name)
            want = expected[name][1]
            changed = [f for f in fields if _norm(row.get(f)) != _norm(want.get(f))]
            if changed:
                frappe.throw(
                    _("{0} This week keeps its lines as entries, so {1} cannot be changed on the sheet.")
                    .format(_(MANAGE_MSG), name), title=_("Read-only on the sheet"))
        doc.set(table, keep)
    # A line this copy of the sheet was SHOWN and has not sent back is a delete. (A line
    # keyed since this copy was loaded is simply one it never saw — not a delete.)
    if (doc.flags.get("hydrated_names") or set()) & (set(expected) - seen):
        frappe.throw(
            _("{0} A line of this week cannot be removed by saving the sheet.").format(_(MANAGE_MSG)),
            title=_("Read-only on the sheet"))
    doc.flags.hydrated = False
    doc.flags.hydrated_names = set()


# ----------------------------------------------------------------------
# Moving a line to another week (the only way a line changes week)
# ----------------------------------------------------------------------

def _assert_movable(doc):
    if doc.status == "Approved":
        frappe.throw(_("This line is signed off. Withdraw the sign-off before moving it to another week."),
                     title=_("Line is signed"))
    if cint(doc.get("reconciled")):
        frappe.throw(_("Finance has ticked this line. Untick it before moving it."), title=_("Line is ticked"))
    if doc.cancelled:
        frappe.throw(_("A voided line is not moved. Reinstate it first."), title=_("Line is voided"))
    _assert_not_posted(doc)


def move_line(entry, txn_date):
    """Move a line to the week that contains ``txn_date``, keeping it the same entry.

    Same week: the date is simply corrected. Another week: the line is re-linked to that
    week's sheet (found or created the way ``quick_entry`` does). If that week keeps rows
    the entry becomes the mirrored copy of a new row there; if this week kept rows and
    that one is native, the row is removed and the entry adopted as a native line.

    Both sheets are locked in name order and both have their totals refreshed, in one
    transaction.
    """
    from vcl_finance.petty_cash import api
    from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import week_saturday

    if not txn_date:
        frappe.throw(_("A date is required."))
    txn_date = getdate(txn_date)
    doc = frappe.get_doc(ENTRY, entry)
    source_name = doc.week_sheet or doc.origin_sheet
    if not source_name:
        frappe.throw(_("This entry is not linked to a week, so it cannot be moved."))
    source = frappe.get_doc(SHEET, source_name)
    target, rerouted = api._route_to_week(source, txn_date)

    locked = lock_sheets(source.name, target.name)
    assert_week_open(locked[source.name])
    assert_week_open(locked[target.name])
    doc.reload()
    _assert_movable(doc)

    src_native, dst_native = bool(doc.week_sheet), bool(cint(locked[target.name]["native_lines"]))

    if not rerouted and week_saturday(txn_date) == week_saturday(getdate(source.week_ending)):
        if src_native:
            doc.txn_date = txn_date
            doc.flags.sheet_locked = True
            doc.save(ignore_permissions=True)
        else:
            _set_row_date(source, doc, txn_date)
        return {"entry": doc.name, "sheet": source.name, "moved": False, "txn_date": str(txn_date)}

    if src_native and dst_native:
        doc.week_sheet = target.name
        doc.txn_date = txn_date
        doc.flags.controlled_move = True
        doc.flags.sheet_locked = True
        doc.save(ignore_permissions=True)
        refresh_sheet(source.name)
    elif src_native and not dst_native:
        entry_to_row(doc, target, txn_date=txn_date)
        refresh_sheet(source.name)
    elif not src_native and dst_native:
        row_to_entry(doc, source, target, txn_date=txn_date)
    else:
        frappe.throw(_("Both weeks keep their lines as rows. Change the date on the row itself."),
                     title=_("Use the sheet"))
    return {"entry": doc.name, "from_sheet": source.name, "sheet": target.name, "moved": True,
            "txn_date": str(txn_date), "target_keeps": "entries" if dst_native else "rows"}


def _set_row_date(sheet_doc, entry_doc, txn_date):
    for table in KIND_TABLE.values():
        for row in (sheet_doc.get(table) or []):
            if row.name == entry_doc.origin_row:
                row.txn_date = txn_date
                sheet_doc.save()
                return
    frappe.throw(_("The row behind this entry was not found on {0}.").format(sheet_doc.name))


def entry_to_row(doc, target_sheet, txn_date=None):
    """Turn a native entry into the mirrored copy of a NEW row on ``target_sheet``.

    The row is written from the entry (``_row_from_entry``), the sheet is saved with the
    mirror held off, the entry is re-linked to the row it now mirrors, and only then is
    the mirror run — which finds the entry already in step and changes nothing. So the
    entry keeps its name, receipt, sign-off, tick, VAT, accounts and journal link.

    Used by a move into a week that keeps rows, and by the fallback tool that converts a
    whole native week back.
    """
    from vcl_finance.petty_cash.mirror import TABLE_MAP, mirror_sheet, row_hash

    values = {f: doc.get(f) for f in ROW_FIELDS if f != "creation"}
    if txn_date:
        values["txn_date"] = txn_date
    table, row = _row_from_entry(values)
    payload = {k: v for k, v in row.items()
               if k not in ("name", "entry_status") and v is not None}
    if not payload.get("row_idx") and table != "parking_entries":
        payload["row_idx"] = max([cint(r.row_idx) for r in (target_sheet.get(table) or [])] + [0]) + 1
    new_row = target_sheet.append(table, payload)
    target_sheet.flags.skip_mirror = True
    target_sheet.save(ignore_permissions=True)

    spec = TABLE_MAP[table]
    frappe.db.set_value(ENTRY, doc.name, {
        "week_sheet": None, "origin_doctype": spec["doctype"], "origin_row": new_row.name,
        "origin_sheet": target_sheet.name, "origin_week_ending": target_sheet.week_ending,
        "origin_hash": row_hash(new_row, spec["amount_field"]), "sync_state": "Mirrored",
        "txn_date": new_row.txn_date, "float": target_sheet.float,
    }, update_modified=False)
    target_sheet.flags.skip_mirror = False
    stats = mirror_sheet(target_sheet, raise_on_error=True)
    doc.reload()
    return {"row": new_row.name, "table": table, "mirror": stats}


def row_to_entry(doc, source_sheet, target_sheet, txn_date=None):
    """Adopt a mirrored entry as a native line of ``target_sheet`` and drop its row.

    The entry is re-linked FIRST, so when the source sheet is saved without the row the
    mirror no longer sees an entry of its own to mark Orphaned.
    """
    table = None
    for t in KIND_TABLE.values():
        for row in (source_sheet.get(t) or []):
            if row.name == doc.origin_row:
                table, found = t, row
    if table is None:
        frappe.throw(_("The row behind this entry was not found on {0}.").format(source_sheet.name))

    if cint(found.get("reconciled")):
        frappe.throw(_("Finance has ticked this line. Untick it before moving it."), title=_("Line is ticked"))
    kind = doc.entry_kind or _kind_for(table, found)
    doc.update({
        "week_sheet": target_sheet.name, "origin_sheet": None, "origin_row": doc.name,
        "origin_doctype": doc.origin_doctype, "origin_week_ending": None, "origin_hash": None,
        "sync_state": "Native", "entry_kind": kind, "txn_date": txn_date or doc.txn_date,
        "voucher_no": found.get("voucher_no") or doc.voucher_no,
    })
    doc.flags.controlled_move = True
    doc.flags.sheet_locked = True
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)

    source_sheet.get(table).remove(found)
    source_sheet.save(ignore_permissions=True)
    return {"table": table, "kind": kind}


def _kind_for(table, row):
    if table == "vouchers":
        return "voucher"
    if table == "wages_entries":
        return {v: k for k, v in WAGE_TYPE.items()}.get(row.get("entry_type") or "Wage", "wage")
    if table == "loan_entries":
        return "loan"
    if table == "misc_entries":
        return "bike" if (row.get("kind") or "") == "Bike Fuel" else "forklift"
    return "parking"


# ----------------------------------------------------------------------
# The change rules: who may change what, on which line, in which week
# ----------------------------------------------------------------------

def _norm(v):
    if v in (None, "", 0, 0.0, "0"):
        return None
    if isinstance(v, float):
        return round(v, 6)
    return str(v) if not isinstance(v, (int, float)) else v


def _differs(doc, before, field):
    return _norm(doc.get(field)) != _norm(before.get(field) if before is not None else None)


def changed_groups(doc, before):
    """The kinds of change in this save. ``new`` for a line being created."""
    if before is None:
        return {"new"}
    groups = {g for g, fields in GROUPS.items() if any(_differs(doc, before, f) for f in fields)}
    if "content" in groups and any(_differs(doc, before, f) for f in ("amount", "cash_in")):
        groups.add("content_cash")
    # Voiding and reinstating also move ``status`` (to and from Void). That is part of
    # the void, not a sign-off decision.
    if "void" in groups and "sign" in groups and "Void" in (doc.status, before.get("status")):
        groups.discard("sign")
    return groups


def _journal_state(before):
    """None, 'draft' or 'submitted' for the journal this line is on."""
    if before is None or not before.get("journal_entry"):
        return None
    ds = frappe.db.get_value("Journal Entry", before.get("journal_entry"), "docstatus")
    return {0: "draft", 1: "submitted"}.get(ds)


def _who():
    from vcl_finance.petty_cash.api import PETTY_APPROVERS, PETTY_PRIV
    roles = set(frappe.get_roles())
    # finance: Accounts Manager — closes and approves weeks, ticks lines.
    # signer:  whoever may sign a line and code it (Accounts Manager, or the purpose-built
    #          Petty Cash Approver role).
    return bool(roles & PETTY_PRIV), bool(roles & PETTY_APPROVERS)


def enforce_change_rules(doc, before, groups, locked):
    """Allow or refuse this change to a native line, and say what it does to the tick.

    Decided from four things: who is asking, what changed (``groups``), the state of the
    line as it was (signed? ticked? on a journal?), and the state of its week.

      Open      the custodian records lines; Finance can review.
      Closed    the cash count is in. The custodian can no longer change lines; Finance
                signs, ticks and codes them under these rules.
      Approved  frozen. Reopen the week first.

    The finance tick means "Finance has checked the CURRENT version of this line". Any
    change to a ticked line clears it, except a slip/receipt number, VAT or account
    change that Finance confirms in the same action (``flags.confirm_tick``), which
    re-stamps the tick with the new reviewer and time.
    """
    finance, signer = _who()
    notes = []

    # ---- on a journal: two states, two different ways back, no generic unpost ----
    state = _journal_state(before)
    if state == "submitted":
        frappe.throw(
            _("This line is on submitted journal {0}. It can only be changed by cancelling or "
              "reversing that journal in ERPNext.").format(before.get("journal_entry")),
            title=_("Journal is submitted"))
    if state == "draft" or (before is not None and before.get("qbo_queue")):
        frappe.throw(
            _("This line is on draft journal {0}. Reopen the week to discard the draft, "
              "then change the line.").format(before.get("journal_entry") or before.get("qbo_queue")),
            title=_("Draft journal exists"))

    # ---- the week(s) ----
    closed = False
    for sheet in locked.values():
        if sheet["docstatus"] != 0 or sheet["status"] in ("Submitted", "Approved"):
            frappe.throw(
                _("The week ending {0} is approved. Reopen it before changing a line.").format(
                    frappe.format_value(sheet["week_ending"], {"fieldtype": "Date"})),
                title=_("Week is approved"))
        closed = closed or sheet["status"] == "Closed"

    def need(ok, message, title=None):
        if not ok:
            frappe.throw(message, frappe.PermissionError, title=title)

    closed_msg = _("This week is closed. Only Finance can change a line on it.")

    if "new" in groups:
        need(finance or not closed, closed_msg)
        return

    signed = before.get("status") == "Approved"
    ticked = bool(cint(before.get("reconciled")))
    clear_tick = False

    if "content" in groups:
        if signed:
            frappe.throw(_("This line is signed off. Withdraw the sign-off before changing it."),
                         title=_("Line is signed"))
        need(finance or not closed, closed_msg)
        clear_tick = True

    if "move" in groups:
        if signed:
            frappe.throw(_("This line is signed off. Withdraw the sign-off before moving it to another week."),
                         title=_("Line is signed"))
        need(finance or not closed, closed_msg)
        clear_tick = True

    if "void" in groups:
        need(finance or not (signed or closed),
             _("This line is signed off. Only Finance can void or reinstate it.") if signed else closed_msg)
        if signed and cint(doc.cancelled) and not (doc.cancel_remark or "").strip():
            frappe.throw(_("Say why this signed line is being voided."), title=_("Reason required"))
        clear_tick = True
        notes.append(_("Voided") + (f": {doc.cancel_remark}" if doc.cancel_remark else "")
                     if cint(doc.cancelled) else _("Reinstated"))

    if "receipt" in groups:
        need(finance or not closed, closed_msg)
        if signed:
            doc.changed_after_approval = 1
        clear_tick = True
        if signed or ticked:
            notes.append(_("Receipt photo replaced"))

    for group, who_msg in (("refs", _("Only Finance can change the slip or receipt number on a signed line.")),
                           ("vat", _("Only Finance can set VAT on a petty cash entry.")),
                           ("coding", _("Only Finance can change the accounts on a petty cash entry.")),
                           ("company", _("Only Finance can change the company on a petty cash entry."))):
        if group not in groups:
            continue
        if group == "refs":
            need(signer or not (signed or closed), who_msg if signed else closed_msg)
        else:
            need(signer, who_msg)
        if signed or ticked or group != "refs":
            notes.append(_describe(doc, before, GROUPS[group]))

    if "company" in groups:
        _check_accounts_for_company(doc)
        clear_tick = True

    if "sign" in groups:
        need(signer, _("Only Finance can approve or withdraw a petty cash entry."))
        if signed and doc.status != "Approved":
            clear_tick = True
            notes.append(_("Sign-off withdrawn") + (f": {doc.withdrawal_reason}" if doc.get("withdrawal_reason") else ""))

    # ---- the tick itself ----
    if "tick" in groups:
        need(finance, _("Only Finance can mark a petty cash line reconciled."))
        if cint(doc.reconciled):
            doc.reconciled_by, doc.reconciled_on = frappe.session.user, now_datetime()
        else:
            doc.reconciled_by = doc.reconciled_on = None
    elif ticked:
        confirmable = bool(groups & CONFIRMABLE) and not clear_tick
        if clear_tick or (groups & CONFIRMABLE):
            if confirmable and doc.flags.get("confirm_tick") and finance:
                doc.reconciled = 1
                doc.reconciled_by, doc.reconciled_on = frappe.session.user, now_datetime()
                notes.append(_("Finance confirmed the revised line; tick re-stamped"))
            else:
                doc.reconciled = 0
                doc.reconciled_by = doc.reconciled_on = None
                notes.append(_("Finance tick cleared because the line changed"))
    else:
        # Not ticked: the stamps are the server's, whatever a client echoes back.
        doc.reconciled_by = doc.reconciled_on = None

    doc.flags.audit_notes = [n for n in notes if n]


def _describe(doc, before, fields):
    parts = []
    for f in fields:
        if _differs(doc, before, f):
            parts.append(f"{LABELS.get(f, f)}: {before.get(f) or '-'} -> {doc.get(f) or '-'}")
    return "; ".join(parts) + f" ({frappe.session.user})" if parts else None


def _check_accounts_for_company(doc):
    """A line's ERPNext account must belong to the line's company."""
    for field in ("posting_account",):
        account = doc.get(field)
        if not account:
            continue
        owner = frappe.db.get_value("Account", account, "company")
        if owner and owner != doc.company:
            frappe.throw(
                _("{0} belongs to {1}, not {2}. Choose an account of {2} for this line, "
                  "then change the company.").format(account, owner, doc.company),
                title=_("Account is for another company"))


# ----------------------------------------------------------------------
# The week: close, confirm cash, approve, reopen — all under the same lock
# ----------------------------------------------------------------------

def week_gate(sheet_name, revision=None):
    """Lock a week and make sure the caller is looking at its current version.

    Returns the locked sheet row. For a native week the caller must send the revision
    it was shown; if a line has changed since — even one whose count and total did not
    move — the action is refused.
    """
    sheet = lock_sheet(sheet_name)
    if cint(sheet["native_lines"]):
        if revision in (None, ""):
            # A screen that does not know about weeks kept as entries (it sends no version).
            frappe.throw(
                _("This week keeps its lines as entries. Close, confirm and approve it from its week "
                  "page: /petty-cash/sheet?name={0}").format(sheet_name),
                title=_("Use the week page"))
        if cint(revision) != cint(sheet["lines_revision"]):
            frappe.throw(
                _("This week changed since you opened it. Reload it and check it again."),
                title=_("Week changed"))
    return sheet


def confirm_cash(sheet_name, revision=None, reason=None, evidence=None):
    """Finance ACKNOWLEDGES a cash exception on a Closed week.

    Acknowledging means the exception has been seen and its reason written down. It does
    not mean the balance reconciles, and it does not resolve the difference. The supplied
    balance is not touched. Recorded: the reason (required), any supporting evidence, who
    and when.
    """
    from vcl_finance.petty_cash.api import _is_accounts_manager
    if not _is_accounts_manager():
        frappe.throw(_("Only an Accounts Manager can acknowledge a cash exception."), frappe.PermissionError)
    sheet = week_gate(sheet_name, revision)
    if sheet["status"] != "Closed":
        frappe.throw(_("Only a Closed week has a cash exception to acknowledge."))
    if not (reason or "").strip():
        frappe.throw(_("Say what is known about this discrepancy. The reason is recorded with the acknowledgement."),
                     title=_("Reason required"))
    values = refresh_sheet(sheet_name, bump=False)
    refresh_cash(sheet_name)
    now = now_datetime()
    frappe.db.set_value(SHEET, sheet_name, {
        "cash_reconfirm_needed": 0, "cash_confirmed_by": frappe.session.user, "cash_confirmed_on": now,
        "cash_confirm_reason": reason.strip(), "cash_confirm_evidence": (evidence or "").strip() or None}, update_modified=False)
    pos = cash_position(sheet_name)
    fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})
    frappe.get_doc(SHEET, sheet_name).add_comment("Info", _(
        "Exception acknowledged (this does not mean the balance reconciles): actual balance at week end {0}, expected "
        "{1}, this week's discrepancy {2}. Reason: {3}{4}").format(
            fmt(pos["actual_balance_at_week_end"]), fmt(pos["expected_actual_close"]), fmt(pos["current_week_discrepancy"]),
            reason.strip(), _(" Evidence: {0}").format(evidence.strip()) if (evidence or "").strip() else ""))
    return {"name": sheet_name, "variance": values["variance"], "expected_close": values["expected_close"],
            "cash_count_end": pos["supplied_balance"], "cash_reconfirm_needed": 0, "cash": pos}


# ----------------------------------------------------------------------
# The fallback: turn a native week back into a week that keeps rows
# ----------------------------------------------------------------------

def convert_week_to_rows(sheet_name):
    """Convert a whole native week back to rows on its sheet, keeping every entry.

    This is what makes a rollback real. Code that predates native lines only understands
    sheet rows and would show a native week as empty, so before such code is deployed
    every native week is converted with this.

    Each entry gets a row written from it, and the SAME entry becomes that row's mirrored
    copy. Entry names do not change, so everything attached to an entry stays attached:
    its receipt, sign-off (who and when), finance tick (who and when), VAT, accounts,
    company, void reason, and its links to a draft or submitted journal and to the
    QuickBooks staging row. The week's own state, cash count, approval stamps and
    per-company posting record are not touched.

    All of it happens in one transaction under the week lock, and it is refused unless
    the mirror then finds every entry already in step with its row: nothing created,
    nothing changed, nothing orphaned.
    """
    from vcl_finance.petty_cash.api import _is_accounts_manager
    from vcl_finance.petty_cash.mirror import TABLE_MAP, mirror_sheet, row_hash

    if not _is_accounts_manager():
        frappe.throw(_("Only an Accounts Manager can convert a week."), frappe.PermissionError)
    lock_sheet(sheet_name)
    doc = frappe.get_doc(SHEET, sheet_name)
    if not cint(doc.native_lines):
        frappe.throw(_("{0} already keeps its lines as rows.").format(sheet_name))

    entries = frappe.get_all(
        ENTRY, filters={"week_sheet": sheet_name},
        fields=ROW_FIELDS + ["approved_by", "approved_on", "cancelled_by", "company"],
        order_by="txn_date asc, creation asc", limit_page_length=0)
    before_totals = (flt(doc.total_out, 2), flt(doc.total_in, 2), flt(doc.expected_close, 2))

    strip_rows(doc)                      # the in-memory view rows and padding go
    doc.native_lines = 0
    counters, pairs = {}, []
    for e in entries:
        table, row = _row_from_entry(e)
        payload = {k: v for k, v in row.items() if k not in ("name", "entry_status") and v is not None}
        if table != "parking_entries":
            run = payload.get("kind") if table == "misc_entries" else table
            counters[run] = counters.get(run, 0) + 1
            payload["row_idx"] = counters[run]
        pairs.append((e, table, doc.append(table, payload)))

    doc.flags.converting_to_rows = True
    doc.flags.skip_mirror = True         # the entries are linked to their rows below, then the mirror is run
    doc.save(ignore_permissions=True)    # native_lines is off now, so this is the ordinary row-week save

    for e, table, row in pairs:
        spec = TABLE_MAP[table]
        signed = e.status == "Approved"
        # The save stamped the row's lock and tick with whoever ran this, just now. They
        # are put back to who actually signed and ticked, and when.
        frappe.db.set_value(spec["doctype"], row.name, {
            "locked_by": e.approved_by if signed else None, "locked_on": e.approved_on if signed else None,
            "reconciled_by": e.reconciled_by if cint(e.reconciled) else None,
            "reconciled_on": e.reconciled_on if cint(e.reconciled) else None,
            "cancelled_on": e.cancelled_on if cint(e.cancelled) else None,
        }, update_modified=False)
        fresh = frappe.get_doc(spec["doctype"], row.name)
        frappe.db.set_value(ENTRY, e.name, {
            "week_sheet": None, "origin_doctype": spec["doctype"], "origin_row": row.name,
            "origin_sheet": sheet_name, "origin_week_ending": doc.week_ending,
            "origin_hash": row_hash(fresh, spec["amount_field"]), "sync_state": "Mirrored",
        }, update_modified=False)

    doc = frappe.get_doc(SHEET, sheet_name)
    stats = mirror_sheet(doc, raise_on_error=True)
    after_totals = (flt(doc.total_out, 2), flt(doc.total_in, 2), flt(doc.expected_close, 2))
    if stats["created"] or stats["updated"] or stats["orphaned"] or stats["unchanged"] != len(entries):
        frappe.throw(_("Conversion stopped: the mirror did not find every entry in step with its row ({0}). "
                       "Nothing was changed.").format(stats), title=_("Conversion not clean"))
    if after_totals != before_totals:
        frappe.throw(_("Conversion stopped: totals moved from {0} to {1}. Nothing was changed.")
                     .format(before_totals, after_totals), title=_("Conversion not clean"))
    doc.add_comment("Info", _("Week converted from entries back to rows: {0} line(s), each still the same entry. "
                              "Totals unchanged.").format(len(entries)))
    return {"sheet": sheet_name, "lines": len(entries), "mirror": stats, "totals": after_totals}


# ----------------------------------------------------------------------
# The float's balance: the book and the actual balance are two separate chains
# ----------------------------------------------------------------------
#
# BOOK     opening balance         ->  book cash balance        (opening - paid + received)
# ACTUAL   opening actual balance  ->  expected actual balance  (opening - paid + received)
#                                      compared with the actual balance at the week end
#
# WHERE THE ACTUAL BALANCE COMES FROM. The float is not a tin that somebody counts on
# Saturday night. Each week the exact balance as it stood at the OPENING OF MONDAY is
# supplied, and it is received and reviewed on Wednesday. It can be made up of cash in
# hand, a corporate M-Pesa balance and a personal M-Pesa balance of which only a stated
# part belongs to the float. Two dates are kept: the date the balance is OF (Monday), and
# the date it was received and reviewed (Wednesday). The supplied Monday figure is used
# as it is: it is never reconstructed from a later balance, and nothing dated Monday or
# later goes into the comparison.
#
# THE BRIDGE. Weeks still end on Saturday, so Saturday's closing balance is reached from
# Monday's opening through SUNDAY'S movements only:
#
#     Saturday closing balance = supplied Monday opening + Sunday payments - Sunday receipts
#
# Sunday belongs to the following week, where it is counted once: that week opens with
# the Saturday closing balance and then takes ALL of its own lines, Sunday included.
#
# THE DIFFERENCE between the book and the actual balance is the sum of three things kept
# apart: the historical difference (the baseline, recorded once), discrepancies approved
# in earlier weeks since then, and THIS week's discrepancy. Only the last is a new
# exception.

COMPONENT_KINDS = ("Cash in hand", "Corporate M-Pesa", "Personal M-Pesa")


def clean_components(components, amount=None):
    """Normalise a supplied balance into its components → ``(rows, float_total)``.

    Each component: ``kind`` (cash in hand, corporate M-Pesa or personal M-Pesa), an
    optional ``label`` (whose line, which till), the ``balance`` as supplied, and
    ``float_amount``: how much of it belongs to THIS float.

    A personal M-Pesa balance is somebody's own money with float money in it. How much
    of it is the float's must be stated; it is never assumed to be all of it. Cash in
    hand and a corporate M-Pesa balance are taken as the float's in full unless a
    smaller amount is given. No part can be negative or exceed its balance.

    With no components, ``amount`` is taken as one undivided figure for the float.
    """
    if isinstance(components, str):
        components = json.loads(components or "[]")
    rows = []
    for c in (components or []):
        kind = (c.get("kind") or "").strip()
        if c.get("balance") in (None, "") and c.get("float_amount") in (None, ""):
            continue
        if kind not in COMPONENT_KINDS:
            frappe.throw(_("A balance is cash in hand, corporate M-Pesa or personal M-Pesa. Got '{0}'.").format(kind))
        balance = flt(c.get("balance"))
        stated = c.get("float_amount") not in (None, "")
        if kind == "Personal M-Pesa" and not stated:
            frappe.throw(
                _("Say how much of the personal M-Pesa balance ({0}) belongs to this float. A personal balance "
                  "is not taken to be company money.").format(frappe.format_value(balance, {"fieldtype": "Currency"})),
                title=_("Personal M-Pesa: float portion needed"))
        part = flt(c.get("float_amount")) if stated else balance
        if balance < 0 or part < 0:
            frappe.throw(_("A balance cannot be negative ({0}).").format(kind), title=_("Enter the actual balance"))
        if part > balance:
            frappe.throw(_("{0}: the amount belonging to this float ({1}) is more than the balance supplied ({2}).").format(
                kind, frappe.format_value(part, {"fieldtype": "Currency"}), frappe.format_value(balance, {"fieldtype": "Currency"})),
                title=_("Check the float portion"))
        rows.append({"kind": kind, "label": (c.get("label") or "").strip() or None,
                     "balance": flt(balance, 2), "float_amount": flt(part, 2)})
    if rows:
        return rows, flt(sum(r["float_amount"] for r in rows), 2)
    if amount in (None, ""):
        frappe.throw(_("Enter the balance supplied for this float."), title=_("Balance needed"))
    if flt(amount) < 0:
        frappe.throw(_("An actual balance cannot be negative. Enter the balance as supplied."), title=_("Enter the actual balance"))
    return [], flt(amount, 2)


def _check_dates(effective_on, reviewed_on, earliest, latest, what):
    """The balance's own date, and the date it was received and reviewed. Two different things."""
    effective_on, reviewed_on = getdate(effective_on), getdate(reviewed_on)
    if effective_on < getdate(earliest) or effective_on > getdate(latest):
        frappe.throw(_("{0} is the balance at the opening of a day between {1} and {2}. Got {3}.").format(
            what, frappe.format_value(earliest, {"fieldtype": "Date"}), frappe.format_value(latest, {"fieldtype": "Date"}),
            frappe.format_value(effective_on, {"fieldtype": "Date"})), title=_("Balance date"))
    if reviewed_on < effective_on:
        frappe.throw(_("The date the balance was received and reviewed cannot be before the date it is the balance of."),
                     title=_("Review date"))
    return effective_on, reviewed_on


def _movements(float_name, first_day, before_day):
    """Paid and received on a float on the days from ``first_day`` up to but NOT including
    ``before_day``. Lines are found by their own date, on whichever sheet they sit; voided
    and orphaned lines are left out. Read under lock."""
    first_day, before_day = getdate(first_day), getdate(before_day)
    if before_day <= first_day:
        return {"out": 0.0, "in": 0.0, "lines": 0, "from": None, "to": None}
    last = frappe.utils.add_days(before_day, -1)
    rows = frappe.db.sql(
        """select amount, cash_in from `tabPetty Cash Entry`
           where `float` = %s and txn_date between %s and %s
             and ifnull(cancelled, 0) = 0 and ifnull(sync_state, '') != 'Orphaned' for update""",
        (float_name, first_day, last), as_dict=True)
    return {"out": flt(sum(flt(r.amount) for r in rows if not r.cash_in), 2),
            "in": flt(sum(flt(r.amount) for r in rows if r.cash_in), 2),
            "lines": len(rows), "from": str(first_day), "to": str(last)}


def _week_start(week_ending):
    from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import week_saturday
    return frappe.utils.add_days(week_saturday(getdate(week_ending)), -6)       # the Sunday


def _previous_native_week(sheet_name):
    head = frappe.db.get_value(SHEET, sheet_name, ["week_ending", "float"], as_dict=True)
    rows = frappe.get_all(SHEET, filters={"float": head.float, "week_ending": ("<", head.week_ending), "docstatus": ("<", 2)},
                          fields=["name", "native_lines", "status", "cash_count_end", "actual_at_week_end", "opening_actual_balance", "week_ending"],
                          order_by="week_ending desc", limit=1)
    return rows[0] if rows else None


def baseline_for(sheet_name):
    """The historical-difference baseline that applies to this week: the most recent week of
    the same float, at or before this one, where an opening actual balance was first recorded."""
    head = frappe.db.get_value(SHEET, sheet_name, ["week_ending", "float"], as_dict=True)
    rows = frappe.get_all(
        SHEET, filters={"float": head.float, "week_ending": ("<=", head.week_ending), "cash_baseline_set": 1, "docstatus": ("<", 2)},
        fields=["name", "week_ending", "opening_balance", "opening_actual_balance", "baseline_note",
                "baseline_acknowledged_by", "baseline_acknowledged_on"], order_by="week_ending desc", limit=1)
    if not rows:
        return None
    b = rows[0]
    b["difference"] = flt(flt(b.opening_actual_balance) - flt(b.opening_balance), 2)
    return b


def set_opening_count(sheet_name, amount=None, note=None, components=None, balance_date=None, reviewed_on=None):
    """Record the float's actual balance for the FIRST week kept as entries (Accounts Manager).

    What is supplied is the exact balance at the opening of Monday of this week (received
    and reviewed some days later). The week itself starts on Sunday, so the balance the
    week opens with is reached back through that one Sunday:

        opening actual balance = supplied Monday opening + Sunday payments - Sunday receipts

    This sets the BASELINE. The book opening balance and the opening actual balance are
    both kept, and their difference is the historical difference, acknowledged here once
    with a note. Later weeks open with the previous week's Saturday closing balance and do
    not go through this.
    """
    from vcl_finance.petty_cash.api import _is_accounts_manager
    if not _is_accounts_manager():
        frappe.throw(_("Only an Accounts Manager can record the opening actual balance."), frappe.PermissionError)
    sheet = lock_sheet(sheet_name)
    if not cint(sheet["native_lines"]):
        frappe.throw(_("The opening actual balance is recorded on weeks that keep their lines as entries."))
    if sheet["status"] != "Draft":
        frappe.throw(_("The opening actual balance is recorded while the week is open."))
    prev = _previous_native_week(sheet_name)
    if prev and cint(prev.native_lines) and prev.status in ("Closed", "Approved"):
        frappe.throw(_("This week opens with the Saturday closing balance of {0}. It is not entered again.").format(prev.name))
    rows, total = clean_components(components, amount)
    start = _week_start(sheet["week_ending"])
    effective, reviewed = _check_dates(
        balance_date or frappe.utils.add_days(start, 1),                         # Monday of this week
        reviewed_on or max(getdate(frappe.utils.today()), getdate(balance_date or frappe.utils.add_days(start, 1))),
        start, sheet["week_ending"], _("The opening balance"))
    since = _movements(sheet["float"], start, effective)                         # Sunday, when the balance is Monday's
    opening = flt(total + since["out"] - since["in"], 2)
    head = frappe.db.get_value(SHEET, sheet_name, ["opening_balance"], as_dict=True)
    difference = flt(opening - flt(head.opening_balance), 2)
    if difference and not (note or "").strip():
        frappe.throw(
            _("The opening actual balance ({0}) differs from the book opening balance ({1}) by {2}. Say what is known "
              "about this difference. It is recorded once as the historical difference carried forward.").format(
                frappe.format_value(opening, {"fieldtype": "Currency"}),
                frappe.format_value(head.opening_balance, {"fieldtype": "Currency"}),
                frappe.format_value(difference, {"fieldtype": "Currency"})),
            title=_("Acknowledge the historical difference"))
    now = now_datetime()
    frappe.db.set_value(SHEET, sheet_name, {
        "opening_snapshot_amount": total, "opening_snapshot_on": effective, "opening_reviewed_on": reviewed,
        "opening_components": json.dumps(rows) if rows else None,
        "opening_actual_balance": opening, "opening_actual_source": "Supplied",
        "opening_recorded_by": frappe.session.user, "opening_recorded_on": now,
        "cash_baseline_set": 1, "baseline_note": (note or "").strip() or None,
        "baseline_acknowledged_by": frappe.session.user, "baseline_acknowledged_on": now}, update_modified=False)
    fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})
    day = lambda d: frappe.format_value(d, {"fieldtype": "Date"})
    frappe.get_doc(SHEET, sheet_name).add_comment("Info", _(
        "Opening actual balance recorded. Supplied: {0} as at the opening of {1}, received and reviewed {2}{3}. "
        "Bridged back to the start of the week ({4}): + paid {5} - received {6} = {7}. Book opening balance: {8}. "
        "Historical difference carried forward from this week: {9}. Both figures are kept; neither replaces the other.{10}").format(
            fmt(total), day(effective), day(reviewed),
            _(" ({0})").format("; ".join(f"{r['kind']} {fmt(r['float_amount'])}" + (f" of {fmt(r['balance'])}" if r["float_amount"] != r["balance"] else "") for r in rows)) if rows else "",
            day(start), fmt(since["out"]), fmt(since["in"]), fmt(opening), fmt(head.opening_balance), fmt(difference),
            _(" Note: {0}").format(note.strip()) if (note or "").strip() else ""))
    refresh_cash(sheet_name)
    return cash_position(sheet_name)


def ensure_opening_count(sheet_name):
    """The actual balance this week opens with. For a week that follows another week kept
    as entries it is that week's SATURDAY CLOSING balance; otherwise it must be recorded."""
    head = frappe.db.get_value(SHEET, sheet_name, ["opening_actual_balance", "opening_actual_source"], as_dict=True)
    if head.opening_actual_source:
        return flt(head.opening_actual_balance)
    prev = _previous_native_week(sheet_name)
    if prev and cint(prev.native_lines) and prev.status in ("Closed", "Approved"):
        frappe.db.set_value(SHEET, sheet_name, {
            "opening_actual_balance": flt(prev.actual_at_week_end), "opening_actual_source": "Carried from previous week"},
            update_modified=False)
        return flt(prev.actual_at_week_end)
    return None


def movements_since_week_end(sheet_name):
    """Paid and received AFTER this week ended and BEFORE the day the supplied balance is the
    opening of. With a Monday opening balance that is Sunday only: Monday's own lines, and
    everything after, come after the snapshot and are left out."""
    h = frappe.db.get_value(SHEET, sheet_name, ["week_ending", "float", "balance_effective_on"], as_dict=True)
    if not h or not h.balance_effective_on:
        return {"out": 0.0, "in": 0.0, "lines": 0, "from": None, "to": None}
    return _movements(h.float, frappe.utils.add_days(getdate(h.week_ending), 1), h.balance_effective_on)


def refresh_cash(sheet_name):
    """Recompute the actual-balance figures stored on the week. Called under the week lock.

        expected actual balance at week end = opening actual balance + received - paid   (this week)
        actual balance at week end          = supplied Monday opening + Sunday paid - Sunday received
        this week's discrepancy             = actual balance at week end - expected
    """
    h = frappe.db.get_value(SHEET, sheet_name, [
        "native_lines", "status", "opening_actual_balance", "opening_actual_source", "total_out", "total_in", "cash_count_end",
        "week_ending", "float", "opening_snapshot_amount", "opening_snapshot_on"], as_dict=True)
    if not h or not cint(h.native_lines) or not h.opening_actual_source:
        return None
    opening = flt(h.opening_actual_balance)
    if h.opening_actual_source == "Supplied" and h.status == "Draft" and h.opening_snapshot_on:
        # While the first week is still open, a Sunday line keyed late moves the balance the
        # week opened with (the supplied Monday figure does not change). Fixed once closed.
        back = _movements(h.float, _week_start(h.week_ending), h.opening_snapshot_on)
        opening = flt(flt(h.opening_snapshot_amount) + back["out"] - back["in"], 2)
    expected = flt(opening - flt(h.total_out) + flt(h.total_in), 2)
    counted = h.status in ("Closed", "Approved", "Submitted")
    since = movements_since_week_end(sheet_name) if counted else {"out": 0.0, "in": 0.0}
    at_week_end = flt(flt(h.cash_count_end) + since["out"] - since["in"], 2) if counted else 0
    values = {"opening_actual_balance": opening, "expected_actual_close": expected,
              "paid_before_balance_date_amt": since["out"], "received_before_balance_date_amt": since["in"],
              "actual_at_week_end": at_week_end,
              "cash_discrepancy": flt(at_week_end - expected, 2) if counted else 0}
    frappe.db.set_value(SHEET, sheet_name, values, update_modified=False)
    return values


def refresh_previous_week_cash(sheet_name):
    """A Sunday line keyed or changed on THIS week sits between the previous week's end and
    the Monday its balance is the opening of, so it changes that week's bridge.

    While the previous week is Closed (balance entered, not yet approved) its figures
    follow, and a discrepancy that appears has to be acknowledged. Once it is Approved it
    is frozen: the line then simply counts in this week's own check.
    """
    prev = _previous_native_week(sheet_name)
    if not prev or not cint(prev.native_lines) or prev.status != "Closed":
        return
    before = frappe.db.get_value(SHEET, prev.name, "cash_discrepancy")
    lock_sheet(prev.name)
    cash = refresh_cash(prev.name)
    if cash and flt(cash["cash_discrepancy"], 2) != flt(before, 2):
        frappe.db.set_value(SHEET, prev.name, {
            "cash_reconfirm_needed": 1 if flt(cash["cash_discrepancy"], 2) else 0,
            "cash_confirmed_by": None, "cash_confirmed_on": None, "cash_confirm_reason": None, "cash_confirm_evidence": None},
            update_modified=False)
        fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})
        frappe.get_doc(SHEET, prev.name).add_comment("Info", _(
            "A line dated between this week's end and the date of its supplied balance changed on {0}. Paid in that "
            "window is now {1}, actual balance at week end {2}, this week's discrepancy {3} (was {4}).").format(
                sheet_name, fmt(cash["paid_before_balance_date_amt"]), fmt(cash["actual_at_week_end"]),
                fmt(cash["cash_discrepancy"]), fmt(before)))


def _load(text):
    try:
        return json.loads(text or "[]")
    except ValueError:
        return []


def cash_position(sheet_name):
    """Everything about a week's balance, with the four things kept apart: the book cash
    balance, the actual balance as supplied, the historical difference carried, and this
    week's discrepancy."""
    h = frappe.db.get_value(SHEET, sheet_name, [
        "name", "status", "native_lines", "week_ending", "float", "opening_balance", "total_out", "total_in", "expected_close",
        "cash_count_end", "variance", "opening_actual_balance", "opening_actual_source", "expected_actual_close",
        "cash_discrepancy", "cash_reconfirm_needed", "cash_confirmed_by", "cash_confirmed_on", "cash_confirm_reason",
        "cash_confirm_evidence", "cash_approval_kind", "balance_effective_on", "balance_reviewed_on", "balance_components",
        "paid_before_balance_date_amt", "received_before_balance_date_amt", "actual_at_week_end",
        "opening_snapshot_amount", "opening_snapshot_on", "opening_reviewed_on", "opening_components"], as_dict=True)
    counted = h.status in ("Closed", "Approved", "Submitted")
    base = baseline_for(sheet_name)
    opening_difference = flt(flt(h.opening_actual_balance) - flt(h.opening_balance), 2) if h.opening_actual_source else None
    earlier = flt(opening_difference - base["difference"], 2) if (base and opening_difference is not None) else None
    supplied_opening = None
    if h.opening_actual_source == "Supplied":
        supplied_opening = {"amount": flt(h.opening_snapshot_amount, 2), "balance_effective_on": str(h.opening_snapshot_on),
                            "balance_reviewed_on": str(h.opening_reviewed_on), "components": _load(h.opening_components)}
    return {
        "sheet": h.name, "status": h.status, "counted": counted,
        "book_opening": flt(h.opening_balance, 2), "book_cash_balance": flt(h.expected_close, 2),
        "opening_actual_balance": flt(h.opening_actual_balance, 2) if h.opening_actual_source else None,
        "opening_actual_source": h.opening_actual_source or None,
        "opening_supplied": supplied_opening,
        "expected_actual_close": flt(h.expected_actual_close, 2) if h.opening_actual_source else None,
        # the balance as supplied: what it is OF, and when it was received and reviewed
        "supplied_balance": flt(h.cash_count_end, 2) if counted else None,
        "balance_effective_on": str(h.balance_effective_on) if (counted and h.balance_effective_on) else None,
        "balance_reviewed_on": str(h.balance_reviewed_on) if (counted and h.balance_reviewed_on) else None,
        "components": _load(h.balance_components) if counted else [],
        "paid_before_balance_date": flt(h.paid_before_balance_date_amt, 2) if counted else None,
        "received_before_balance_date": flt(h.received_before_balance_date_amt, 2) if counted else None,
        "actual_balance_at_week_end": flt(h.actual_at_week_end, 2) if counted else None,
        "current_week_discrepancy": flt(h.cash_discrepancy, 2) if (counted and h.opening_actual_source) else None,
        "historical_difference": base["difference"] if base else None,
        "baseline": ({"sheet": base["name"], "week_ending": str(base["week_ending"]), "difference": base["difference"],
                      "book_opening": flt(base["opening_balance"], 2), "actual_opening": flt(base["opening_actual_balance"], 2),
                      "note": base["baseline_note"], "acknowledged_by": base["baseline_acknowledged_by"],
                      "acknowledged_on": str(base["baseline_acknowledged_on"])} if base else None),
        "earlier_discrepancies_since_baseline": earlier,
        "book_vs_actual": flt(flt(h.actual_at_week_end) - flt(h.expected_close), 2) if counted else opening_difference,
        "exception_needs_acknowledging": bool(cint(h.cash_reconfirm_needed)),
        "acknowledged_by": h.cash_confirmed_by, "acknowledged_on": str(h.cash_confirmed_on) if h.cash_confirmed_on else None,
        "acknowledged_reason": h.cash_confirm_reason, "acknowledged_evidence": h.cash_confirm_evidence,
        "approval_kind": h.cash_approval_kind or None,
    }
