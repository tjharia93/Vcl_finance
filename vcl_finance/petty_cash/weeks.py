"""Which entries belong to a week. THE one answer, for every reader.

Before this module each reader carried its own filter — ``origin_sheet`` in one place,
``week_ending`` + ``float`` in another, both in a third — and a native entry (one keyed
straight in as a Petty Cash Entry, with no sheet row behind it) was visible to some and
invisible to others. ``post.sheet_filters`` in particular matched on ``origin_sheet``
alone whenever a sheet existed, so a native line on a week that has a sheet would have
been left out of posting and VAT without a word.

An entry is exactly ONE of three kinds, decided by its links and nothing else:

* **mirrored** — ``origin_sheet`` is set. Copied from a row on that sheet. It belongs to
  that sheet whatever its own ``week_ending`` says: 523 live mirrored entries carry a
  week date different from their sheet's (May/June sheets stored on off-Saturdays, and
  lines dated into a neighbouring week), so the date is not the authority here.
* **native** — ``week_sheet`` is set and ``origin_sheet`` is not. ``week_sheet`` is the
  single authority; the entry controller keeps its date and float in agreement with it.
* **unlinked** — neither is set. None exist as at 15/11/2026 and the controller refuses
  new ones, but the leg stays as a safety net: such an entry is still returned under its
  own week date and float, flagged ``unlinked``, so it shows up to be fixed instead of
  silently dropping out of a total.

The kinds are mutually exclusive, so one entry can never be returned twice, and every
entry with a week is returned by exactly one leg.
"""

import frappe
from frappe.utils import getdate

SHEET = "Petty Cash Sheet"
ENTRY = "Petty Cash Entry"

MIRRORED, NATIVE, UNLINKED = "mirrored", "native", "unlinked"

NOT_SET = ("is", "not set")


def sheet_name_for(week_ending, float_name):
    """The sheet for a week and float, or None. One sheet per float per week."""
    if not (week_ending and float_name):
        return None
    return frappe.db.get_value(
        SHEET, {"week_ending": week_ending, "float": float_name, "docstatus": ("<", 2)}, "name")


def _week_dates(sheet):
    """The week date(s) an UNLINKED entry of this sheet could carry.

    An entry's ``week_ending`` is always normalised to the Saturday of its Sun–Sat week,
    while an import-era sheet may be stored on a Friday or Sunday. Both are accepted.
    """
    from vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet import week_saturday
    stored = getdate(sheet["week_ending"])
    return sorted({stored, week_saturday(stored)})


def _sheet_row(sheet):
    if isinstance(sheet, str):
        row = frappe.db.get_value(SHEET, sheet, ["name", "week_ending", "float"], as_dict=True)
        if not row:
            frappe.throw(frappe._("Petty Cash Sheet {0} does not exist.").format(sheet))
        return row
    return {"name": sheet.get("name"), "week_ending": sheet.get("week_ending"), "float": sheet.get("float")}


def week_entries(sheet, fields=None, filters=None, include_orphaned=False,
                 order_by="txn_date asc, creation asc"):
    """Every entry that belongs to ``sheet``, each exactly once.

    ``sheet``   a sheet name, or a sheet doc / dict with name, week_ending and float.
    ``fields``  as for ``frappe.get_all``; ``name`` is always included.
    ``filters`` extra AND conditions as a dict (e.g. ``{"cancelled": 0}``).
    ``include_orphaned``  Orphaned entries are dropped unless asked for. Callers that
        must keep an orphan still tied to a journal ask for them and decide themselves.

    Each returned row carries ``link_kind``: mirrored, native or unlinked.
    """
    s = _sheet_row(sheet)
    fields = list(fields or ["name"])
    for must in ("name", "sync_state"):
        if must not in fields:
            fields.append(must)
    extra = dict(filters or {})

    legs = (
        (MIRRORED, {"origin_sheet": s["name"]}),
        (NATIVE, {"week_sheet": s["name"], "origin_sheet": NOT_SET}),
        (UNLINKED, {"origin_sheet": NOT_SET, "week_sheet": NOT_SET,
                    "week_ending": ("in", _week_dates(s)), "float": s["float"]}),
    )
    out, seen = [], set()
    for kind, leg in legs:
        rows = frappe.get_all(ENTRY, filters={**extra, **leg}, fields=fields,
                              order_by=order_by, limit_page_length=0)
        for r in rows:
            if r["name"] in seen:      # cannot happen while the kinds stay exclusive
                continue
            if not include_orphaned and r.get("sync_state") == "Orphaned":
                continue
            seen.add(r["name"])
            r["link_kind"] = kind
            out.append(r)
    if order_by:
        out.sort(key=lambda r: (str(r.get("txn_date") or ""), str(r.get("creation") or ""), r["name"]))
    return out


def entries_for_week(week_ending, float_name, fields=None, filters=None, include_orphaned=False,
                     order_by="txn_date asc, creation asc"):
    """Entries for a week and float → ``(rows, sheet_name)``.

    When the week has a sheet this is ``week_entries`` on it. When it has none (lines
    keyed for a week nobody opened a sheet for) the entries are found by their own week
    date and float, exactly as ``post.sheet_filters`` always did, and ``sheet_name`` is
    None.
    """
    name = sheet_name_for(week_ending, float_name)
    if name:
        return week_entries(name, fields=fields, filters=filters,
                            include_orphaned=include_orphaned, order_by=order_by), name
    fields = list(fields or ["name"])
    for must in ("name", "sync_state"):
        if must not in fields:
            fields.append(must)
    rows = frappe.get_all(ENTRY, filters={**dict(filters or {}), "week_ending": week_ending, "float": float_name},
                          fields=fields, order_by=order_by, limit_page_length=0)
    if not include_orphaned:
        rows = [r for r in rows if r.get("sync_state") != "Orphaned"]
    for r in rows:
        r["link_kind"] = UNLINKED
    return rows, None


def native_entry_count(sheet_name):
    """How many native entries (any state, voided included) hang off this sheet."""
    return frappe.db.count(ENTRY, {"week_sheet": sheet_name})


def coverage_report():
    """Prove the lookup is a partition: every entry with a home is returned exactly once.

    Walks every sheet, collects ``week_entries`` (orphans included), and compares the
    union against the whole table. Returns counts plus the names of anything returned
    twice or never — both lists must be empty.
    """
    seen, twice = {}, []
    sheets = frappe.get_all(SHEET, filters={"docstatus": ("<", 2)}, pluck="name", limit_page_length=0)
    by_kind = {MIRRORED: 0, NATIVE: 0, UNLINKED: 0}
    for name in sheets:
        for r in week_entries(name, include_orphaned=True):
            if r["name"] in seen:
                twice.append((r["name"], seen[r["name"]], name))
            seen[r["name"]] = name
            by_kind[r["link_kind"]] += 1
    everything = set(frappe.get_all(ENTRY, pluck="name", limit_page_length=0))
    missing = sorted(everything - set(seen))
    return {
        "sheets": len(sheets),
        "entries_in_table": len(everything),
        "entries_returned": len(seen),
        "by_kind": by_kind,
        "returned_twice": twice,
        "never_returned": missing,
        "returned_but_not_in_table": sorted(set(seen) - everything),
    }


def live_amounts_locked(sheet):
    """``(amount, cash_in)`` for every live entry of a sheet, read UNDER LOCK.

    The same three legs as ``week_entries`` (mirrored, native, unlinked), cancelled and
    orphaned left out, as one locking read. A plain read inside a transaction sees the
    database as it was when that transaction first looked, so a total computed from it
    could miss a line another session committed a moment ago. A locking read always
    sees what is committed now (or, on a server with snapshot isolation, fails cleanly
    so the action can be retried). Totals that are about to be stored, or closed
    against, are computed from this.
    """
    s = _sheet_row(sheet)
    return frappe.db.sql(
        """select amount, cash_in from `tabPetty Cash Entry`
           where ifnull(cancelled, 0) = 0 and ifnull(sync_state, '') != 'Orphaned'
             and ( origin_sheet = %(sheet)s
                or (week_sheet = %(sheet)s and ifnull(origin_sheet, '') = '')
                or (ifnull(origin_sheet, '') = '' and ifnull(week_sheet, '') = ''
                    and week_ending in %(dates)s and `float` = %(float)s) )
           for update""",
        {"sheet": s["name"], "dates": tuple(_week_dates(s)), "float": s["float"]}, as_dict=True)
