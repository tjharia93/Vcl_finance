"""Keypad entry wizard — `/petty-cash/entry?name=PCS-2026-0008`.

Phase 3 port of the prototype's one-entry-at-a-time wizard. Renders the keypad
stage + running feed; all writes go through the whitelisted API
(`vcl_finance.petty_cash.api.quick_entry` / `get_feed` / `attach_receipt`).

If no ``name`` is supplied and exactly one open (Draft) sheet exists, jump
straight into it; otherwise show a sheet picker.
"""
import json

import frappe
from frappe import _

from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import (
    VEHICLES, DAY_NAMES,
)
from vcl_finance.petty_cash.api import _feed_items, _summary

ENTRY_TYPES = [
    {"kind": "voucher",    "label": "Voucher",    "hint": "Cash spend, by category"},
    {"kind": "wage",       "label": "Wage",       "hint": "Weekly wage / casual"},
    {"kind": "commission", "label": "Commission", "hint": "Sales commission"},
    {"kind": "loan",       "label": "Loan",       "hint": "Staff advance"},
    {"kind": "bike",       "label": "Bike fuel",  "hint": "Motorbike fuel"},
    {"kind": "forklift",   "label": "Forklift",   "hint": "Forklift gas"},
    {"kind": "parking",    "label": "Parking",    "hint": "Per vehicle / day"},
]
CATEGORIES = [
    {"code": "TG", "label": "Transport-Goods",    "color": "var(--vcl-blue, #1F5FBF)"},
    {"code": "TE", "label": "Transport-Employee", "color": "var(--vcl-blue-mid, #3E7BD6)"},
    {"code": "SE", "label": "Spares / Eng",       "color": "var(--vcl-navy, #14264A)"},
    {"code": "OA", "label": "Office / Admin",     "color": "var(--vcl-sage, #6E8B7B)"},
    {"code": "FD", "label": "Food",               "color": "var(--vcl-amber, #B86B00)"},
    {"code": "GP", "label": "Geeprint",           "color": "#5B2C6F"},
    {"code": "OT", "label": "Other",              "color": "var(--muted, #8A909E)"},
]
CASH_IN = {"code": "IN", "label": "Cash-in", "color": "var(--vcl-green, #1B7A45)"}


def _open_sheets():
    rows = frappe.get_all(
        "Petty Cash Sheet",
        filters={"status": ("!=", "Approved"), "docstatus": ("<", 2)},
        fields=["name", "week_no", "week_ending", "float"],
        order_by="week_ending desc, float asc",
        limit=50,
    )
    out = []
    for r in rows:
        out.append({
            "name": r["name"],
            "label": f"Wk{r['week_no']} · {r['week_ending']} · {r['float']}",
        })
    return out


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.PermissionError)

    name = frappe.form_dict.get("name") or frappe.form_dict.get("sheet")
    opens = _open_sheets()

    if not name:
        if len(opens) == 1:
            frappe.local.flags.redirect_location = f"/petty-cash/entry?name={opens[0]['name']}"
            raise frappe.Redirect
        context.no_cache = 1
        context.title = "Add petty-cash entry"
        context.sheet = None
        context.open_sheets = opens
        return context

    if not frappe.has_permission("Petty Cash Sheet", "read", name):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    doc = frappe.get_doc("Petty Cash Sheet", name)
    # First render of a brand-new sheet: force the blank grid once.
    if doc.docstatus == 0 and not doc.vouchers:
        doc.ensure_grid()
        doc.save(ignore_permissions=True)
        doc = frappe.get_doc("Petty Cash Sheet", name)

    summary = _summary(doc)
    feed = _feed_items(doc)

    context.no_cache = 1
    context.title = f"Add entry — Wk {doc.week_no or '?'}"
    context.doc = doc
    context.sheet = doc
    context.open_sheets = opens
    context.entry_types = ENTRY_TYPES
    context.categories = CATEGORIES
    context.cash_in = CASH_IN
    context.vehicles = VEHICLES
    context.days = DAY_NAMES
    context.summary = summary
    # Bootstrap blob the JS hydrates from (avoids a first round-trip).
    context.wz_data = json.dumps({
        "sheet": doc.name,
        "status": doc.status,
        "opening_balance": doc.opening_balance or 0,
        "authorised_float": doc.authorised_float or 0,
        "feed": feed,
        "summary": summary,
        "days": DAY_NAMES,
        "vehicles": VEHICLES,
    })
    return context
