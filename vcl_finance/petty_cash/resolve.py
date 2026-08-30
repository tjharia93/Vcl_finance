"""Where a petty cash entry would post — the single resolver.

One function answers "what happens to this line", and both consumers call it: the
approval screen, so the person signing can see the route before they sign, and the
posting run later, so what was shown is what happens. Two resolvers would drift, and
the drift would only surface as a journal that disagrees with the screen somebody
approved from.

Resolution is `Posting Map` and nothing else. The old paths — `Petty Cash Category.
gl_account` and the hard-coded fallback tables in `posting.py` — are deliberately not
consulted: three places an account can come from is how a line ends up posted to an
account nobody chose.

Five outcomes, and only one of them posts:

    posts        an approved map row with an account
    never        an approved map row marked never_post (commission, bank charges)
    unmapped     no map row for this (source_type, source_key) pair
    unapproved   a row exists but nobody has approved it
    void         the line is cancelled

`unmapped` is the loud one. It is not an error in the entry — it is the map being
incomplete, and the approval screen says so per line so the gap is visible weekly
rather than discovered on the first posting run.
"""

import frappe

COMPANY = "Vimit Converters Limited"

# What a resolution can say. Only POSTS reaches a journal.
POSTS = "posts"
NEVER = "never"
UNMAPPED = "unmapped"
UNAPPROVED = "unapproved"
VOID = "void"


def _map_row(company, source_type, source_key):
    """The map row for a pair, preferring an exact key, then the family default.

    A route like Parking is mapped once for every vehicle rather than once per plate,
    so an exact-key miss falls back to the row with an empty key marked `is_default`.
    """
    if not source_type:
        return None
    exact = frappe.get_all(
        "Posting Map",
        filters={"company": company, "source_type": source_type,
                 "source_key": (source_key or "")},
        fields=["name", "erp_account", "qbo_account", "qbo_tax_code", "erp_party_type",
                "approved", "never_post", "never_post_reason"],
        limit_page_length=1,
    )
    if exact:
        return exact[0]
    default = frappe.get_all(
        "Posting Map",
        filters={"company": company, "source_type": source_type,
                 "source_key": "", "is_default": 1},
        fields=["name", "erp_account", "qbo_account", "qbo_tax_code", "erp_party_type",
                "approved", "never_post", "never_post_reason"],
        limit_page_length=1,
    )
    return default[0] if default else None


def resolve(entry, company=COMPANY):
    """Resolve one entry (a dict or a Document) to its route.

    Returns ``{outcome, erp_account, qbo_account, tax_code, reason, map_row}``.
    Never raises and never guesses: an unresolvable line comes back as ``unmapped``
    with the pair named, which is more useful than a default account would be.
    """
    get = entry.get if isinstance(entry, dict) else (lambda k, d=None: entry.get(k, d))

    if get("cancelled"):
        return {"outcome": VOID, "erp_account": None, "qbo_account": None,
                "tax_code": None, "reason": "Voided — never posts", "map_row": None}

    source_type = get("source_type")
    source_key = get("source_key") or ""
    row = _map_row(company, source_type, source_key)

    pair = f"{source_type or '?'} · {source_key or '—'}"
    if not row:
        return {"outcome": UNMAPPED, "erp_account": None, "qbo_account": None,
                "tax_code": None, "reason": f"No account mapped for {pair}", "map_row": None}

    if row.get("never_post"):
        return {"outcome": NEVER, "erp_account": None, "qbo_account": None,
                "tax_code": None,
                "reason": row.get("never_post_reason") or "Deliberately kept out of the books",
                "map_row": row["name"]}

    if not row.get("approved"):
        return {"outcome": UNAPPROVED, "erp_account": row.get("erp_account"),
                "qbo_account": row.get("qbo_account"), "tax_code": row.get("qbo_tax_code"),
                "reason": f"The mapping for {pair} has not been approved", "map_row": row["name"]}

    if not row.get("erp_account"):
        return {"outcome": UNMAPPED, "erp_account": None, "qbo_account": None,
                "tax_code": None,
                "reason": f"The mapping for {pair} carries no account", "map_row": row["name"]}

    return {"outcome": POSTS, "erp_account": row.get("erp_account"),
            "qbo_account": row.get("qbo_account"), "tax_code": row.get("qbo_tax_code"),
            "reason": None, "map_row": row["name"]}


@frappe.whitelist()
def routes_for_week(week_ending, float_name=None, **kwargs):
    """Every entry in a week, with the route it would take.

    Drives the route column on the approval screen. Read-only — resolving a line
    changes nothing, so this is safe to call on every render.

    Accepts ``float`` as an alias for ``float_name``: the DocType field is called
    ``float``, so that is what a caller reaches for first, and Frappe silently drops
    an unmatched kwarg rather than complaining.
    """
    float_name = float_name or kwargs.get("float")
    filters = {"week_ending": week_ending}
    if float_name:
        filters["float"] = float_name

    entries = frappe.get_all(
        "Petty Cash Entry", filters=filters,
        fields=["name", "source_type", "source_key", "cancelled", "amount",
                "cash_in", "status"],
        limit_page_length=0,
    )

    routes, tally = {}, {}
    for e in entries:
        r = resolve(e)
        routes[e["name"]] = {
            "outcome": r["outcome"],
            "erp_account": r["erp_account"],
            "qbo_account": r["qbo_account"],
            "reason": r["reason"],
        }
        tally[r["outcome"]] = tally.get(r["outcome"], 0) + 1

    return {"routes": routes, "tally": tally, "entries": len(entries)}
