"""Pure logic for VAT on petty cash and for building a week's postings.

No Frappe import on purpose: everything here takes plain dicts and returns plain dicts,
so it can be unit tested without a bench (``python3 -m unittest`` from the repo root).
``vat.py`` and ``weekpost.py`` are the thin Frappe layers that read entries, call this,
and write the result.

VAT MODEL
---------
``amount`` on an entry is always the GROSS cash that left the tin. Finance records a
VAT treatment per entry; from it we derive ``vat_amount`` and ``net_amount`` so that
``net + vat == gross`` to the cent. The sheet's cash count never sees any of this.

* Standard 16%  -> VAT = gross * 16 / 116
* Petrol 8%     -> VAT = gross * 8 / 108
* Exempt, No VAT, Not applicable -> VAT = 0

Finance may type the VAT the receipt actually shows instead of the computed figure
(rounding on a till receipt rarely matches the formula). That is only allowed for a
treatment that carries a rate.

Wages and Loan entries can never carry VAT, so they take "Not applicable" without
anyone setting it. Everything else must be set explicitly before it can be posted:
a blank is "not reviewed yet", which is different from "reviewed, no VAT".

POSTING SHAPE
-------------
ERPNext: one draft journal per company/float/week. Debit lines are either SUMMARISED
(one line per expense account, the entries listed in the line's remark) or ONE PER
ENTRY (the original behaviour of post.py). VAT is one debit line to the company's VAT
account. Credit is the float's cash account for the gross total.

QuickBooks: one Expense (Purchase, PaymentType Cash) with GlobalTaxCalculation
"TaxInclusive": each line carries the GROSS for one (QuickBooks account, tax code)
pair and QuickBooks works the VAT out from the tax code. Never summarised across tax
codes, because that would change the VAT.
"""

from __future__ import annotations

# treatment -> rate and the QuickBooks purchase tax code id (QBO Tax Code.name).
# ids read from the live QBO Tax Code doctype on 08/10/2026:
#   3 "16.0% S", 12 "8.0% Petrol", 4 "Exempt Purchase", 10 "No VAT".
TREATMENTS = {
    "Standard 16%": {"rate": 0.16, "qbo_tax": "3"},
    "Petrol 8%": {"rate": 0.08, "qbo_tax": "12"},
    "Exempt": {"rate": 0.0, "qbo_tax": "4"},
    "No VAT": {"rate": 0.0, "qbo_tax": "10"},
    "Not applicable": {"rate": 0.0, "qbo_tax": "10"},
}
TREATMENT_ORDER = list(TREATMENTS)

# Entry types that can never carry VAT, so they need no treatment set by hand.
# Replenishment is a top-up of the float: money moving between our own accounts.
AUTO_NOT_APPLICABLE = {"Wages Entry", "Loan", "Replenishment"}

# Floats whose top-ups are posted to ERPNext (Dr the float, Cr the bank it came from).
# Hauz-Pay is a wallet topped up from the bank, so the transfer has to be booked
# somewhere and petty cash is the only place that books it. Cash-tin top-ups stay
# never-post. QuickBooks never gets a top-up from here.
TOPUP_FLOATS = {"Hauz-Pay"}


def is_topup(e: dict) -> bool:
    """A cash-in line on a float whose top-ups post (ERPNext only)."""
    return bool(int(e.get("cash_in") or 0)) and e.get("float") in TOPUP_FLOATS

# Input VAT lands here, per company.
VAT_ACCOUNT = {
    "Vimit Converters Limited": "VAT - VCL",
    "BAHATI VENTURES LIMITED": "VAT - BVL",
    "BAHATI INDUSTRIES LIMITED": "VAT - BIL",
}


class VatError(ValueError):
    """A VAT figure that cannot stand. The message is written for a person."""


def r2(x) -> float:
    return round(float(x or 0) + 0.0, 2)


def has_evidence(e: dict) -> bool:
    """Is there any receipt on the entry? VAT cannot be claimed on nothing."""
    return bool(e.get("receipt") or e.get("receipt_etr")
                or int(e.get("pc_received") or 0) or int(e.get("etr_received") or 0))


def split_vat(gross, treatment, vat_override=None):
    """(net, vat) for a gross amount. Raises VatError rather than guessing."""
    if treatment not in TREATMENTS:
        raise VatError(f"Unknown VAT treatment: {treatment!r}")
    gross = r2(gross)
    rate = TREATMENTS[treatment]["rate"]
    if vat_override in (None, ""):
        vat = r2(gross * rate / (1 + rate)) if rate else 0.0
    else:
        if not rate:
            raise VatError(f"{treatment} carries no VAT, so a VAT amount cannot be entered.")
        try:
            vat = r2(vat_override)
        except (TypeError, ValueError):
            raise VatError("VAT must be a number.")
        if vat < 0 or vat > gross:
            raise VatError("VAT must be between 0 and the amount paid.")
    return r2(gross - vat), vat


def vat_basis(gross, treatment):
    """What a stored VAT figure was worked out FROM. Stored beside the VAT so a later change
    to the amount (the mirror rewrites it when a sheet row changes) or to the treatment
    makes the stored figure visibly stale instead of quietly wrong."""
    return f"{treatment}|{r2(gross):.2f}"


def effective_vat(e: dict):
    """The VAT an entry will post with: ``(treatment, net, vat, problem)``.

    ``treatment`` is None while nobody has reviewed the entry; ``problem`` is a short
    reason the entry cannot post yet, or None.

    A stored VAT figure is only trusted while its ``vat_basis`` still matches the entry's
    current treatment and amount. If it does not, the figure is stale: it is NOT silently
    recomputed (the receipt may have shown something else), the entry is flagged and
    Finance sets it again. With no basis at all (treatment set by hand in the desk form),
    VAT is simply computed from the treatment.
    """
    gross = r2(e.get("amount"))
    t = (e.get("vat_treatment") or "").strip() or None
    if not t and (e.get("source_type") in AUTO_NOT_APPLICABLE or int(e.get("cash_in") or 0)):
        t = "Not applicable"
    if not t:
        return None, gross, 0.0, "VAT not reviewed"
    if t not in TREATMENTS:
        return t, gross, 0.0, f"Unknown VAT treatment {t}"
    rated = bool(TREATMENTS[t]["rate"])
    basis = (e.get("vat_basis") or "").strip()
    try:
        if basis:
            if basis != vat_basis(gross, t):
                return t, gross, 0.0, "VAT is out of date: the amount or treatment changed after it was set"
            net, vat = split_vat(gross, t, e.get("vat_amount") if rated else None)
        else:
            net, vat = split_vat(gross, t)
    except VatError as exc:
        return t, gross, 0.0, str(exc)
    if vat and not has_evidence(e):
        return t, net, vat, "VAT claimed with no receipt"
    return t, net, vat, None


def qbo_tax_code(treatment):
    return TREATMENTS[treatment]["qbo_tax"]


# ---------------------------------------------------------------------------
# ERPNext journal
# ---------------------------------------------------------------------------

def _ids(rows, limit=60):
    names = [r["name"] for r in rows]
    s = ", ".join(names[:limit])
    return s + (f" +{len(names) - limit} more" if len(names) > limit else "")


def build_erp_lines(lines, cash_account, vat_account, mode="grouped"):
    """Journal lines for one company and one float.

    ``lines`` are dicts: name, account, gross, net, vat, subject, txn_date.
    Returns (rows, totals). Each row: account, debit, credit, remark, entries.
    Raises VatError if the journal would not balance.
    """
    if mode not in ("grouped", "each"):
        raise VatError(f"Unknown line mode {mode!r}")
    # Top-ups (``cash_in``) run the other way: Dr the float, Cr the bank on the line.
    # They carry no VAT and are kept out of the spend totals.
    topups = [x for x in lines if x.get("cash_in")]
    for x in topups:
        if x.get("vat"):
            raise VatError(f"A top-up cannot carry VAT ({x['name']}).")
    lines = [x for x in lines if not x.get("cash_in")]
    rows = []
    if mode == "grouped":
        by = {}
        for ln in lines:
            by.setdefault(ln["account"], []).append(ln)
        for account in sorted(by):
            grp = by[account]
            net = r2(sum(x["net"] for x in grp))
            rows.append({
                "account": account, "debit": net, "credit": 0.0,
                "remark": f"{len(grp)} entr{'y' if len(grp) == 1 else 'ies'}: {_ids(grp)}",
                "entries": [x["name"] for x in grp],
            })
        vat_rows = [x for x in lines if x["vat"]]
        if vat_rows:
            rows.append({
                "account": vat_account, "debit": r2(sum(x["vat"] for x in vat_rows)), "credit": 0.0,
                "remark": f"Input VAT on {len(vat_rows)} entr{'y' if len(vat_rows) == 1 else 'ies'}: {_ids(vat_rows)}",
                "entries": [x["name"] for x in vat_rows],
            })
    else:
        for ln in lines:
            tag = f"{ln.get('txn_date') or ''} {ln.get('subject') or ''} [{ln['name']}]".strip()
            rows.append({"account": ln["account"], "debit": r2(ln["net"]), "credit": 0.0, "remark": tag, "entries": [ln["name"]]})
            if ln["vat"]:
                rows.append({"account": vat_account, "debit": r2(ln["vat"]), "credit": 0.0,
                             "remark": f"VAT {tag}", "entries": [ln["name"]]})
    gross = r2(sum(x["gross"] for x in lines))
    if lines or not topups:
        rows.append({"account": cash_account, "debit": 0.0, "credit": gross,
                     "remark": "Petty cash paid out", "entries": []})
    topup = r2(sum(x["gross"] for x in topups))
    if topups:
        if mode == "grouped":
            by = {}
            for ln in topups:
                by.setdefault(ln["account"], []).append(ln)
            for account in sorted(by):
                grp = by[account]
                rows.append({"account": account, "debit": 0.0, "credit": r2(sum(x["gross"] for x in grp)),
                             "remark": f"Top-up from bank, {len(grp)} entr{'y' if len(grp) == 1 else 'ies'}: {_ids(grp)}",
                             "entries": [x["name"] for x in grp]})
        else:
            for ln in topups:
                tag = f"{ln.get('txn_date') or ''} Top-up {ln.get('subject') or ''} [{ln['name']}]".strip()
                rows.append({"account": ln["account"], "debit": 0.0, "credit": r2(ln["gross"]),
                             "remark": tag, "entries": [ln["name"]]})
        rows.append({"account": cash_account, "debit": topup, "credit": 0.0,
                     "remark": f"Top-up received, {len(topups)} entr{'y' if len(topups) == 1 else 'ies'}: {_ids(topups)}",
                     "entries": [x["name"] for x in topups]})
    dr, cr = r2(sum(r["debit"] for r in rows)), r2(sum(r["credit"] for r in rows))
    if abs(dr - cr) > 0.005:
        raise VatError(f"Journal does not balance: debits {dr:,.2f}, credits {cr:,.2f}.")
    return rows, {"debit": dr, "credit": cr, "gross": gross,
                  "net": r2(sum(x["net"] for x in lines)), "vat": r2(sum(x["vat"] for x in lines)),
                  "topup": topup, "topups": len(topups)}


# ---------------------------------------------------------------------------
# QuickBooks expense
# ---------------------------------------------------------------------------

def etr_parts(ref):
    """(pin, etr) from ``supplier_ref``. The VAT screen stores ``PIN|CU invoice number``;
    a ref without the bar is a plain receipt number and comes back as (None, None)."""
    ref = (ref or "").strip()
    if "|" not in ref:
        return None, None
    pin, _, cu = ref.partition("|")
    return (pin.strip() or None), (cu.strip() or None)


def qbo_description(ln):
    """What a person reconciling QuickBooks reads on one line.

    ``dd/mm/yyyy · what kind · what it was / who was paid · notes · ETR <CU no> · PIN · PCE``.
    The ETR (CU invoice number) is what ties the line to the KRA record; the PCE number
    ties it back to the petty cash entry. Codes like "SE" never appear: ``kind`` is the
    category's name.
    """
    d = str(ln.get("txn_date") or "")[:10]
    parts = ["/".join(reversed(d.split("-"))) if d else ""]
    kind = (ln.get("kind") or "").strip()
    parts.append(kind)
    seen = {kind.lower()}
    for f in ("subject", "notes"):
        v = " ".join(str(ln.get(f) or "").split())
        if v and v.lower() not in seen:
            parts.append(v[:120])
            seen.add(v.lower())
    pin, etr = etr_parts(ln.get("supplier_ref"))
    ref = (ln.get("supplier_ref") or "").strip()
    if etr:
        parts.append(f"ETR {etr}")
    elif ref:
        parts.append(f"Receipt {ref}")
    if pin:
        parts.append(f"PIN {pin}")
    parts.append(ln["name"])
    return " · ".join(p for p in parts if p)[:4000]


def build_qbo_lines(lines, per_entry=False):
    """Group by (QuickBooks account, tax code). ``lines`` carry qbo_account and treatment.

    ``per_entry`` gives one line per entry instead, described by ``qbo_description``: that
    is what is SENT, so every line in QuickBooks names its date, purpose and ETR. The
    grouped form is the on-screen summary.
    """
    by = {}
    for ln in lines:
        key = (str(ln["qbo_account"]), qbo_tax_code(ln["treatment"]))
        if per_entry:
            key = (str(ln.get("txn_date") or ""), ln["name"]) + key
        by.setdefault(key, []).append(ln)
    out = []
    for key in sorted(by):
        acct, tax = key[-2], key[-1]
        grp = by[key]
        gross = r2(sum(x["gross"] for x in grp))
        rate = next((t["rate"] for t in TREATMENTS.values() if t["qbo_tax"] == tax and t["rate"]), 0.0)
        vat = r2(sum(x["vat"] for x in grp))
        vat_qbo = r2(gross * rate / (1 + rate)) if rate else 0.0
        desc = (qbo_description(grp[0]) if per_entry else
                f"{grp[0].get('label') or 'Petty cash'}: {len(grp)} entr{'y' if len(grp) == 1 else 'ies'} {_ids(grp, 25)}"[:4000])
        out.append({
            "qbo_account": acct, "tax_code": tax, "amount": gross,
            "vat": vat, "vat_qbo": vat_qbo, "vat_gap": r2(vat - vat_qbo),
            "description": desc,
            "entries": [x["name"] for x in grp],
        })
    return out


def build_qbo_payload(lines, petty_cash_account, week_ending, doc_number, private_note):
    """The Purchase object, tax-inclusive. None when there is nothing to send.

    On a TaxInclusive Purchase QuickBooks reads ``Amount`` as the NET and adds the tax on
    top unless the line also carries ``TaxInclusiveAmt`` (the gross). Sending the gross as
    ``Amount`` overstated Expense 54170 (week 10/10/2026) by its VAT, 552.00. So each line
    sends net = gross - VAT and the gross alongside, and QuickBooks' total is the cash paid.
    """
    if not lines:
        return None
    return {
        "PaymentType": "Cash",
        "AccountRef": {"value": str(petty_cash_account)},
        "TxnDate": str(week_ending),
        "DocNumber": doc_number,
        "PrivateNote": private_note,
        "GlobalTaxCalculation": "TaxInclusive",
        "Line": [{
            "DetailType": "AccountBasedExpenseLineDetail",
            "Amount": r2(ln["amount"] - ln.get("vat_qbo", 0.0)),
            "Description": ln["description"],
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": ln["qbo_account"]},
                "TaxCodeRef": {"value": ln["tax_code"]},
                "TaxInclusiveAmt": ln["amount"],
            },
        } for ln in lines],
    }
