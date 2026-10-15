"""Move one petty cash line between the Voucher register and the Wages register.

Frappe-free: the rules and the field mapping only. ``move.py`` is the Frappe layer.

Why it exists (Tanuj, 15/10/2026): the Hauz Pay import routes a statement line to
Wages when the phone is on the staff roster and to Vouchers otherwise, and that route
is a suggestion. When it is wrong the only fix was to void the row and key it again,
which loses the ``src_ref`` link to the Hauz Pay transaction id, and with it the
import's protection against bringing the same line in twice.

A move is a void plus a re-key done by the server in one save:

* the source row is voided (``cancelled``), never deleted, with the remark
  "Moved to Wages by <user>" so the trail explains itself;
* a new row is written in the other register carrying the same date, amount,
  recipient, attachment and ``src_ref``, with the notes / reason carried across;
* the mirror then leaves the old ``Petty Cash Entry`` Void and creates one new
  Unapproved entry. Nothing is approved, posted or decided by the move.

Only a line nobody has signed or used yet may move: unlocked, not voided, not
reconciled, its entry still Unapproved with no journal and no QuickBooks queue row,
on a sheet that is not Submitted, Approved or Closed.
"""

VOUCHERS = "vouchers"
WAGES = "wages_entries"
TARGET = {VOUCHERS: WAGES, WAGES: VOUCHERS}
LABEL = {VOUCHERS: "Vouchers", WAGES: "Wages"}
FROZEN_STATUSES = ("Submitted", "Approved", "Closed")
CHARGES_REF = "HZP-CHARGES"     # hauzpay_core.CHARGES_REF: the week's lumped-charges row

_ALIASES = {
    "vouchers": VOUCHERS, "voucher": VOUCHERS, "petty cash voucher": VOUCHERS,
    "wages_entries": WAGES, "wages": WAGES, "wage": WAGES, "petty cash wages entry": WAGES,
}

# Field lengths on the target doctypes (Data = 140; voucher_no is Data with length 20
# as the import writes it).
_DATA = 140
_VOUCHER_NO = 20


def normalise_table(to_table):
    """'wages' / 'Wages' / 'wages_entries' -> 'wages_entries'. None if not a register a row can move to."""
    return _ALIASES.get(str(to_table or "").strip().lower())


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _txt(v):
    return str(v or "").strip()


def first_ref(src_ref):
    """The first token of a src_ref (a lumped-charges row carries several, one per line)."""
    return next((t for t in _txt(src_ref).replace(",", " ").split() if t), "")


def refusal(table, row, target, sheet_status, sheet_docstatus=0, entry=None):
    """Why this row may NOT move to ``target``, or None if it may.

    ``row``: the child row (dict-like). ``entry``: its Petty Cash Entry as a dict with
    status / journal_entry / qbo_queue, or None if the mirror has not made one yet (a
    line with no entry has been signed by nobody, so that does not block the move).
    """
    if table not in TARGET:
        return "Only a Voucher or a Wages line can be moved."
    if target not in TARGET:
        return "A line can only be moved to Vouchers or to Wages."
    if target == table:
        return f"This line is already on {LABEL[table]}."
    if int(sheet_docstatus or 0) != 0 or (sheet_status or "Draft") in FROZEN_STATUSES:
        return f"The week is {sheet_status or 'not a draft'}. Re-open it before moving a line."
    if _num(row.get("amount")) <= 0:
        return "Only a line with an amount can be moved."
    if row.get("cancelled"):
        return "This line is voided."
    if row.get("locked"):
        return "This line is approved (locked). Unlock it before moving it."
    if row.get("reconciled"):
        return "Finance has reconciled this line. Untick it before moving it."
    if table == VOUCHERS and row.get("cash_in"):
        return "A cash-in line (a top-up) cannot be a wage."
    if _txt(row.get("src_ref")).startswith(CHARGES_REF):
        return "The week's lumped Hauz Pay charges line cannot be a wage."
    if entry:
        if entry.get("status") != "Unapproved":
            return f"Its petty cash entry is {entry.get('status')}, not Unapproved."
        if entry.get("journal_entry"):
            return f"It is already in journal {entry.get('journal_entry')}."
        if entry.get("qbo_queue"):
            return "It is already queued for QuickBooks."
    return None


def cancel_remark(target, user):
    return f"Moved to {LABEL[target]} by {user}"


def _join(*parts):
    return " · ".join(p for p in (_txt(x) for x in parts) if p)


def moved_values(table, row, user):
    """The new row for the other register, as a dict of field values. Pure.

    Vouchers -> Wages
        txn_date, amount, recipient, receipt, src_ref   carried as they are
        entry_type                                      "Wage"
        reason (Data, 140)                              "Moved from Vouchers by <user>" · the voucher's notes
                                                        (the full notes stay on the voided voucher)
        voucher_no, category, pc/etr ticks              not carried: a wage has no such fields
    Wages -> Vouchers
        txn_date, amount, recipient, receipt, src_ref   carried as they are
        category                                        "OT", cash_in 0
        voucher_no                                      the Hauz Pay id (first src_ref token), as the import writes it
        notes                                           "Suggested: OT (moved from Wages by <user>, pick the category)"
                                                        · Was <entry_type> · Staff ID <id> · PAYE · the wage's reason
        pc/etr ticks                                    0: a wage's evidence is the signature, not a slip
    """
    base = {
        "txn_date": row.get("txn_date"),
        "amount": round(_num(row.get("amount")), 2),
        "recipient": _txt(row.get("recipient"))[:_DATA],
        "receipt": row.get("receipt") or None,
        "src_ref": row.get("src_ref") or None,
    }
    if table == VOUCHERS:
        base.update({
            "entry_type": "Wage",
            "reason": _join(f"Moved from Vouchers by {user}", row.get("notes"))[:_DATA],
        })
        return base
    base.update({
        "category": "OT",
        "cash_in": 0,
        "voucher_no": first_ref(row.get("src_ref"))[:_VOUCHER_NO],
        "notes": _join(
            f"Suggested: OT (moved from Wages by {user}, pick the category)",
            f"Was {row.get('entry_type')}" if row.get("entry_type") else "",
            f"Staff ID {_txt(row.get('staff_id'))}" if _txt(row.get("staff_id")) else "",
            "PAYE" if row.get("paye") else "",
            row.get("reason"),
        ),
    })
    return base


def is_reusable_blank(table, row):
    """A scaffolding row the new line may fill (the sheet pads each register to 18)."""
    if row.get("locked") or row.get("cancelled") or _num(row.get("amount")):
        return False
    fields = ("recipient", "txn_date", "notes", "voucher_no", "src_ref") if table == VOUCHERS \
        else ("recipient", "txn_date", "reason", "staff_id", "src_ref")
    if any(_txt(row.get(f)) for f in fields):
        return False
    return not (table == VOUCHERS and (row.get("cash_in") or row.get("pc_received") or row.get("etr_received")))
