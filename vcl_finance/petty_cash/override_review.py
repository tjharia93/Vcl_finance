"""What the overrides are telling us about the map.

Every week, Finance approves lines. Most take the account the map proposed; some
get changed. A change is not a mistake — it is evidence, and the pattern in the
changes says something specific about which map row is wrong.

This reads that evidence and produces PROPOSALS. It never edits the map. An
override is one person's judgement on one line; turning it into the standing rule
for every future line of that kind is a different decision, and the map's own
approval gate is where that decision belongs. An agent that quietly rewrote the
map would let a single mis-click become policy, and nobody would know when it
happened.

Three findings, and they mean different things:

  CHANGE THE MAP     every override of this route went to the same account.
                     The map row is simply wrong; say what it should be.

  SPLIT THE ROUTE    overrides of this route went to several different accounts.
                     One map row cannot be right for all of them — the pair is
                     too coarse and needs a finer key, or the route is genuinely
                     case-by-case and should stay unmapped on purpose.

  FILL THE MAP       lines approved with an account where the map had none.
                     Nothing is wrong; the map is just incomplete, and here is
                     what people have been choosing.
"""

from collections import defaultdict

import frappe
from frappe.utils import add_days, flt, nowdate

# How many times a route must have been overridden the same way before it is
# worth proposing a change. One override is an opinion; three is a pattern.
MIN_EVIDENCE = 3


def _entries(from_date, to_date):
    return frappe.get_all(
        "Petty Cash Entry",
        filters={
            "week_ending": ("between", [from_date, to_date]),
            "status": "Approved",
            "cancelled": 0,
        },
        fields=["name", "source_type", "source_key", "amount", "posting_account",
                "mapped_account", "override_reason", "approved_by", "week_ending"],
        limit_page_length=0,
    )


@frappe.whitelist()
def review(from_date=None, to_date=None):
    """Look back over approved lines and propose map changes. Read-only."""
    to_date = to_date or nowdate()
    from_date = from_date or add_days(to_date, -56)   # eight weeks of evidence

    rows = _entries(from_date, to_date)

    # route -> chosen account -> [count, value, sample reasons]
    overridden = defaultdict(lambda: defaultdict(lambda: [0, 0.0, []]))
    filled = defaultdict(lambda: defaultdict(lambda: [0, 0.0, []]))
    agreed = defaultdict(int)

    for e in rows:
        chosen, mapped = e.get("posting_account"), e.get("mapped_account")
        if not chosen:
            continue                       # never coded — not evidence either way
        route = (e.get("source_type") or "?", (e.get("source_key") or "").strip())
        if not mapped:
            bucket = filled[route][chosen]
        elif chosen != mapped:
            bucket = overridden[route][chosen]
        else:
            agreed[route] += 1
            continue
        bucket[0] += 1
        bucket[1] += flt(e.get("amount"))
        why = (e.get("override_reason") or "").strip()
        if why and len(bucket[2]) < 3:
            bucket[2].append(why)

    findings = []

    for route, choices in overridden.items():
        total = sum(c[0] for c in choices.values())
        if total < MIN_EVIDENCE:
            continue
        st, sk = route
        if len(choices) == 1:
            acct, (n, val, why) = next(iter(choices.items()))
            findings.append({
                "kind": "change_the_map", "source_type": st, "source_key": sk,
                "account": acct, "times": n, "value": round(val, 2),
                "agreed_times": agreed.get(route, 0), "reasons": why,
                "headline": f"{st} · {sk or '—'} was re-coded to {acct} {n} times, "
                            f"never to anything else. The map row looks wrong.",
            })
        else:
            spread = sorted(((a, c[0], round(c[1], 2)) for a, c in choices.items()),
                            key=lambda x: -x[1])
            findings.append({
                "kind": "split_the_route", "source_type": st, "source_key": sk,
                "spread": spread, "times": total, "agreed_times": agreed.get(route, 0),
                "headline": f"{st} · {sk or '—'} was re-coded {total} times across "
                            f"{len(choices)} different accounts. One row cannot be "
                            f"right for all of them.",
            })

    for route, choices in filled.items():
        total = sum(c[0] for c in choices.values())
        if total < MIN_EVIDENCE:
            continue
        st, sk = route
        acct, (n, val, _why) = max(choices.items(), key=lambda kv: kv[1][0])
        findings.append({
            "kind": "fill_the_map", "source_type": st, "source_key": sk,
            "account": acct, "times": n, "value": round(val, 2),
            "alternatives": len(choices) - 1,
            "headline": f"{st} · {sk or '—'} has no map row. It has been coded to "
                        f"{acct} {n} time{'s' if n != 1 else ''}"
                        + (f" (and {len(choices)-1} other account(s))" if len(choices) > 1 else "")
                        + ".",
        })

    findings.sort(key=lambda f: -(f.get("value") or sum(s[2] for s in f.get("spread", []))))

    return {
        "from_date": str(from_date), "to_date": str(to_date),
        "approved_lines": len(rows),
        "coded_lines": sum(1 for e in rows if e.get("posting_account")),
        # sum of every override across every route — `len(c) and ...` was a
        # needlessly clever way to write the same thing, and read as a bug.
        "overridden_lines": sum(v[0] for c in overridden.values() for v in c.values()),
        "findings": findings,
        "nothing_to_do": not findings,
    }


def summary(from_date=None, to_date=None):
    """Plain text, for an agent or a console. Never writes."""
    r = review(from_date, to_date)
    if r["nothing_to_do"]:
        return (f"Petty cash map review {r['from_date']} → {r['to_date']}: "
                f"{r['approved_lines']} approved lines, nothing to change.")
    lines = [f"Petty cash map review {r['from_date']} → {r['to_date']}",
             f"{r['approved_lines']} approved lines, {r['overridden_lines']} re-coded.", ""]
    for f in r["findings"]:
        lines.append(f"[{f['kind'].replace('_', ' ').upper()}] {f['headline']}")
        if f.get("reasons"):
            lines.append("    reasons given: " + " · ".join(f["reasons"]))
        if f.get("spread"):
            for a, n, v in f["spread"]:
                lines.append(f"    {n:>3} × {a}  (KES {v:,.0f})")
        lines.append("")
    lines.append("Nothing here has been changed. Each proposal is a Posting Map row "
                 "for a person to set and approve.")
    return "\n".join(lines)
