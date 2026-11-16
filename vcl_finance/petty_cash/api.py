"""Whitelisted JSON API for the keypad entry wizard (Phase 2).

``quick_entry`` appends ONE line to the right child table of the sheet whose Sun–Sat
week CONTAINS the entry's ``txn_date`` — NOT necessarily the sheet passed in (voucher /
wages / loan / misc grids are pre-seeded with blank rows by ``ensure_grid``, so we
normally just fill the first empty one; parking rows are created on demand).
``get_feed`` returns the running feed + live balance the wizard renders.
``attach_receipt`` sets a voucher row's ``receipt`` (Attach Image) from an
already-uploaded Frappe File.

Ported from the prototype's ``routes/api.py`` (quick-entry) and
``routes/sheets.py`` (``_feed_items`` / ``_summary``), adapted to the Frappe field
names: voucher ``category`` Select + ``cash_in`` Check; wages ``entry_type``
Wage/Overtime/Piecework/Commission; loans ``amount_issued``/``amount_signed``;
parking ``txn_date`` + ``vehicle`` (``day_idx`` is derived server-side); misc
``kind`` "Bike Fuel"/"Forklift".

Everything recomputes server-side — client amounts are never trusted for totals.
"""
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, add_days, get_url

from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    VEHICLES, DAY_NAMES, CATEGORY_CODES, week_saturday, create_for_week,
)

# Wizard kind → display label + colour for the running feed (brand vars resolved
# client-side; here we just emit the CSS custom-property name).
TYPE_COLOR = {
    "voucher": "var(--vcl-blue, #1F5FBF)",
    "wage": "var(--vcl-sage, #6E8B7B)",
    "overtime": "var(--vcl-sage, #6E8B7B)",
    "commission": "#5B2C6F",
    "piecework": "#5B2C6F",
    "loan": "var(--vcl-amber, #B86B00)",
    "bike": "var(--vcl-blue-mid, #3E7BD6)",
    "forklift": "var(--vcl-navy, #14264A)",
    "parking": "var(--muted, #8A909E)",
}
CAT_COLOR = {
    "TG": "var(--vcl-blue, #1F5FBF)", "TE": "var(--vcl-blue-mid, #3E7BD6)",
    "SE": "var(--vcl-navy, #14264A)", "OA": "var(--vcl-sage, #6E8B7B)",
    "FD": "var(--vcl-amber, #B86B00)", "GP": "#5B2C6F", "OT": "var(--muted, #8A909E)",
    "IN": "var(--vcl-green, #1B7A45)",
}
CAT_LABEL = {
    "TG": "Transport-Goods", "TE": "Transport-Employee", "SE": "Spares / Eng",
    "OA": "Office / Admin", "FD": "Food", "GP": "Geeprint", "OT": "Other", "IN": "Cash-in",
}
WAGE_TYPE_LABEL = {
    "Wage": "Wage", "Overtime": "Overtime", "Piecework": "Piecework", "Commission": "Commission",
}


def _date_str(d):
    return str(d) if d else None


# ----------------------------------------------------------------------
# Summary + feed (ports _summary / _feed_items)
# ----------------------------------------------------------------------

def _retrying(fn):
    """Each of these actions is the whole of its request. It runs in a fresh transaction
    that takes the week lock before it reads anything, and is run again if another session
    got in first. See ``native.exclusive_call``."""
    import functools

    @functools.wraps(fn)
    def run(*args, **kwargs):
        from vcl_finance.petty_cash.native import exclusive_call
        return exclusive_call(fn, args, kwargs)
    return run


def _native(doc):
    """Rows for a NATIVE week are rebuilt in memory from its entries, so everything
    below that reads ``doc.vouchers`` and friends works on either kind of week."""
    if cint(doc.get("native_lines")):
        from vcl_finance.petty_cash import native
        native.hydrate(doc)
    return doc


def _summary(doc):
    _native(doc)
    # Cancelled (voided) rows are excluded from every figure here so cash-remaining,
    # expected close and variance all ignore them (they remain on the sheet for audit).
    cat = {c: 0.0 for c in CATEGORY_CODES}
    cat_in = 0.0
    for v in doc.vouchers:
        if v.cancelled:
            continue
        amt = flt(v.amount)
        if v.cash_in:
            cat_in += amt
        elif v.category in cat:
            cat[v.category] += amt

    parking_total = flt(sum(flt(p.amount) for p in doc.parking_entries if not p.cancelled))
    bike_total = flt(sum(flt(m.amount) for m in doc.misc_entries if m.kind == "Bike Fuel" and not m.cancelled))
    forklift_total = flt(sum(flt(m.amount) for m in doc.misc_entries if m.kind == "Forklift" and not m.cancelled))
    wages_total = flt(sum(flt(w.amount) for w in doc.wages_entries if not w.cancelled))
    loans_total = flt(sum(flt(l.amount_issued) for l in doc.loan_entries if not l.cancelled))
    voucher_out = flt(sum(cat.values()))

    total_out = voucher_out + parking_total + bike_total + forklift_total + wages_total + loans_total
    expected_close = flt((doc.opening_balance or 0) - total_out + cat_in, 2)
    variance = flt((doc.cash_count_end or 0) - expected_close, 2)

    return {
        "cat_out": cat,
        "cat_in": flt(cat_in, 2),
        "voucher_total_out": flt(voucher_out, 2),
        "parking_total": flt(parking_total, 2),
        "bike_total": flt(bike_total, 2),
        "forklift_total": flt(forklift_total, 2),
        "wages_total": flt(wages_total, 2),
        "loans_total": flt(loans_total, 2),
        "total_out": flt(total_out, 2),
        "expected_close": expected_close,
        "variance": variance,
        "opening_balance": flt(doc.opening_balance or 0, 2),
        "status": doc.status,
        # For a native week: the version the screen is showing. Close and approve send
        # it back, and are refused if a line has changed since.
        "native": bool(cint(doc.get("native_lines"))),
        "lines_revision": cint(doc.get("lines_revision")),
        "cash_reconfirm_needed": bool(cint(doc.get("cash_reconfirm_needed"))),
        # The figure the screens have called "cash remaining" comes from the BOOK. On a
        # week kept as entries it is named for what it is, next to what was counted.
        "balance_label": "Book cash balance" if cint(doc.get("native_lines")) else "Cash remaining",
    }


def _feed_items(doc):
    """Flatten a sheet's non-blank entries into one chronological feed."""
    _native(doc)
    items = []

    for v in doc.vouchers:
        out_amt = flt(v.amount) if not v.cash_in else 0.0
        in_amt = flt(v.amount) if v.cash_in else 0.0
        date = _date_str(v.txn_date)
        ticks = {"pc": bool(v.pc_received), "etr": bool(v.etr_received)}
        if out_amt:
            code = v.category or "OT"
            items.append({
                "id": v.name, "kind": "voucher", "section": "voucher", "row_idx": v.row_idx,
                "date": date, "label": CAT_LABEL.get(code, code), "cat_code": code,
                "recipient": v.recipient or "", "voucher_no": v.voucher_no or "",
                "subtitle": (v.voucher_no or v.recipient or ""), "amount": out_amt,
                "direction": "out", "color": CAT_COLOR.get(code), "ticks": ticks,
                "receipt": v.receipt or None,
                "cancelled": bool(v.cancelled), "cancel_remark": v.cancel_remark or "",
                "locked": bool(v.locked), "locked_by": v.locked_by or "",
                "locked_on": str(v.locked_on) if v.locked_on else "",
            })
        if in_amt:
            items.append({
                "id": v.name, "kind": "voucher", "section": "voucher", "row_idx": v.row_idx,
                "date": date, "label": "Cash-in", "cat_code": "IN",
                "recipient": v.recipient or "", "voucher_no": v.voucher_no or "",
                "subtitle": v.voucher_no or "", "amount": in_amt, "direction": "in",
                "color": CAT_COLOR["IN"], "ticks": ticks, "receipt": v.receipt or None,
                "cancelled": bool(v.cancelled), "cancel_remark": v.cancel_remark or "",
                "locked": bool(v.locked), "locked_by": v.locked_by or "",
                "locked_on": str(v.locked_on) if v.locked_on else "",
            })

    for w in doc.wages_entries:
        if not flt(w.amount):
            continue
        t = w.entry_type or "Wage"
        kind = "commission" if t == "Commission" else ("piecework" if t == "Piecework" else "wage")
        items.append({
            "id": w.name, "kind": kind, "section": "wages", "row_idx": w.row_idx,
            "date": _date_str(w.txn_date), "label": WAGE_TYPE_LABEL.get(t, "Wage"),
            "recipient": w.recipient or "", "staff_id": w.staff_id or "", "reason": w.reason or "",
            "subtitle": w.reason or w.staff_id or "", "amount": flt(w.amount), "direction": "out",
            "color": TYPE_COLOR.get(kind), "ticks": {"paye": bool(w.paye)}, "receipt": w.receipt or None,
            "cancelled": bool(w.cancelled), "cancel_remark": w.cancel_remark or "",
            "locked": bool(w.locked), "locked_by": w.locked_by or "",
            "locked_on": str(w.locked_on) if w.locked_on else "",
        })

    for l in doc.loan_entries:
        if not flt(l.amount_issued):
            continue
        items.append({
            "id": l.name, "kind": "loan", "section": "loan", "row_idx": l.row_idx,
            "date": _date_str(l.txn_date), "label": "Loan", "recipient": l.recipient or "",
            "staff_id": l.staff_id or "", "reason": l.reason or "",
            "subtitle": l.reason or l.staff_id or "", "amount": flt(l.amount_issued),
            "direction": "out", "color": TYPE_COLOR["loan"], "ticks": {"paye": bool(l.paye)},
            "receipt": l.receipt or None,
            "cancelled": bool(l.cancelled), "cancel_remark": l.cancel_remark or "",
            "locked": bool(l.locked), "locked_by": l.locked_by or "",
            "locked_on": str(l.locked_on) if l.locked_on else "",
        })

    for m in doc.misc_entries:
        if not flt(m.amount):
            continue
        kind = "bike" if m.kind == "Bike Fuel" else "forklift"
        items.append({
            "id": m.name, "kind": kind, "section": "misc", "row_idx": m.row_idx,
            "date": _date_str(m.txn_date), "label": m.kind, "recipient": "",
            "notes": m.notes or "", "subtitle": m.notes or "", "amount": flt(m.amount),
            "direction": "out", "color": TYPE_COLOR.get(kind), "ticks": {}, "receipt": m.receipt or None,
            "cancelled": bool(m.cancelled), "cancel_remark": m.cancel_remark or "",
            "locked": bool(m.locked), "locked_by": m.locked_by or "",
            "locked_on": str(m.locked_on) if m.locked_on else "",
        })

    for p in doc.parking_entries:
        if not flt(p.amount):
            continue
        items.append({
            "id": p.name, "kind": "parking", "section": "parking", "row_idx": None,
            "day_idx": p.day_idx, "vehicle": p.vehicle, "slot": p.slot,
            "date": _date_str(p.txn_date),
            "label": "Parking", "recipient": p.vehicle,
            "subtitle": p.vehicle or p.day_idx or "", "amount": flt(p.amount),
            "direction": "out", "color": TYPE_COLOR["parking"], "ticks": {}, "receipt": p.receipt or None,
            "cancelled": bool(p.cancelled), "cancel_remark": p.cancel_remark or "",
            "locked": bool(p.locked), "locked_by": p.locked_by or "",
            "locked_on": str(p.locked_on) if p.locked_on else "",
        })

    # Newest first: dated desc, undated sink under dated (stable by id).
    items.sort(key=lambda it: (it["date"] or "", str(it["id"])), reverse=True)
    return items


@frappe.whitelist()
def get_feed(sheet):
    """Running feed for the wizard: every recorded entry + live balance figures."""
    if not frappe.has_permission("Petty Cash Sheet", "read", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    return {"items": _feed_items(doc), "summary": _summary(doc)}


# ----------------------------------------------------------------------
# Accounts-Manager guard (defined here so all call sites below can use it)
# ----------------------------------------------------------------------

PETTY_PRIV = {"Accounts Manager", "System Manager"}

# Who may SIGN and CODE one entry — wider than PETTY_PRIV.
#
# Shiro keys petty cash and Tanuj asked for her to approve it too. Doing that by
# granting Accounts Manager would have handed the petty cash custodian the bank,
# the journals and every invoice, because that is what the role carries
# everywhere else in ERPNext. This role carries one thing.
#
# It deliberately does NOT reach _is_accounts_manager below, so closing a week,
# approving a week, reopening one and posting all stay where they were. The
# recorder can sign a line; she cannot lock the week she keyed, and she cannot
# push it into a journal.
# Decision (Tanuj, 16/11/2026): ACCOUNTS codes the lines and signs them; the petty cash
# custodian (Shiro) only keys what is paid out. So the signing role is the Accounts User
# role plus the accounts managers. The old purpose-built "Petty Cash Approver" role is no
# longer part of the rule and is removed from the custodian's account.
PETTY_APPROVERS = PETTY_PRIV | {"Accounts User"}


def _is_accounts_manager():
    return bool(set(frappe.get_roles()) & PETTY_PRIV)


def _assert_can_write(sheet):
    """Block edits to a locked week unless the caller is an Accounts Manager."""
    if sheet.is_locked() and not _is_accounts_manager():
        frappe.throw(
            _("The week ending {0} is closed. Please speak to Finance to have this "
              "entry posted.").format(frappe.format_value(sheet.week_ending, {"fieldtype": "Date"})),
            frappe.PermissionError)


def _assert_row_unlocked(row):
    """Block a non-AM from touching a per-row-locked child entry.

    ``PettyCashSheet.guard_locked_rows`` is the real enforcement (it also covers the
    Compass grid's full-document REST save, which never reaches this module). This is
    a cheap early check so the API call sites fail with the right message instead of
    a diff-detected throw from deep inside validate().
    """
    if cint(row.get("locked")) and not _is_accounts_manager():
        frappe.throw(_("This row is locked. Only an Accounts Manager can change it."),
                     frappe.PermissionError)


@frappe.whitelist()
def week_status(float_name, date):
    """For a given date + float, return the containing week's status WITHOUT creating it.

    Lets the entry wizard warn a custodian before they fill in an entry for a closed week.
    The petty-cash week runs Sun–Sat, anchored to Saturday as ``week_ending``.

    Returns::

        {
            "week_ending": "YYYY-MM-DD",   # Saturday of the containing week
            "sheet": name_or_None,          # existing Petty Cash Sheet name, or None
            "status": "Draft"|"Closed"|"Submitted"|"Approved"|"New",
            "locked": bool,                 # True when status in Closed/Submitted/Approved
            "is_accounts_manager": bool,    # whether the caller may override a lock
        }
    """
    # Week runs Sun–Sat; Saturday is the week_ending anchor (see week_saturday).
    week_ending = week_saturday(getdate(date))

    _LOCKED = {"Closed", "Submitted", "Approved"}
    existing = frappe.db.get_value(
        "Petty Cash Sheet",
        {"week_ending": week_ending, "float": float_name, "docstatus": ("<", 2)},
        ["name", "status"],
        as_dict=True,
    )
    if not existing:
        return {
            "week_ending": str(week_ending),
            "sheet": None,
            "status": "New",
            "locked": False,
            "is_accounts_manager": _is_accounts_manager(),
        }
    return {
        "week_ending": str(week_ending),
        "sheet": existing["name"],
        "status": existing["status"],
        "locked": existing["status"] in _LOCKED,
        "is_accounts_manager": _is_accounts_manager(),
    }


# ----------------------------------------------------------------------
# Quick-entry (ports /api/quick-entry)
# ----------------------------------------------------------------------

def _voucher_has_data(v):
    return bool(
        v.voucher_no or v.recipient or v.txn_date or v.notes
        or v.pc_received or v.etr_received or v.cash_in
        or flt(v.amount)
    )


def _first_blank(rows, is_blank):
    """First reusable scaffolding row. A locked row is never reused — even by an
    Accounts Manager — because filling it in would silently rewrite a frozen line;
    quick_entry appends a fresh row instead."""
    for r in rows:
        if cint(r.get("locked")):
            continue
        if is_blank(r):
            return r
    return None


def _next_idx(rows):
    return (max((r.row_idx or 0) for r in rows) + 1) if rows else 1


def _legacy_day(day):
    """Back-compat only: normalise a client-supplied day to a DAY_NAMES entry.

    Accepts an int / numeric string 0-6 (Sun=0 … Sat=6) or a "Sun".."Sat" string.
    Returns None when it isn't a valid day — new clients send a date instead.
    """
    if isinstance(day, str) and day.isdigit():
        day = int(day)
    if isinstance(day, int):
        return DAY_NAMES[day] if day in range(7) else None
    return day if day in DAY_NAMES else None


def _route_to_week(anchor, txn_date):
    """Resolve the sheet a ``txn_date`` actually belongs to, for ``anchor``'s float.

    The week a transaction belongs to is the Sun–Sat week that CONTAINS its date —
    never "whichever sheet was open when it was typed in". Recording Sat 04/07 while
    the 05/07–11/07 sheet is open must land on the sheet ending 04/07.

    Find-or-create, so a custodian entering Sunday's spend on Monday morning still
    gets a sheet (created via ``create_for_week``, which carries the opening balance
    forward). Returns ``(doc, rerouted)`` — ``doc`` is ``anchor`` untouched when the
    date already falls in its week, or when no date was supplied (legacy callers,
    and the undated ``day_idx`` parking path).
    """
    if not txn_date:
        # A date is not optional: the week is derived from it. There is no sensible
        # "today's week" fallback — that is exactly how Sat 04/07 lines ended up on
        # the 05/07–11/07 sheet and duplicated Wk 27.
        frappe.throw(_("A date is required. The week an entry belongs to is derived "
                       "from its date."))

    want = week_saturday(txn_date)
    # The anchor's OWN week is derived the same way, so a historical Friday-anchored
    # backfill sheet still recognises its own Sun–Sat dates and is never rerouted.
    have = week_saturday(getdate(anchor.week_ending)) if anchor.week_ending else None
    if have == want:
        return anchor, False

    name = frappe.db.get_value(
        "Petty Cash Sheet",
        {"week_ending": want, "float": anchor.float, "docstatus": ("<", 2)},
        "name",
    ) or create_for_week(
        week_ending=want,
        float_name=anchor.float,
        custodian_name=anchor.custodian_name or "Shiro",
        authorised_float=anchor.authorised_float or 50000,
    )
    if name == anchor.name:
        return anchor, False
    return frappe.get_doc("Petty Cash Sheet", name), True


@frappe.whitelist()
@_retrying
def quick_entry(sheet, kind, **fields):
    """Append ONE line to the child table of the sheet whose week CONTAINS ``txn_date``.

    ``sheet`` is only the STARTING POINT (it fixes the float): the transaction date
    decides the week. When the date falls outside that sheet's Sun–Sat span the entry
    is written to the correct week's sheet instead (created if it doesn't exist), and
    the sheet actually used comes back in ``sheet`` / ``rerouted`` so the client can
    follow. Callers that send no date keep the old behaviour and post to ``sheet``.

    Rejects Submitted/Approved/cancelled sheets. ``kind`` is one of:
    voucher | wage | commission | piecework | loan | bike | forklift | parking.
    Returns ``{sheet, rerouted, week_ending, entry_id, row_idx, kind, summary}``.
    """
    if not frappe.has_permission("Petty Cash Sheet", "read", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    kind = (kind or "").lower()
    if not fields.get("txn_date"):
        frappe.throw(_("A date is required for every entry."))
    txn_date = getdate(fields.get("txn_date"))

    anchor = frappe.get_doc("Petty Cash Sheet", sheet)
    doc, rerouted = _route_to_week(anchor, txn_date)

    if not frappe.has_permission("Petty Cash Sheet", "write", doc.name):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    _assert_can_write(doc)
    if doc.docstatus != 0 or doc.status in ("Submitted", "Approved"):
        if rerouted:
            frappe.throw(_(
                "That date belongs to the week ending {0}, which is closed — "
                "re-open it before adding entries."
            ).format(getdate(doc.week_ending).strftime("%d/%m/%Y")))
        frappe.throw(_("That week is closed — re-open it before adding entries."))

    if cint(doc.native_lines):
        # This week keeps its lines as entries: the line is created as one, with the
        # same checks, and there is no row to append.
        from vcl_finance.petty_cash import native
        line = native.create_line(doc, kind, fields, txn_date)
        doc.reload()
        return {
            "ok": True, "sheet": doc.name, "requested_sheet": anchor.name, "rerouted": rerouted,
            "week_ending": str(doc.week_ending), "kind": kind, "entry_id": line.name,
            "row_idx": None, "native": True, "summary": _summary(doc),
        }

    amount = flt(fields.get("amount"))
    target = None

    if kind == "voucher":
        cash_in = _truthy(fields.get("cash_in"))
        cat = (fields.get("category") or "").upper()
        if not cash_in and cat not in CATEGORY_CODES:
            frappe.throw(_("Pick a spend category (or tick Cash-in)."))
        # A spend voucher must have a receipt slip recorded — PC or ETR.
        if not cash_in and not (_truthy(fields.get("pc_received")) or _truthy(fields.get("etr_received"))):
            frappe.throw(_("Tick PC or ETR — a spend voucher needs a receipt slip recorded."))
        target = _first_blank(
            sorted(doc.vouchers, key=lambda x: x.row_idx or 0),
            lambda v: not _voucher_has_data(v),
        )
        if target is None:
            target = doc.append("vouchers", {"row_idx": _next_idx(doc.vouchers)})
        target.txn_date = txn_date
        target.voucher_no = fields.get("voucher_no") or ""
        target.recipient = fields.get("recipient") or ""
        target.notes = fields.get("notes") or ""
        target.pc_received = 1 if _truthy(fields.get("pc_received")) else 0
        target.etr_received = 1 if _truthy(fields.get("etr_received")) else 0
        target.cash_in = 1 if cash_in else 0
        target.category = "" if cash_in else cat
        target.amount = amount

    elif kind in ("wage", "commission", "piecework", "overtime"):
        entry_type = {
            "wage": "Wage", "commission": "Commission",
            "piecework": "Piecework", "overtime": "Overtime",
        }[kind]
        target = _first_blank(
            sorted(doc.wages_entries, key=lambda x: x.row_idx or 0),
            lambda w: not (w.recipient or "").strip() and not flt(w.amount)
            and not w.txn_date and not (w.reason or "").strip() and not (w.staff_id or "").strip(),
        )
        if target is None:
            target = doc.append("wages_entries", {"row_idx": _next_idx(doc.wages_entries)})
        target.txn_date = txn_date
        target.entry_type = entry_type
        target.recipient = fields.get("recipient") or ""
        target.staff_id = fields.get("staff_id") or ""
        target.reason = fields.get("reason") or ""
        target.amount = amount
        target.paye = 1 if _truthy(fields.get("paye")) else 0

    elif kind == "loan":
        target = _first_blank(
            sorted(doc.loan_entries, key=lambda x: x.row_idx or 0),
            lambda l: not (l.recipient or "").strip() and not flt(l.amount_issued)
            and not flt(l.amount_signed) and not l.txn_date
            and not (l.reason or "").strip() and not (l.staff_id or "").strip(),
        )
        if target is None:
            target = doc.append("loan_entries", {"row_idx": _next_idx(doc.loan_entries)})
        target.txn_date = txn_date
        target.recipient = fields.get("recipient") or ""
        target.staff_id = fields.get("staff_id") or ""
        target.reason = fields.get("reason") or ""
        target.amount_issued = amount
        # Quick-add: default signed = issued; refine in the grid.
        target.amount_signed = amount
        target.paye = 1 if _truthy(fields.get("paye")) else 0

    elif kind in ("bike", "forklift"):
        mkind = "Bike Fuel" if kind == "bike" else "Forklift"
        rows = sorted([m for m in doc.misc_entries if m.kind == mkind], key=lambda x: x.row_idx or 0)
        target = _first_blank(
            rows, lambda m: not flt(m.amount) and not m.txn_date and not (m.notes or "").strip(),
        )
        if target is None:
            target = doc.append("misc_entries", {"kind": mkind, "row_idx": _next_idx(rows)})
        target.txn_date = txn_date
        target.amount = amount
        target.notes = fields.get("notes") or ""

    elif kind == "parking":
        # Parking is a dated single entry: Date + Vehicle + Amount. `day_idx` is
        # derived from txn_date by the controller — the client never picks a weekday.
        # Older clients still POST day_idx; it is only honoured when no date came with it.
        vehicle = fields.get("vehicle")
        if vehicle not in VEHICLES:
            frappe.throw(_("Pick a vehicle for the parking entry."))
        row = {"vehicle": vehicle, "amount": amount, "slot": 1}
        if txn_date:
            row["txn_date"] = txn_date
        else:
            day = _legacy_day(fields.get("day_idx"))
            if day is None:
                frappe.throw(_("Pick a date for the parking entry."))
            row["day_idx"] = day
        target = doc.append("parking_entries", row)

    else:
        frappe.throw(_("Unknown entry kind '{0}'.").format(kind))

    doc.flags.ignore_permissions = False
    doc.save()

    summ = _summary(doc)
    return {
        "ok": True,
        # The sheet ACTUALLY written to — differs from the requested one when the
        # date routed the entry to another week. `summary` belongs to this sheet.
        "sheet": doc.name,
        "requested_sheet": anchor.name,
        "rerouted": rerouted,
        "week_ending": str(doc.week_ending),
        "kind": kind,
        "entry_id": target.name,
        "row_idx": getattr(target, "row_idx", None),
        "summary": summ,
    }


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


# ----------------------------------------------------------------------
# Cancel / reinstate a single feed entry (soft-void — ERPNext never deletes;
# the row stays on the sheet for audit, just excluded from totals + posting).
# ----------------------------------------------------------------------

_CHILD_TABLES = (
    "vouchers", "wages_entries", "loan_entries", "misc_entries", "parking_entries",
)


def _find_row(doc, entry_id):
    """Locate the child row (and its table) whose ``name`` == entry_id."""
    for table in _CHILD_TABLES:
        for r in (doc.get(table) or []):
            if r.name == entry_id:
                return table, r
    return None, None


def _open_sheet_for_write(sheet):
    """Shared guard: write permission + sheet not Submitted/Approved. Returns the doc."""
    if not frappe.has_permission("Petty Cash Sheet", "write", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if doc.docstatus != 0 or doc.status in ("Submitted", "Approved"):
        frappe.throw(_("That week is closed — re-open it before changing entries."))
    return doc


@frappe.whitelist(methods=["POST"])
@_retrying
def cancel_entry(sheet, entry_id, remark=None):
    """Soft-cancel (void) ONE child row — it is NOT deleted.

    Sets ``cancelled=1`` + ``cancelled_on`` + ``cancel_remark`` on the row, re-saves
    the parent (which re-derives totals, excluding cancelled rows) and returns the
    recomputed ``summary``. The row stays on the sheet for the audit trail.
    ``entry_id`` is the feed item's ``id`` (the child row name).
    """
    doc = _open_sheet_for_write(sheet)
    _assert_can_write(doc)
    if cint(doc.native_lines):
        from vcl_finance.petty_cash import native
        line = native.cancel_line(doc.name, entry_id, remark)
        doc.reload()
        return {"ok": True, "sheet": doc.name, "cancelled_in": native.KIND_TABLE.get(line.entry_kind),
                "entry_id": entry_id, "native": True, "summary": _summary(doc)}
    table, row = _find_row(doc, entry_id)
    if row is None:
        frappe.throw(_("That entry no longer exists on this sheet."))
    _assert_row_unlocked(row)

    row.cancelled = 1
    row.cancelled_on = frappe.utils.now_datetime()
    row.cancel_remark = (remark or "").strip() or None

    doc.flags.ignore_permissions = False
    doc.save()

    return {"ok": True, "sheet": doc.name, "cancelled_in": table,
            "entry_id": entry_id, "summary": _summary(doc)}


@frappe.whitelist(methods=["POST"])
@_retrying
def reinstate_entry(sheet, entry_id):
    """Reverse a mistaken cancel — clears ``cancelled`` so the row counts again."""
    doc = _open_sheet_for_write(sheet)
    _assert_can_write(doc)
    if cint(doc.native_lines):
        from vcl_finance.petty_cash import native
        line = native.reinstate_line(doc.name, entry_id)
        doc.reload()
        return {"ok": True, "sheet": doc.name, "reinstated_in": native.KIND_TABLE.get(line.entry_kind),
                "entry_id": entry_id, "native": True, "summary": _summary(doc)}
    table, row = _find_row(doc, entry_id)
    if row is None:
        frappe.throw(_("That entry no longer exists on this sheet."))
    _assert_row_unlocked(row)

    row.cancelled = 0
    row.cancelled_on = None
    row.cancel_remark = None

    doc.flags.ignore_permissions = False
    doc.save()

    return {"ok": True, "sheet": doc.name, "reinstated_in": table,
            "entry_id": entry_id, "summary": _summary(doc)}


# ----------------------------------------------------------------------
# Receipt attach (Frappe-native; the file is uploaded via /api/method/upload_file
# first, then its file_url is set on the voucher row's Attach Image field).
# ----------------------------------------------------------------------

@frappe.whitelist()
@_retrying
def attach_receipt(sheet, voucher_name=None, file_url=None, row_name=None):
    """Set a child row's ``receipt`` Attach Image to an uploaded File's url.

    Every child table carries ``receipt`` now, not just vouchers — a wage sheet, a
    loan chit and a parking ticket are all things somebody hands over paper for.
    So the row is looked up across all five tables via ``_find_row`` rather than
    ``doc.vouchers``.

    ``voucher_name`` is kept as the parameter name because the phone UI has been
    sending it since May; ``row_name`` is the honest alias for new callers. Either
    one names a child row.

    A LOCKED row can still take a receipt — but only from an Accounts Manager, via
    ``_assert_row_unlocked``'s bypass. That is deliberate: Finance reconciling a
    closed week is exactly when a missing slip turns up.
    """
    row_name = row_name or voucher_name
    if not row_name:
        frappe.throw(_("No row given to attach the receipt to."))
    if not frappe.has_permission("Petty Cash Sheet", "write", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    _assert_can_write(doc)
    if doc.docstatus != 0:
        frappe.throw(_("Sheet is submitted — cannot change receipts."))
    if cint(doc.native_lines):
        from vcl_finance.petty_cash import native
        line = native.attach_receipt_line(doc.name, row_name, file_url)
        return {"ok": True, "table": native.KIND_TABLE.get(line.entry_kind), "row_name": row_name,
                "voucher_name": row_name, "receipt": file_url, "native": True}
    table, row = _find_row(doc, row_name)
    if row is None:
        frappe.throw(_("That row no longer exists on this sheet."))
    _assert_row_unlocked(row)
    row.receipt = file_url
    doc.save()
    return {"ok": True, "table": table, "row_name": row_name,
            "voucher_name": row_name, "receipt": file_url}


@frappe.whitelist(methods=["POST"])
@_retrying
def close_week(sheet, cash_count_end=None, revision=None, balance_date=None, reviewed_on=None, components=None):
    if not _is_accounts_manager():
        frappe.throw("Only an Accounts Manager can close a week.", frappe.PermissionError)
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    # The same lock every change to a line takes, taken FIRST: a line cannot be changed
    # between the moment the totals are read here and the moment the week is closed.
    # For a native week the caller must also be looking at the current version.
    from vcl_finance.petty_cash import native
    locked = native.week_gate(sheet, revision)
    rows = None
    if cint(locked["native_lines"]):
        # The balance supplied for the float: one figure, or its components (cash in hand,
        # corporate M-Pesa, and the stated float portion of a personal M-Pesa balance).
        # Never negative; a negative BOOK balance is an exception to show, not a balance
        # to type in.
        rows, cash_count_end = native.clean_components(components, cash_count_end)
        if native.ensure_opening_count(sheet) is None:
            frappe.throw(
                _("Record the opening actual balance for this week first. It is kept next to the book opening balance."),
                title=_("Opening actual balance needed"))
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if cint(locked["native_lines"]):
        # TWO DATES. What the balance is OF: the opening of a day, normally the Monday after
        # the week ended. And when it was received and reviewed: normally the Wednesday.
        # The supplied figure is used as it is. Saturday's closing balance is reached from
        # it through the days in between only (Sunday, for a Monday balance); nothing dated
        # on or after the balance date is included.
        week_end = getdate(doc.week_ending)
        monday = frappe.utils.add_days(week_end, 2)
        effective = getdate(balance_date) if balance_date else monday
        reviewed = getdate(reviewed_on) if reviewed_on else max(getdate(frappe.utils.today()), effective)
        effective, reviewed = native._check_dates(
            effective, reviewed, frappe.utils.add_days(week_end, 1), frappe.utils.add_days(week_end, 7), _("The closing balance"))
        doc.balance_effective_on, doc.balance_reviewed_on = effective, reviewed
        doc.balance_components = json.dumps(rows) if rows else None
    doc.cash_count_end = flt(cash_count_end)
    doc.status = "Closed"
    doc.closed_by = frappe.session.user
    doc.closed_on = frappe.utils.now_datetime()
    doc.save()  # controller recomputes total_out/expected_close/variance, from the entries
    if cint(doc.native_lines):
        # What the count was reconciled against, kept so a later change to a line can be
        # seen against it. The count itself is never rewritten by a line change.
        # The week's cash is judged on the PHYSICAL chain: the closing count against the
        # opening count plus what came in less what went out. Any difference is a NEW
        # discrepancy for this week: no tolerance, and it must be acknowledged with a reason
        # before the week can be approved. The difference between the book and the tin that
        # was already there at the baseline is carried and shown; it is not asked about
        # again, and it is never used to absorb this week's discrepancy.
        cash = native.refresh_cash(doc.name)
        exception = bool(flt(cash["cash_discrepancy"], 2))
        frappe.db.set_value("Petty Cash Sheet", doc.name, {
            "expected_close_at_count": doc.expected_close, "cash_reconfirm_needed": 1 if exception else 0,
            "cash_confirmed_by": None, "cash_confirmed_on": None, "cash_confirm_reason": None,
            "cash_confirm_evidence": None, "cash_approval_kind": None}, update_modified=False)
        pos = native.cash_position(doc.name)
        fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})
        day = lambda d: frappe.format_value(d, {"fieldtype": "Date"})
        parts = "; ".join(
            f"{r['kind']} {fmt(r['float_amount'])}" + (f" of {fmt(r['balance'])}" if r["float_amount"] != r["balance"] else "")
            for r in (pos["components"] or []))
        doc.add_comment("Info", _(
            "Week closed. Balance supplied for this float: {count} as at the opening of {eff}, received and reviewed {rev}"
            "{parts}. Bridge to the week end: + paid {out_since} - received {in_since} between the week end and that date "
            "= actual balance at week end {at_end}. Expected {expected} (opening actual balance {opening} + received "
            "{received} - paid {paid}); this week's discrepancy {disc}. Book cash balance {book}; book against actual "
            "{diff}, of which historical difference carried {hist}.{exc}").format(
                count=fmt(pos["supplied_balance"]), eff=day(doc.balance_effective_on), rev=day(doc.balance_reviewed_on),
                parts=f" ({parts})" if parts else "", out_since=fmt(pos["paid_before_balance_date"]),
                in_since=fmt(pos["received_before_balance_date"]), at_end=fmt(pos["actual_balance_at_week_end"]),
                expected=fmt(pos["expected_actual_close"]), opening=fmt(pos["opening_actual_balance"]),
                received=fmt(doc.total_in), paid=fmt(doc.total_out), disc=fmt(pos["current_week_discrepancy"]),
                book=fmt(pos["book_cash_balance"]), diff=fmt(pos["book_vs_actual"]),
                hist=fmt(pos["historical_difference"] or 0),
                exc=_(" A new cash exception: it must be acknowledged before the week can be approved.") if exception else ""))
    out = {"name": doc.name, "status": doc.status, "expected_close": doc.expected_close,
           "cash_count_end": doc.cash_count_end, "variance": doc.variance,
           "lines_revision": cint(doc.lines_revision)}
    if cint(doc.native_lines):
        out["cash"] = native.cash_position(doc.name)
    return out


@frappe.whitelist(methods=["POST"])
@_retrying
def confirm_cash(sheet, revision=None, reason=None, evidence=None):
    """Finance acknowledges this week's cash exception, with a reason and any evidence.
    Acknowledging is not reconciling. See ``native.confirm_cash``."""
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    from vcl_finance.petty_cash import native
    return native.confirm_cash(sheet, revision, reason, evidence)


@frappe.whitelist(methods=["POST"])
@_retrying
def set_opening_count(sheet, amount=None, note=None, components=None, balance_date=None, reviewed_on=None):
    """Record the float's actual balance for the first week kept as entries: the supplied
    Monday opening balance (one figure or its components), the date it is of and the date
    it was reviewed. Sets the historical-difference baseline. See
    ``native.set_opening_count``."""
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    from vcl_finance.petty_cash import native
    return native.set_opening_count(sheet, amount, note, components, balance_date, reviewed_on)


@frappe.whitelist()
def cash_position(sheet):
    """Book cash balance, physical count, historical difference carried and this week's
    discrepancy, kept apart. Read-only."""
    frappe.has_permission("Petty Cash Sheet", "read", sheet, throw=True)
    from vcl_finance.petty_cash import native
    return native.cash_position(sheet)


@frappe.whitelist(methods=["POST"])
@_retrying
def approve_week(sheet, revision=None):
    """Second, distinct sign-off after Close — fully locks the week.

    Draft → Closed (reopenable) → Approved (still reopenable by an Accounts
    Manager, but treated as locked for every non-AM write path). Requires the week
    to already be Closed so the physical cash count / variance are settled first.
    """
    if not _is_accounts_manager():
        frappe.throw("Only an Accounts Manager can approve a week.", frappe.PermissionError)
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    from vcl_finance.petty_cash import native
    native.week_gate(sheet, revision)          # lock first; current version for a native week
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if doc.status != "Closed":
        frappe.throw("A week must be Closed before it can be approved.")
    if cint(doc.native_lines) and cint(doc.cash_reconfirm_needed):
        frappe.throw(
            "This week has a new discrepancy: the actual balance at the week end does not equal the opening "
            "actual balance plus received less paid. Acknowledge it, with the reason, before approving the week.",
            title="Cash exception not acknowledged")
    # The week is the second signature, so every entry in it must already carry the
    # first. Without this a week could be approved with entries still open, and the
    # posting run would then hold them back without the sheet ever saying so.
    # Entries are matched by the sheet they came from AND by the week and float they sit in,
    # so a native entry (no source sheet) or one dated into this week is not missed.
    base = {"cancelled": 0, "sync_state": ["!=", "Orphaned"],
            "status": ["not in", ["Approved", "Void"]], "amount": ["!=", 0]}
    # This sheet's own lines come from the one shared lookup (weeks.py): mirrored,
    # native and unlinked, each once.
    from vcl_finance.petty_cash import weeks
    own = {k: v for k, v in base.items() if k != "sync_state"}
    seen = {e["name"]: e for e in weeks.week_entries(
        doc.name, fields=["name", "amount", "origin_sheet", "week_sheet"], filters=own)}
    # KEPT AS IT WAS: a line that sits on ANOTHER sheet but is dated into this week
    # also holds this week's approval back. That is wider than "this sheet's lines"
    # and is legacy behaviour, left alone so existing weeks approve exactly as before.
    for e in frappe.get_all("Petty Cash Entry",
                            filters={**base, "week_ending": doc.week_ending, "float": doc.float},
                            fields=["name", "amount", "origin_sheet", "week_sheet"], limit_page_length=0):
        seen.setdefault(e["name"], e)
    open_entries = list(seen.values())
    if open_entries:
        # Say WHICH lines, and which sheet each one actually sits on: a line on another
        # sheet that is dated into this week blocks it too, and without the sheet that
        # reads as a mystery.
        def where(e):
            home = e.get("week_sheet") or e.get("origin_sheet") or doc.name
            return f"{e['name']} (KES {flt(e['amount']):,.2f})" + ("" if home == doc.name else f" on {home}")
        listed = "; ".join(where(e) for e in open_entries[:10])
        more = f"; and {len(open_entries) - 10} more" if len(open_entries) > 10 else ""
        frappe.throw(
            f"{len(open_entries)} entr{'y is' if len(open_entries) == 1 else 'ies are'} not approved yet "
            f"(KES {sum(flt(e['amount']) for e in open_entries):,.2f}). Approve every entry first, "
            f"then approve the week. Not approved: {listed}{more}.",
            title="Entries still open")
    doc.status = "Approved"
    doc.approved_by = frappe.session.user
    doc.approved_on = frappe.utils.now_datetime()
    doc.save()
    if cint(doc.native_lines):
        # Say which it was: the historical difference merely carried, or a new exception
        # for this week approved. They are different things and are recorded as such.
        pos = native.cash_position(doc.name)
        new_exception = bool(flt(pos["current_week_discrepancy"] or 0, 2))
        kind = "New cash exception approved" if new_exception else "Cash agrees; historical difference carried"
        frappe.db.set_value("Petty Cash Sheet", doc.name, "cash_approval_kind", kind, update_modified=False)
        fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})
        doc.add_comment("Info", (
            f"Week approved with a NEW cash exception of {fmt(pos['current_week_discrepancy'])} for this week, acknowledged by "
            f"{pos['acknowledged_by']}: {pos['acknowledged_reason']}. Historical difference carried: {fmt(pos['historical_difference'] or 0)}."
            if new_exception else
            f"Week approved. This week's actual balance agrees with its movements. Historical difference carried, unchanged: "
            f"{fmt(pos['historical_difference'] or 0)}."))
    out = {"name": doc.name, "status": doc.status,
           "approved_by": doc.approved_by, "approved_on": str(doc.approved_on),
           "expected_close": doc.expected_close,
           "cash_count_end": doc.cash_count_end, "variance": doc.variance}
    if cint(doc.native_lines):
        out["cash"] = native.cash_position(doc.name)
    return out


@frappe.whitelist(methods=["POST"])
@_retrying
def reopen_week(sheet):
    """Re-open a Closed OR Approved week back to Draft (Accounts-Manager-only).

    Clears both the close and the approval stamps so the audit trail reflects the
    reopen honestly.
    """
    if not _is_accounts_manager():
        frappe.throw("Only an Accounts Manager can reopen a week.", frappe.PermissionError)
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    from vcl_finance.petty_cash import native, weeks
    native.lock_sheet(sheet)                   # the same lock as a line change, taken first
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    discarded = None
    if cint(doc.native_lines):
        # Controlled regeneration. A submitted journal or a pushed QuickBooks expense
        # refuses the reopen; drafts and an unpushed staging row are discarded, for every
        # company, so no journal is left pointing at lines that can now change.
        from vcl_finance.petty_cash import weekpost
        discarded = weekpost.discard_drafts(doc.name)
        frappe.db.set_value("Petty Cash Sheet", doc.name, {
            "cash_reconfirm_needed": 0, "cash_confirmed_by": None, "cash_confirmed_on": None,
            "cash_confirm_reason": None, "cash_confirm_evidence": None, "cash_approval_kind": None,
            "cash_discrepancy": 0, "expected_close_at_count": 0}, update_modified=False)
        doc.reload()
    doc.status = "Draft"
    doc.closed_by = None
    doc.closed_on = None
    doc.approved_by = None
    doc.approved_on = None
    doc.save()
    return {"name": doc.name, "status": doc.status, "expected_close": doc.expected_close,
            "cash_count_end": doc.cash_count_end, "variance": doc.variance, "discarded": discarded}


@frappe.whitelist()
def range_report(from_date, to_date, float=None):
    if not _is_accounts_manager():
        frappe.throw("Accounts Manager only.", frappe.PermissionError)
    filters = {"week_ending": ["between", [from_date, to_date]]}
    if float:
        filters["float"] = float
    names = frappe.get_all("Petty Cash Sheet", filters=filters, pluck="name")
    by_cat, weeks = {}, []
    out = tin = wages = loans = parking = bike = forklift = 0.0
    for nm in names:
        d = frappe.get_doc("Petty Cash Sheet", nm)
        for v in d.vouchers:
            if v.cancelled: continue
            if v.cash_in: tin += flt(v.amount)
            else:
                out += flt(v.amount)
                if v.category:  # skip blank/None rows — would pollute by_cat with a None key
                    by_cat[v.category] = by_cat.get(v.category, 0.0) + flt(v.amount)
        for w in d.wages_entries:
            if not w.cancelled: out += flt(w.amount); wages += flt(w.amount)
        for l in d.loan_entries:
            if not l.cancelled: out += flt(l.amount_issued); loans += flt(l.amount_issued)
        for p in d.parking_entries:
            if not p.cancelled: out += flt(p.amount); parking += flt(p.amount)
        for m in d.misc_entries:
            if m.cancelled: continue
            out += flt(m.amount)
            if m.kind == "Forklift": forklift += flt(m.amount)
            else: bike += flt(m.amount)
        weeks.append({"name": d.name, "week_ending": str(d.week_ending), "float": d.float,
                      "expected_close": d.expected_close, "cash_count_end": d.cash_count_end,
                      "variance": d.variance, "status": d.status})
    return {"from_date": from_date, "to_date": to_date, "float": float,
            "total_out": out, "total_in": tin, "net": tin - out, "by_category": by_cat,
            "sections": {"wages": wages, "loans": loans, "parking": parking,
                         "bike": bike, "forklift": forklift},
            "weeks": sorted(weeks, key=lambda x: x["week_ending"])}


# ----------------------------------------------------------------------
# Petty Cash Analytics — Accounts-Manager-only aggregate view.
# Server enforces the role (never trust the client); cancelled rows are excluded
# from every spend figure but surfaced separately as "voided".
# ----------------------------------------------------------------------

_ANALYTICS_ROLES = {"Accounts Manager", "System Manager"}


def _assert_accounts_manager():
    if not (set(frappe.get_roles()) & _ANALYTICS_ROLES):
        frappe.throw(
            _("Petty Cash Analytics is restricted to Accounts Managers."),
            frappe.PermissionError,
        )


def _sheet_closing(sheet):
    """Cash carried out of a sheet: physical count if entered, else expected close."""
    cnt = flt(sheet.cash_count_end)
    return cnt if cnt else flt(sheet.expected_close)


@frappe.whitelist()
def petty_cash_analytics(period="8"):
    """Aggregate analytics across Petty Cash Sheets. Accounts-Manager-only.

    ``period`` = number of trailing weeks ("4"/"8"/"12") or "all". Cancelled rows
    are excluded from all spend figures; voided entries are reported separately.
    """
    _assert_accounts_manager()

    weeks = int(period) if str(period).isdigit() else None
    filters = {}
    if weeks:
        filters["week_ending"] = (">=", add_days(getdate(), -7 * weeks))

    sheets = frappe.get_all(
        "Petty Cash Sheet", filters=filters,
        fields=["name", "float", "week_ending", "week_no", "opening_balance",
                "expected_close", "cash_count_end", "variance", "status", "custodian_name"],
        order_by="week_ending asc",
    )

    cat = {c: 0.0 for c in CATEGORY_CODES}
    total_out = total_in = 0.0
    sections = {"wages": 0.0, "loans": 0.0, "parking": 0.0, "bike": 0.0, "forklift": 0.0}
    by_float = {}                 # float -> {out, in, current, week_ending}
    week_bucket = {}              # week_ending(str) -> {out, in, cat:{...}}
    variance_trend = []
    recipients = {}               # recipient -> spend
    voided_count = 0
    voided_value = 0.0
    voided_log = []
    voucher_spend_n = 0
    voucher_receipt_n = 0

    for s in sheets:
        we = str(s.week_ending)
        wk = week_bucket.setdefault(we, {"out": 0.0, "in": 0.0, "cat": {c: 0.0 for c in CATEGORY_CODES}})
        fl = by_float.setdefault(s.float, {"out": 0.0, "in": 0.0, "current": 0.0, "week_ending": None})
        # latest sheet per float drives the "current cash position"
        if fl["week_ending"] is None or we >= fl["week_ending"]:
            fl["week_ending"] = we
            fl["current"] = _sheet_closing(s)

        doc = frappe.get_doc("Petty Cash Sheet", s.name)

        def _void(amount, kind, label, date, remark, when):
            nonlocal voided_count, voided_value
            voided_count += 1
            voided_value += flt(amount)
            voided_log.append({
                "date": str(date) if date else we, "kind": kind, "label": label,
                "amount": flt(amount, 2), "remark": remark or "",
                "when": str(when) if when else None, "sheet": s.name,
                "float": s.float, "custodian": s.custodian_name or "",
            })

        for v in doc.vouchers:
            amt = flt(v.amount)
            if v.cancelled:
                if amt:
                    _void(amt, "voucher", CAT_LABEL.get(v.category or "OT", "Voucher"),
                          v.txn_date, v.cancel_remark, v.cancelled_on)
                continue
            if not amt:
                continue
            if v.cash_in:
                total_in += amt
                fl["in"] += amt
                wk["in"] += amt
            else:
                code = v.category if v.category in cat else "OT"
                cat[code] += amt
                wk["cat"][code] += amt
                total_out += amt
                fl["out"] += amt
                wk["out"] += amt
                voucher_spend_n += 1
                if v.pc_received or v.etr_received:
                    voucher_receipt_n += 1
                who = (v.recipient or "").strip()
                if who:
                    recipients[who] = recipients.get(who, 0.0) + amt

        for w in doc.wages_entries:
            amt = flt(w.amount)
            if w.cancelled:
                if amt:
                    _void(amt, "wage", w.entry_type or "Wage", w.txn_date, w.cancel_remark, w.cancelled_on)
                continue
            if amt:
                sections["wages"] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        for l in doc.loan_entries:
            amt = flt(l.amount_issued)
            if l.cancelled:
                if amt:
                    _void(amt, "loan", "Loan", l.txn_date, l.cancel_remark, l.cancelled_on)
                continue
            if amt:
                sections["loans"] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        for m in doc.misc_entries:
            amt = flt(m.amount)
            key = "bike" if m.kind == "Bike Fuel" else "forklift"
            if m.cancelled:
                if amt:
                    _void(amt, key, m.kind, m.txn_date, m.cancel_remark, m.cancelled_on)
                continue
            if amt:
                sections[key] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        for p in doc.parking_entries:
            amt = flt(p.amount)
            if p.cancelled:
                if amt:
                    _void(amt, "parking", f"Parking · {p.vehicle}", p.txn_date, p.cancel_remark, p.cancelled_on)
                continue
            if amt:
                sections["parking"] += amt; total_out += amt; fl["out"] += amt; wk["out"] += amt

        if flt(s.cash_count_end):
            variance_trend.append({
                "week_ending": we, "week_no": s.week_no, "float": s.float,
                "expected_close": flt(s.expected_close, 2),
                "cash_count_end": flt(s.cash_count_end, 2),
                "variance": flt(s.variance, 2),
            })

    weeks_sorted = sorted(week_bucket.keys())
    category_trend = {
        "weeks": weeks_sorted,
        "series": {c: [flt(week_bucket[w]["cat"][c], 2) for w in weeks_sorted] for c in CATEGORY_CODES},
        "out": [flt(week_bucket[w]["out"], 2) for w in weeks_sorted],
        "in": [flt(week_bucket[w]["in"], 2) for w in weeks_sorted],
    }
    top_recipients = sorted(
        ({"recipient": r, "total": flt(v, 2)} for r, v in recipients.items()),
        key=lambda x: -x["total"],
    )[:10]
    coverage = round(100.0 * voucher_receipt_n / voucher_spend_n, 1) if voucher_spend_n else None
    voided_log.sort(key=lambda x: (x["when"] or x["date"] or ""), reverse=True)

    return {
        "currency": "KES",
        "period": period,
        "weeks": len(weeks_sorted),
        "sheet_count": len(sheets),
        "from": weeks_sorted[0] if weeks_sorted else None,
        "to": weeks_sorted[-1] if weeks_sorted else None,
        "kpi": {
            "total_out": flt(total_out, 2),
            "total_in": flt(total_in, 2),
            "net": flt(total_in - total_out, 2),
            "voided_count": voided_count,
            "voided_value": flt(voided_value, 2),
            "receipt_coverage_pct": coverage,
            "voucher_spend_count": voucher_spend_n,
        },
        "by_category": [
            {"code": c, "label": CAT_LABEL.get(c, c), "total": flt(cat[c], 2)}
            for c in CATEGORY_CODES
        ],
        "category_trend": category_trend,
        "by_float": [
            {"float": k, "out": flt(v["out"], 2), "in": flt(v["in"], 2),
             "current": flt(v["current"], 2)}
            for k, v in sorted(by_float.items())
        ],
        "sections": {k: flt(v, 2) for k, v in sections.items()},
        "variance_trend": variance_trend,
        "top_recipients": top_recipients,
        "voided_log": voided_log[:50],
    }


@frappe.whitelist()
def range_report_xlsx(from_date, to_date, float=None):
    """Download the date-range report as an Excel workbook.

    Same numbers as ``range_report``, laid out the way Finance reads a week:
    categories across the top, days down the rows. One week gives Summary /
    Vouchers / Registers; several give a Range summary tab first.

    The workbook itself is built by ``report_xlsx``, which has no frappe import
    so the local ``pcreport`` CLI produces a byte-identical file.
    """
    if not _is_accounts_manager():
        frappe.throw(_("Accounts Manager only."), frappe.PermissionError)

    from vcl_finance.petty_cash import report_xlsx

    filters = {"week_ending": ["between", [from_date, to_date]]}
    if float:
        filters["float"] = float
    names = frappe.get_all("Petty Cash Sheet", filters=filters,
                           order_by="week_ending asc", pluck="name")
    if not names:
        frappe.throw(_("No petty cash sheet falls in {0} → {1}.").format(from_date, to_date))

    sheets = [frappe.get_doc("Petty Cash Sheet", nm).as_dict() for nm in names]
    checks = _report_checks(sheets)
    content = report_xlsx.build_workbook(
        sheets, from_date=from_date, to_date=to_date,
        source=get_url(), checks=checks)

    frappe.local.response.filename = report_xlsx.filename(sheets, from_date, to_date)
    frappe.local.response.filecontent = content
    frappe.local.response.type = "binary"


def _report_checks(sheets):
    """Data-quality lines for the workbook's Notes tab.

    The carry-forward chain is diffed BOTH ways: opening_balance is a
    creation-time snapshot that is never recomputed, so a week can tie to the
    one before it and still fail to carry into the one after.
    """
    from vcl_finance.petty_cash import report_xlsx

    checks = []
    for d in sheets:
        nm, we, fl = d.get("name"), d.get("week_ending"), d.get("float")
        days = set(report_xlsx.week_days(we))

        stray = [v.get("recipient") or v.get("row_idx") for v in (d.get("vouchers") or [])
                 if v.get("txn_date") and not v.get("cancelled")
                 and report_xlsx.as_date(v.get("txn_date")) not in days]
        checks.append((not stray,
                       f"{nm}: all dated rows fall inside the Sun–Sat span." if not stray else
                       f"{nm}: {len(stray)} row(s) dated outside the week span — "
                       + ", ".join(str(s) for s in stray[:5]) + "."))

        approved = d.get("status") == "Approved"
        checks.append((approved, f"{nm}: status is {d.get('status')}."
                       + ("" if approved else " Figures can still change until it is approved.")))

        counted = d.get("cash_count_end") or 0
        checks.append((bool(counted),
                       f"{nm}: physical cash count recorded." if counted else
                       f"{nm}: no physical cash count entered, so the variance figure is not meaningful."))

        close = counted or flt(d.get("expected_close"))
        prior = frappe.get_all("Petty Cash Sheet",
                               filters={"float": fl, "week_ending": ["<", we]},
                               fields=["name", "week_ending", "expected_close", "cash_count_end"],
                               order_by="week_ending desc", limit=1)
        if prior:
            p = prior[0]
            pc = flt(p.cash_count_end) or flt(p.expected_close)
            gap = flt(d.get("opening_balance")) - pc
            checks.append((abs(gap) < 1,
                           f"{nm}: opening ties to {p.name} (w/e {p.week_ending}) close of {pc:,.0f}."
                           if abs(gap) < 1 else
                           f"{nm}: opening {flt(d.get('opening_balance')):,.0f} does NOT tie to {p.name} "
                           f"close {pc:,.0f} — gap {gap:,.0f}. opening_balance is a creation-time "
                           "snapshot and is never recomputed."))
        nxt = frappe.get_all("Petty Cash Sheet",
                             filters={"float": fl, "week_ending": [">", we]},
                             fields=["name", "week_ending", "opening_balance"],
                             order_by="week_ending asc", limit=1)
        if nxt:
            n = nxt[0]
            gap = flt(n.opening_balance) - close
            if abs(gap) >= 1:
                checks.append((False,
                               f"{nm}: this week's close {close:,.0f} does NOT carry into {n.name} "
                               f"(w/e {n.week_ending}) opening {flt(n.opening_balance):,.0f} — gap {gap:,.0f}."))
    return checks


@frappe.whitelist(methods=["POST"])
@_retrying
def move_entry_to_week(entry, txn_date):
    """Move a line to the week that contains ``txn_date`` — the only way a line changes
    week. The line stays the same entry, whichever way either week keeps its lines.
    See ``native.move_line``."""
    from vcl_finance.petty_cash import native
    doc = frappe.get_doc("Petty Cash Entry", entry)
    sheet = doc.week_sheet or doc.origin_sheet
    if not sheet or not frappe.has_permission("Petty Cash Sheet", "write", sheet):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    return native.move_line(entry, txn_date)


@frappe.whitelist(methods=["POST"])
@_retrying
def edit_entry(sheet, entry_id, **fields):
    """Edit one line of a NATIVE week — what the entry page's Edit does there.

    (On a week that keeps rows the page writes the row on the sheet, as it always has.)
    Returns the same shape as ``quick_entry`` so the page can refresh from it.
    """
    doc = _open_sheet_for_write(sheet)
    _assert_can_write(doc)
    if not cint(doc.native_lines):
        frappe.throw(_("This week keeps its lines as rows. Edit the row on the sheet."))
    from vcl_finance.petty_cash import native
    for drop in ("cmd", "sheet", "entry_id"):
        fields.pop(drop, None)
    line = native.edit_line(doc.name, entry_id, fields)
    home = frappe.get_doc("Petty Cash Sheet", line.week_sheet or line.origin_sheet)
    return {"ok": True, "sheet": home.name, "requested_sheet": doc.name, "rerouted": home.name != doc.name,
            "entry_id": line.name, "native": True, "summary": _summary(home)}


def petty_cash_loan_rows():
    """Every live loan line across all weeks, for the print format's cumulative ledger.

    The ledger used to read the ``Petty Cash Loan Entry`` rows straight from the
    database. A week that keeps its lines as entries stores no such rows, so its loans
    would have dropped out of that week's ledger and every later one. This returns the
    stored rows AND the native loan entries, in the same shape, each with the sheet it
    belongs to as ``parent``.
    """
    fields = ["parent", "txn_date", "recipient", "staff_id", "reason", "amount_issued", "amount_signed", "paye"]
    rows = frappe.get_all(
        "Petty Cash Loan Entry", filters={"parenttype": "Petty Cash Sheet", "cancelled": 0},
        fields=fields + ["idx"], order_by="txn_date asc, idx asc", limit_page_length=0, ignore_permissions=True)
    for e in frappe.get_all(
            "Petty Cash Entry",
            filters={"entry_kind": "loan", "week_sheet": ("is", "set"), "cancelled": 0, "sync_state": ("!=", "Orphaned")},
            fields=["week_sheet", "txn_date", "recipient", "amount", "payload", "creation"],
            order_by="txn_date asc, creation asc", limit_page_length=0, ignore_permissions=True):
        try:
            extra = json.loads(e.payload or "{}")
        except ValueError:
            extra = {}
        rows.append(frappe._dict(
            parent=e.week_sheet, txn_date=e.txn_date, recipient=e.recipient, staff_id=extra.get("staff_id"),
            reason=extra.get("reason"), amount_issued=flt(e.amount), amount_signed=flt(extra.get("amount_signed")),
            paye=cint(extra.get("paye")), idx=9999))
    # Same order the ledger always had: by date (undated first, as the database sorts
    # them), then by row position. A stable sort, so stored rows keep their order exactly.
    rows.sort(key=lambda r: (str(r.txn_date or ""), cint(r.idx)))
    return rows


@frappe.whitelist(methods=["POST"])
@_retrying
def convert_week_to_rows(sheet):
    """Fallback: convert a week that keeps its lines as entries back to rows on the
    sheet, keeping every entry and everything attached to it. Accounts Manager only.
    See ``native.convert_week_to_rows``."""
    frappe.has_permission("Petty Cash Sheet", "write", sheet, throw=True)
    from vcl_finance.petty_cash import native
    return native.convert_week_to_rows(sheet)
