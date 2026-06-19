"""Shadow → aligned posting layer (Phase 2).

The Petty Cash Sheet is the full *shadow* book — every line Shiro records. This
module classifies each postable line (which official account? post or park?) and
assembles the *aligned* **Draft Journal Entry** that ties to QBO / posts to
ERPNext.

Ported from the FastAPI prototype's ``routes/posting.py``. Two locked rules carry
over from the May review:
  - M-Pesa / bank-charge lines (detected in recipient/notes) → PARK (not posted).
  - Commission / Piecework wage lines → PARK (open accounting point).
Cash-in (refund) lines are float *funding*, never posted as an expense.

VCL standing rule (Tanuj): the Journal Entry is created with ``docstatus=0`` and
**never submitted** — a human reviews + submits it in ERPNext. Re-posting a sheet
updates the same Draft in place (idempotent), never duplicates.

A voucher category's ERP debit account comes from
``Petty Cash Category.gl_account``. Wages / loans / parking / bike / forklift use
the small ``CAT_DEFAULT`` map below. Any line whose ERP account can't be resolved
is **kept and flagged** ``NEEDS ERP a/c`` in the preview — never silently dropped.
"""
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

COMPANY = "Vimit Converters Limited"
PARK = "— PARK (not posted) —"
NEEDS_ERP = "⚠ NEEDS ERP a/c"

# Float → ERP cash/wallet account credited.
FLOAT_CASH_ACCOUNT = {
    "Cash": "1110 - Cash - VCL",
    "Hauz-Pay": "Hauz-Pay Wallet - VCL",
}

# Non-voucher line kinds → ERP debit account (best-guess defaults; the reviewer
# adjusts in ERPNext). Voucher categories are NOT here — they resolve through
# Petty Cash Category.gl_account.
CAT_DEFAULT = {
    "wage": "5120-D - Production Salaries - VCL",       # Weekly Wages / Overtime
    "overtime": "5120-D - Production Salaries - VCL",
    "loan": "1610 - Employee Advances - VCL",           # asset/receivable, not expense
    "parking": "5240.2 - Parking - Deliveries - VCL",
    "bike": "5240.1 - Fuel - Motorbikes - VCL",
    "forklift": "5240.5 - Fuel - Forklift - VCL",
}

# Fallback ERP accounts for voucher categories WITHOUT a Petty Cash Category.gl_account.
# Only used when the master leaves gl_account blank; if neither resolves, the line is
# flagged NEEDS ERP a/c (kept, not dropped). Mirrors the prototype's OFFICIAL_TO_ERP.
VOUCHER_CAT_FALLBACK = {
    "TG": "5214.1 - Customer Deliveries - Transporter - VCL",   # Transport — Goods
    "TE": "5216 - Travel Expenses - VCL",                       # Transport — Employee
    "SE": "5240.4 - Vehicle Maintenance - VCL",                 # Spares / Engineering
    "OA": "5206 - Legal Expenses - VCL",                        # Office / Admin
    "FD": "5208.1 - Staff Welfare: Tea, Milk, Water, Snacks - VCL",  # Food
    "GP": "5211 - Print and Stationery - VCL",                  # Geeprint
    "OT": "5240.2 - Parking - Deliveries - VCL",                # Other (vehicle running)
}

# Keywords that mean "this is an M-Pesa / bank transaction charge" → park it.
_CHARGE_KEYWORDS = (
    "transaction charge", "mpesa transaction", "m-pesa charge",
    "bank charge", "mpesa charge", "transaction fee", "withdrawal charge",
)


def _is_charge(*texts):
    blob = " ".join((t or "") for t in texts).lower()
    return any(k in blob for k in _CHARGE_KEYWORDS)


def _category_gl_account(code):
    """ERP account for a voucher category: master gl_account, else fallback map."""
    if not code:
        return None
    gl = frappe.db.get_value("Petty Cash Category", code, "gl_account")
    if gl:
        return gl
    return VOUCHER_CAT_FALLBACK.get(code)


def _explode(sheet):
    """Explode a sheet into postable lines.

    Returns a list of dicts: {src_key, memo, amount, erp_account, post, reason}.
    ``post`` False → parked (with a ``reason``). Parked lines are kept for the
    preview but never reach the JE debits.
    """
    lines = []

    # --- Vouchers (one line per row, by category) ---
    for v in sorted(sheet.vouchers, key=lambda x: x.row_idx or 0):
        amt = flt(v.amount, 2)
        if not amt:
            continue
        who = (v.recipient or "").strip() or (v.notes or "").strip()
        if v.cash_in:
            # Cash-in is float funding, not an expense — never posted.
            continue
        code = v.category or "OT"
        if _is_charge(v.recipient, v.notes) and code in ("OT", "OA"):
            lines.append({
                "src_key": f"V{v.name}", "memo": f"[{code}] {who}".strip(),
                "amount": amt, "erp_account": None, "post": False,
                "reason": "M-Pesa / bank charge — locked rule (parked)",
            })
            continue
        erp = _category_gl_account(code)
        lines.append({
            "src_key": f"V{v.name}", "memo": f"[{code}] {who}".strip(),
            "amount": amt, "erp_account": erp, "post": True,
            "reason": None if erp else "No gl_account on Petty Cash Category",
        })

    # --- Wages (by entry_type; Commission/Piecework parked) ---
    for w in sorted(sheet.wages_entries, key=lambda x: x.row_idx or 0):
        amt = flt(w.amount, 2)
        if amt <= 0:
            continue
        t = (w.entry_type or "Wage")
        memo = (w.recipient or "").strip()
        if w.reason:
            memo = f"{memo} · {w.reason}".strip(" ·")
        if t in ("Commission", "Piecework"):
            lines.append({
                "src_key": f"W{w.name}", "memo": f"[{t}] {memo}".strip(),
                "amount": amt, "erp_account": None, "post": False,
                "reason": f"{t} parked — locked rule",
            })
            continue
        erp = CAT_DEFAULT["overtime"] if t == "Overtime" else CAT_DEFAULT["wage"]
        lines.append({
            "src_key": f"W{w.name}", "memo": f"[{t}] {memo}".strip(),
            "amount": amt, "erp_account": erp, "post": True, "reason": None,
        })

    # --- Loans (only the cash issued leaves the float) ---
    for l in sorted(sheet.loan_entries, key=lambda x: x.row_idx or 0):
        issued = flt(l.amount_issued, 2)
        if issued <= 0:
            continue
        memo = (l.recipient or "").strip()
        if l.reason:
            memo = f"{memo} · {l.reason}".strip(" ·")
        signed = flt(l.amount_signed, 2)
        if signed and abs(signed - issued) > 0.005:
            memo = f"{memo} (signed-off {signed:,.0f})".strip()
        lines.append({
            "src_key": f"L{l.name}", "memo": f"[LOAN] {memo}".strip(),
            "amount": issued, "erp_account": CAT_DEFAULT["loan"], "post": True, "reason": None,
        })

    # --- Parking (aggregated) ---
    pk = flt(sum(flt(p.amount) for p in sheet.parking_entries), 2)
    if pk:
        lines.append({
            "src_key": "PARKING", "memo": "Parking — all vehicles/days",
            "amount": pk, "erp_account": CAT_DEFAULT["parking"], "post": True, "reason": None,
        })

    # --- Bike fuel / Forklift (aggregated) ---
    bike = flt(sum(flt(m.amount) for m in sheet.misc_entries if m.kind == "Bike Fuel"), 2)
    if bike:
        lines.append({
            "src_key": "BIKE", "memo": "Bike fuel",
            "amount": bike, "erp_account": CAT_DEFAULT["bike"], "post": True, "reason": None,
        })
    fork = flt(sum(flt(m.amount) for m in sheet.misc_entries if m.kind == "Forklift"), 2)
    if fork:
        lines.append({
            "src_key": "FORKLIFT", "memo": "Forklift gas",
            "amount": fork, "erp_account": CAT_DEFAULT["forklift"], "post": True, "reason": None,
        })

    return lines


def _assemble(sheet):
    """Group postable lines by ERP account → JE debit rows + parked list + flags.

    Returns (debits, parked, total, unmapped) where:
      - debits = [{account, debit, needs_account, memo_count}], grouped by account
        (lines missing an account collapse under NEEDS_ERP so they're visible).
      - parked = [{memo, amount, reason}]
      - total  = sum of all posted debits (= the credit to cash)
      - unmapped = [{memo, amount}] lines with no ERP account (kept, flagged)
    """
    lines = _explode(sheet)
    grouped = defaultdict(lambda: {"debit": 0.0, "count": 0})
    parked = []
    unmapped = []
    for ln in lines:
        if not ln["post"]:
            parked.append({"memo": ln["memo"], "amount": ln["amount"], "reason": ln["reason"]})
            continue
        acct = ln["erp_account"]
        if not acct:
            unmapped.append({"memo": ln["memo"], "amount": ln["amount"]})
            acct = NEEDS_ERP
        g = grouped[acct]
        g["debit"] = flt(g["debit"] + ln["amount"], 2)
        g["count"] += 1

    debits = [
        {
            "account": acct,
            "debit": flt(g["debit"], 2),
            "needs_account": acct == NEEDS_ERP,
            "memo_count": g["count"],
        }
        for acct, g in grouped.items()
    ]
    debits.sort(key=lambda d: (d["needs_account"], -d["debit"]))
    total = flt(sum(d["debit"] for d in debits), 2)
    return debits, parked, total, unmapped


def build_je_preview(sheet):
    """JSON-friendly preview of the Draft JE this sheet would generate.

    Does NOT touch ERPNext. ``ready`` is False when any line lacks an ERP account.
    """
    debits, parked, total, unmapped = _assemble(sheet)
    cash = FLOAT_CASH_ACCOUNT.get(sheet.float, FLOAT_CASH_ACCOUNT["Cash"])
    return {
        "sheet": sheet.name,
        "float": sheet.float,
        "week_no": sheet.week_no,
        "posting_date": str(sheet.week_ending) if sheet.week_ending else None,
        "cash_account": cash,
        "debits": debits,
        "credit": {"account": cash, "credit": total},
        "total": total,
        "parked": parked,
        "unmapped": unmapped,
        "ready": len(unmapped) == 0,
        "existing_je": frappe.db.get_value(
            "Journal Entry", {"cheque_no": sheet.name, "docstatus": ("<", 2)}, "name"
        ),
    }


def _user_remark(sheet):
    return f"Petty cash {sheet.float} wk{sheet.week_no or '?'} — {sheet.name} (shadow→aligned)"


@frappe.whitelist()
def preview_journal_entry(sheet_name):
    """Whitelisted JE preview (no write). Used by the posting UI / reviewer."""
    if not frappe.has_permission("Petty Cash Sheet", "read", sheet_name):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    sheet = frappe.get_doc("Petty Cash Sheet", sheet_name)
    return build_je_preview(sheet)


@frappe.whitelist()
def make_draft_journal_entry(sheet_name):
    """Create (or refresh) the **Draft** Journal Entry for a sheet.

    - Balanced: Dr each ERP expense account (grouped) / Cr the float cash account.
    - ``docstatus=0`` — NEVER submitted (VCL standing rule). The reviewer submits
      it in ERPNext after checking, especially any ``NEEDS ERP a/c`` lines.
    - Idempotent: a prior Draft for this sheet (tagged via ``cheque_no = sheet.name``)
      is rewritten in place rather than duplicated. A SUBMITTED JE is never touched —
      we raise instead so we don't silently fork the books.
    - Lines whose ERP account can't be resolved are kept under the ``NEEDS ERP a/c``
      placeholder account so the figure is visible and the reviewer can't miss it.
    """
    if not frappe.has_permission("Petty Cash Sheet", "write", sheet_name):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    sheet = frappe.get_doc("Petty Cash Sheet", sheet_name)
    debits, parked, total, unmapped = _assemble(sheet)

    if total <= 0:
        frappe.throw(_("Nothing to post — this sheet has no postable spend."))

    cash = FLOAT_CASH_ACCOUNT.get(sheet.float, FLOAT_CASH_ACCOUNT["Cash"])

    # Find any existing JE we previously created for this sheet.
    existing = frappe.db.get_value(
        "Journal Entry", {"cheque_no": sheet_name}, ["name", "docstatus"], as_dict=True
    )
    if existing and existing.docstatus == 1:
        frappe.throw(_(
            "Journal Entry {0} for this sheet is already submitted — cancel/amend it "
            "in ERPNext before re-posting."
        ).format(existing.name))

    if existing:
        je = frappe.get_doc("Journal Entry", existing.name)
        je.set("accounts", [])
    else:
        je = frappe.new_doc("Journal Entry")

    je.voucher_type = "Journal Entry"
    je.company = COMPANY
    je.posting_date = sheet.week_ending
    je.cheque_no = sheet_name          # back-link tag for idempotency
    je.cheque_date = sheet.week_ending
    je.user_remark = _user_remark(sheet)

    for d in debits:
        je.append("accounts", {
            "account": d["account"],
            "debit_in_account_currency": d["debit"],
            "credit_in_account_currency": 0,
        })
    je.append("accounts", {
        "account": cash,
        "debit_in_account_currency": 0,
        "credit_in_account_currency": flt(total, 2),
    })

    # Save as DRAFT only. Never submit.
    je.flags.ignore_permissions = True
    je.save()

    # Store the back-link on the sheet without re-triggering submit logic.
    if frappe.db.has_column("Petty Cash Sheet", "journal_entry"):
        frappe.db.set_value("Petty Cash Sheet", sheet_name, "journal_entry", je.name,
                            update_modified=False)

    return {
        "journal_entry": je.name,
        "docstatus": je.docstatus,        # always 0
        "total": flt(total, 2),
        "cash_account": cash,
        "debit_lines": len(debits),
        "parked": parked,
        "unmapped": unmapped,
        "ready": len(unmapped) == 0,
    }
