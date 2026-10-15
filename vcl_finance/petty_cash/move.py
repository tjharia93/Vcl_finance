"""Move a petty cash line between Vouchers and Wages — the Frappe layer over ``move_core``.

* ``movable_rows(sheet)``: which rows of the sheet may move, and where. Read-only; the
  finance-dev grid shows a Move button only on the rows listed here.
* ``move_row(sheet, row_name, to_table)``: void the row and write it again in the other
  register, in one save of the sheet. See ``move_core`` for the rules and the mapping.

The move decides nothing. The new row lands like any hand-keyed line: unlocked, its
entry Unapproved, no account chosen. ``_assert_moved`` checks the mirror's result after
the save and throws (rolling the whole request back) unless the old entry is Void and the
new one is Unapproved and undecided.

Re-importing the statement still skips the line: ``hauzpay._live_state`` collects every
``src_ref`` on every Hauz-Pay sheet, voided rows included, and the new row carries the
same ``src_ref`` as the old one.
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

from vcl_finance.petty_cash import move_core as C
from vcl_finance.petty_cash.api import PETTY_APPROVERS, _find_row, _next_idx, _summary


def _assert_finance():
    """The same role set as approving a line (``api.PETTY_APPROVERS``), checked in the body
    because a whitelisted method is reachable whatever the doctype permissions say."""
    if not (set(frappe.get_roles()) & PETTY_APPROVERS):
        frappe.throw(_("Only Finance can move a petty cash line."), frappe.PermissionError)


def _entries(sheet_name, row_names):
    """origin_row -> its live entry. A Mirrored entry wins over an Orphaned one."""
    if not row_names:
        return {}
    out = {}
    for e in frappe.get_all("Petty Cash Entry",
                            filters={"origin_sheet": sheet_name, "origin_row": ("in", list(row_names))},
                            fields=["name", "origin_row", "status", "journal_entry", "qbo_queue",
                                    "posting_account", "sync_state"]):
        if e.origin_row not in out or e.sync_state == "Mirrored":
            out[e.origin_row] = e
    return out


@frappe.whitelist()
def movable_rows(sheet):
    """{row name: {"to": target table, "label": button text}} for the rows that may move now.

    Empty for anyone outside Finance, and for a week that is not a draft."""
    if not (set(frappe.get_roles()) & PETTY_APPROVERS):
        return {"allowed": False, "rows": {}}
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if not frappe.has_permission("Petty Cash Sheet", "write", doc=doc):
        return {"allowed": False, "rows": {}}
    rows = [(t, r) for t in C.TARGET for r in (doc.get(t) or [])]
    ents = _entries(doc.name, [r.name for _t, r in rows if r.name])
    out = {}
    for table, r in rows:
        target = C.TARGET[table]
        if C.refusal(table, r, target, doc.status, doc.docstatus, ents.get(r.name)) is None:
            out[r.name] = {"to": target, "label": "Move to " + C.LABEL[target].lower()}
    return {"allowed": True, "rows": out}


@frappe.whitelist(methods=["POST"])
def move_row(sheet, row_name, to_table):
    """Void ``row_name`` on ``sheet`` and write it again in ``to_table`` (vouchers / wages)."""
    _assert_finance()
    target = C.normalise_table(to_table)
    if not target:
        frappe.throw(_("A line can only be moved to Vouchers or to Wages."))
    doc = frappe.get_doc("Petty Cash Sheet", sheet)
    if not frappe.has_permission("Petty Cash Sheet", "write", doc=doc):
        frappe.throw(_("You may not change the petty cash sheet {0}.").format(sheet), frappe.PermissionError)
    table, row = _find_row(doc, row_name)
    if row is None:
        frappe.throw(_("That line is no longer on this sheet. Reload the week."))
    why = C.refusal(table, row, target, doc.status, doc.docstatus, _entries(doc.name, [row.name]).get(row.name))
    if why:
        frappe.throw(why)

    user = frappe.session.user
    new = _place(doc, target, C.moved_values(table, row, user))
    row.cancelled = 1
    row.cancelled_on = now_datetime()
    row.cancel_remark = C.cancel_remark(target, user)
    doc.save()

    entry = _assert_moved(doc.name, row.name, new.name)
    return {"ok": True, "sheet": doc.name, "moved_from": table, "moved_to": target,
            "old_row": row.name, "new_row": new.name, "entry": entry, "summary": _summary(doc)}


def _place(doc, target, values):
    """Fill the first blank scaffolding row of ``target``, or append one.

    A blank row that once carried an entry is skipped: the mirror would update that old
    entry (whatever its status) instead of creating the one new Unapproved entry."""
    rows = sorted(doc.get(target) or [], key=lambda r: r.row_idx or 0)
    used = set(_entries(doc.name, [r.name for r in rows if r.name]))
    r = next((x for x in rows if x.name not in used and C.is_reusable_blank(target, x)), None)
    if r is None:
        r = doc.append(target, {"row_idx": _next_idx(doc.get(target) or [])})
    for k, v in values.items():
        r.set(k, v)
    return r


def _assert_moved(sheet_name, old_row, new_row):
    """The mirror runs inside the save but swallows its own errors, so its result is
    checked here rather than assumed. Anything but (old Void, new Unapproved and
    undecided) throws, and the request rolls back: nothing moved."""
    ents = _entries(sheet_name, [old_row, new_row])
    old, new = ents.get(old_row), ents.get(new_row)
    if not old or old.status != "Void":
        frappe.throw(_("The moved line's old entry did not come out Void. Nothing was moved."))
    if not new or new.status != "Unapproved" or new.posting_account or new.journal_entry or new.qbo_queue:
        frappe.throw(_("The moved line's new entry did not come out Unapproved. Nothing was moved."))
    return new.name
