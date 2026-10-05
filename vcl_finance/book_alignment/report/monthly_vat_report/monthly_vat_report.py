"""Monthly VAT report: what moved the VAT account in a month, and what is payable.

Read, do not compute: VAT is taken from the VAT row of each submitted invoice's tax
table (the figure already in the books), never recalculated from a rate. The
Importation template stacks duty, VAT and IDF on one invoice, so the invoice's
total taxes would overstate it; only the row posted to the VAT account counts.

Sections (effect on VAT payable: output +, input -):
  Output - Standard      Sales Invoices carrying VAT
  Output - Zero-rated    Sales Invoices with no VAT (exports, exempt) — taxable shown
  Input - Local          Purchase Invoices with VAT, not imports
  Input - Imports        Purchase Invoices on the Importation template, or a customs
                         entry number as the bill number (e.g. 26NBOIM410174734)
  Other VAT ledger       Journal / Payment entries etc. posted to the VAT account
                         (withholding offsets, adjustments, import VAT posted by hand)

A check row compares the net payable with the real movement on the VAT account in
the ledger. It should be zero; anything else is VAT posted a way this report does
not know about.

An import cleared by a bank journal with no tax line (no VAT account line) never
reaches the ledger or this report. That is the point of the check: post it as an
invoice with a VAT row if it has to appear.
"""
import calendar
import re

import frappe
from frappe import _
from frappe.utils import flt, getdate

MONTHS = [m for m in calendar.month_name if m]
IMPORT_TEMPLATE = "Importation"
# KRA customs entry number: 2-digit year, 3-4 letter station, "IM", digits
ENTRY_NO = re.compile(r"^\d{2}[A-Z]{3,4}IM\d+", re.I)

OUT_STD = "Output - Standard"
OUT_ZERO = "Output - Zero-rated / No VAT"
IN_LOCAL = "Input - Local"
IN_IMPORT = "Input - Imports"
IN_NONE = "Input - No VAT"
OTHER = "Other VAT ledger entries"
SECTIONS = [OUT_STD, OUT_ZERO, IN_LOCAL, IN_IMPORT, IN_NONE, OTHER]


def month_range(year, month_name):
    """First and last day of a month given its English name."""
    m = MONTHS.index(month_name) + 1
    last = calendar.monthrange(int(year), m)[1]
    return getdate(f"{int(year)}-{m:02d}-01"), getdate(f"{int(year)}-{m:02d}-{last:02d}")


def is_import(template, bill_no):
    return IMPORT_TEMPLATE in (template or "") or bool(ENTRY_NO.match(bill_no or ""))


def classify_sale(vat):
    return OUT_STD if flt(vat, 2) else OUT_ZERO


def classify_purchase(vat, template, bill_no):
    if is_import(template, bill_no):
        return IN_IMPORT
    return IN_LOCAL if flt(vat, 2) else IN_NONE


def execute(filters=None):
    f = frappe._dict(filters or {})
    if not (f.company and f.year and f.month):
        frappe.throw(_("Company, Year and Month are required."))
    start, end = month_range(f.year, f.month)

    vat_account = frappe.db.get_value(
        "Account",
        {"company": f.company, "account_name": "VAT", "account_type": "Tax", "is_group": 0},
        "name",
    )
    if not vat_account:
        frappe.throw(_("No VAT account found for {0}.").format(f.company))

    rows = []
    rows += _invoice_rows("Sales Invoice", "customer", None, vat_account, f.company, start, end)
    rows += _invoice_rows("Purchase Invoice", "supplier", "bill_no", vat_account, f.company, start, end)
    rows += _other_rows(vat_account, f.company, start, end)

    return _columns(), _layout(rows, vat_account, start, end, f.company), None, None, _summary(rows)


def _invoice_rows(doctype, party_field, bill_field, vat_account, company, start, end):
    fields = ["name", "posting_date", party_field, "taxes_and_charges", "base_net_total", "is_return"]
    if bill_field:
        fields.append(bill_field)
    invoices = frappe.get_all(
        doctype,
        filters={"docstatus": 1, "company": company, "posting_date": ["between", [start, end]]},
        fields=fields,
        order_by="posting_date, name",
    )
    if not invoices:
        return []

    is_sale = doctype == "Sales Invoice"
    tax_doctype = "Sales Taxes and Charges" if is_sale else "Purchase Taxes and Charges"
    # only purchase tax rows carry add_deduct_tax; a sales tax row is always an add
    tax_fields = ["parent", "base_tax_amount"] + ([] if is_sale else ["add_deduct_tax"])
    vat = {}
    for t in frappe.get_all(
        tax_doctype,
        filters={"parenttype": doctype, "parent": ["in", [i.name for i in invoices]], "account_head": vat_account},
        fields=tax_fields,
    ):
        sign = -1 if t.get("add_deduct_tax") == "Deduct" else 1
        vat[t.parent] = vat.get(t.parent, 0) + sign * flt(t.base_tax_amount)

    out = []
    for i in invoices:
        v = flt(vat.get(i.name), 2)
        bill = i.get(bill_field) if bill_field else None
        if is_sale:
            section, effect = classify_sale(v), v
        else:
            section, effect = classify_purchase(v, i.taxes_and_charges, bill), -v
        out.append(
            {
                "section": section,
                "posting_date": i.posting_date,
                "voucher_type": doctype,
                "voucher_no": i.name,
                "party": i.get(party_field),
                "bill_no": bill,
                "template": i.taxes_and_charges,
                "taxable": flt(i.base_net_total, 2),
                "vat": v,
                "effect": effect,
            }
        )
    return out


def _other_rows(vat_account, company, start, end):
    """Ledger lines on the VAT account that did not come from a sales or purchase invoice."""
    gl = frappe.get_all(
        "GL Entry",
        filters={
            "account": vat_account,
            "company": company,
            "is_cancelled": 0,
            "posting_date": ["between", [start, end]],
            "voucher_type": ["not in", ["Sales Invoice", "Purchase Invoice"]],
        },
        fields=["posting_date", "voucher_type", "voucher_no", "party", "debit", "credit", "remarks"],
        order_by="posting_date, voucher_no",
    )
    return [
        {
            "section": OTHER,
            "posting_date": g.posting_date,
            "voucher_type": g.voucher_type,
            "voucher_no": g.voucher_no,
            "party": g.party,
            "bill_no": (g.remarks or "")[:60],
            "template": None,
            "taxable": None,
            "vat": flt(g.credit - g.debit, 2),
            "effect": flt(g.credit - g.debit, 2),
        }
        for g in gl
    ]


def _layout(rows, vat_account, start, end, company):
    """Detail by section with a subtotal each, then net payable and the ledger check."""
    data = []
    for sec in SECTIONS:
        sec_rows = [r for r in rows if r["section"] == sec]
        if not sec_rows:
            continue
        data.append({"section": sec, "bold": 1})
        data += sec_rows
        data.append(
            {
                "section": sec,
                "voucher_no": _("Total — {0}").format(sec),
                "taxable": sum(flt(r["taxable"]) for r in sec_rows) if sec != OTHER else None,
                "vat": sum(r["vat"] for r in sec_rows),
                "effect": sum(r["effect"] for r in sec_rows),
                "bold": 1,
            }
        )
        data.append({})

    net = sum(r["effect"] for r in rows)
    ledger = _ledger_movement(vat_account, company, start, end)
    data.append({"voucher_no": _("NET VAT PAYABLE (+) / REFUNDABLE (−)"), "effect": net, "bold": 1})
    data.append({"voucher_no": _("Movement on {0} in the ledger").format(vat_account), "effect": ledger})
    data.append(
        {
            "voucher_no": _("Check: ledger less report (should be 0)"),
            "effect": flt(ledger - net, 2),
            "bold": 1,
        }
    )
    return data


def _ledger_movement(vat_account, company, start, end):
    gl = frappe.get_all(
        "GL Entry",
        filters={
            "account": vat_account,
            "company": company,
            "is_cancelled": 0,
            "posting_date": ["between", [start, end]],
        },
        fields=["debit", "credit"],
    )
    return flt(sum(g.credit - g.debit for g in gl), 2)


def _summary(rows):
    def tot(*secs):
        return sum(r["effect"] for r in rows if r["section"] in secs)

    out = tot(OUT_STD)
    inp = -tot(IN_LOCAL, IN_IMPORT, IN_NONE)
    other = tot(OTHER)
    return [
        {"label": _("Output VAT"), "value": out, "datatype": "Currency", "indicator": "Blue"},
        {"label": _("Input VAT"), "value": inp, "datatype": "Currency", "indicator": "Blue"},
        {"label": _("Other ledger entries"), "value": other, "datatype": "Currency", "indicator": "Grey"},
        {
            "label": _("Net payable"),
            "value": out - inp + other,
            "datatype": "Currency",
            "indicator": "Red" if out - inp + other > 0 else "Green",
        },
    ]


def _columns():
    return [
        {"fieldname": "section", "label": _("Category"), "fieldtype": "Data", "width": 190},
        {"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
        {"fieldname": "voucher_type", "label": _("Type"), "fieldtype": "Data", "width": 120},
        {
            "fieldname": "voucher_no",
            "label": _("Voucher"),
            "fieldtype": "Dynamic Link",
            "options": "voucher_type",
            "width": 190,
        },
        {"fieldname": "party", "label": _("Party"), "fieldtype": "Data", "width": 200},
        {"fieldname": "bill_no", "label": _("Bill / entry no."), "fieldtype": "Data", "width": 190},
        {"fieldname": "template", "label": _("Tax template"), "fieldtype": "Data", "width": 140},
        {"fieldname": "taxable", "label": _("Taxable (KES)"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "vat", "label": _("VAT (KES)"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "effect", "label": _("Effect on VAT payable"), "fieldtype": "Currency", "width": 150},
    ]
