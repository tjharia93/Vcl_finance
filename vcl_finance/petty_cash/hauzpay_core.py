"""Pure logic for the Hauz Pay statement import: parse, classify, plan.

No Frappe import on purpose (same pattern as ``weekpost_core``): everything takes plain
rows / dicts and returns plain dicts, so it is unit tested without a bench. ``hauzpay.py``
is the thin Frappe layer that reads the uploaded file and the live sheets, calls this,
and writes the plan.

Ported from the Gen-1 intranet importer (``intranet/petty_cash/app/backend/
hauzpay_import.py``, retired). The routing convention is unchanged from June 2026:

  * money in (STK / bank deposit)        -> Voucher, Cash-in
  * payee phone in the staff roster      -> Wages register (Wage)
  * known recurring payee / keyword      -> Voucher, mapped category
  * anything else                        -> Voucher, OT, FLAGGED for review
  * every "Transaction Charges" row      -> ONE combined OT voucher per week

Every route, category and bank here is a SUGGESTION (Tanuj, 14/10/2026): nothing is
decided automatically. The import writes each line Unapproved with the suggestion in
its notes (``suggestion``), and the reviewer accepts or changes it on the normal
approval screen, exactly as for a line the custodian keyed by hand.

What changed from Gen-1:

* The week is the Sun-Sat week anchored on SATURDAY (``week_saturday``, the same rule
  as the sheet controller), not Friday.
* The statement is read by HEADER NAME, not position: the export grew two columns
  (eTIMS, Expense Category) between June and October 2026.
* Two kinds of "already imported": an exact transaction-id match (``src_ref`` on a
  child row), and a LEGACY match - a row with no ``src_ref`` on the same sheet with
  the same date and amount (the Jul-Aug sheets were back-filled without ids, and a
  custodian may have keyed a line by hand). Legacy matches are skipped and shown.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

CHARGES_REF = "HZP-CHARGES"          # src_ref prefix of the one lumped-charges row per week
CHARGE_NARRATION = "transaction charges"
CATEGORY_CODES = ("TG", "TE", "SE", "OA", "FD", "GP", "OT")
LOCKED_STATUSES = ("Closed", "Submitted", "Approved")

# Routes, as the preview groups them.
CASH_IN, WAGE, VOUCHER, FLAGGED, CHARGE = "cash_in", "wage", "voucher", "flagged", "charge"

# Header text (lower-cased, stripped) -> our key. First match wins per column.
HEADERS = {
    "date + time": "date", "date": "date",
    "transaction id": "txid",
    "payment method": "method",
    "payee": "payee",
    "name": "name",
    "narration": "narration",
    "money in (kes)": "money_in", "money in": "money_in",
    "money out (kes)": "money_out", "money out": "money_out",
    "running balance (kes)": "balance", "running balance": "balance",
    "etims": "etims",
    "expense category": "expense_category",
    "raised by": "raised_by",
    "approved by": "approved_by",
}
# The June 2026 export had no meta rows and no header: fixed 11 columns.
LEGACY_COLUMNS = ("date", "txid", "method", "payee", "name", "narration",
                  "money_in", "money_out", "balance", "raised_by", "approved_by")


# ------------------------------------------------------------------ helpers
def ref_tokens(src_ref) -> list[str]:
    """The Hauz Pay transaction ids a row's ``src_ref`` stands for (the lumped-charges
    row carries ``HZP-CHARGES`` plus one id per charge). An id seen on ANY Hauz-Pay row,
    voided ones included, is never imported again: a line moved between Vouchers and
    Wages leaves its id on both the voided row and the new one."""
    return [t for t in re.split(r"[\s,]+", src_ref or "") if t and not t.startswith(CHARGES_REF)]


def week_saturday(d: date) -> date:
    """The Saturday ending the Sun-Sat week containing ``d`` (mirrors the sheet controller)."""
    return d + timedelta(days=(5 - d.weekday()) % 7)


def money(v) -> float:
    """'19,816.00' / 19816 / '' / None -> float. Never raises on a blank."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).strip().replace(",", "")
    if not s or s in ("-", "--"):
        return 0.0
    return round(float(s), 2)


def parse_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().split(" ")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unreadable date {v!r} — expected dd/mm/yyyy.")


def phone_key(v) -> str:
    """Last 9 digits of a Kenyan mobile: '+254 721 519206', '254721519206', '0721519206' agree.
    Anything that is not a mobile number (a paybill, an account) returns ''."""
    digits = re.sub(r"\D", "", str(v or ""))
    if len(digits) == 12 and digits.startswith("254"):
        return digits[3:]
    if len(digits) == 10 and digits.startswith("0"):
        return digits[1:]
    if len(digits) == 9 and digits[0] in "17":
        return digits
    return ""


def _text(v) -> str:
    return "" if v is None else str(v).replace("\n", " / ").strip()


# ------------------------------------------------------------------ parse
def parse_rows(rows) -> dict:
    """Parse the 'Statement' sheet's rows (tuples of cell values) into
    ``{period, opening, closing, lines, warnings}``.

    Handles the current export (PERIOD / OPENING / CLOSING rows, a header row, 13
    columns) and the June 2026 headerless 11-column export (no opening/closing).
    Every statement row becomes one line; charges are marked ``is_charge``.
    """
    rows = [tuple(r) for r in rows]
    period = opening = closing = None
    meta = {}
    header_idx, cols = None, None
    for i, r in enumerate(rows[:12]):
        first = _text(r[0] if r else "").lower()
        if first in ("period", "opening balance", "closing balance"):
            meta[first] = r[1] if len(r) > 1 else None
            continue
        lowered = [_text(c).lower() for c in r]
        if "transaction id" in lowered:
            header_idx = i
            cols = {}
            for j, h in enumerate(lowered):
                key = HEADERS.get(h)
                if key and key not in cols.values():
                    cols[j] = key
            break
    warnings = []
    if "period" in meta:
        period = _text(meta["period"])
    if "opening balance" in meta:
        opening = money(meta["opening balance"])
    if "closing balance" in meta:
        closing = money(meta["closing balance"])

    if header_idx is None:
        start = 0
        cols = {j: k for j, k in enumerate(LEGACY_COLUMNS)}
        warnings.append("No header row found — read as the June 2026 11-column layout. "
                        "There is no opening/closing balance to check against.")
    else:
        start = header_idx + 1
        missing = {"date", "txid", "money_in", "money_out"} - set(cols.values())
        if missing:
            raise ValueError("This does not look like a Hauz Pay statement: no column for "
                             + ", ".join(sorted(missing)) + ".")

    lines = []
    for n, r in enumerate(rows[start:], start=start + 1):
        rec = {k: (r[j] if j < len(r) else None) for j, k in cols.items()}
        d = parse_date(rec.get("date")) if _text(rec.get("date")) else None
        if d is None:
            continue
        narration = _text(rec.get("narration"))
        mi, mo = money(rec.get("money_in")), money(rec.get("money_out"))
        if not mi and not mo:
            continue
        lines.append({
            "row": n,                                   # 1-based spreadsheet row
            "date": d,
            "txid": _text(rec.get("txid")),
            "method": _text(rec.get("method")),
            "payee": _text(rec.get("payee")),
            "name": _text(rec.get("name")),
            "narration": narration,
            "money_in": mi,
            "money_out": mo,
            "balance": money(rec.get("balance")) if _text(rec.get("balance")) else None,
            "expense_category": _text(rec.get("expense_category")),
            "is_charge": narration.lower() == CHARGE_NARRATION,
        })
    if not lines:
        warnings.append("No transactions found in the statement.")
    return {"period": period, "opening": opening, "closing": closing,
            "lines": lines, "warnings": warnings}


def balance_check(parsed: dict) -> dict:
    """opening + in - out = closing, plus the first row whose running balance disagrees."""
    t_in = round(sum(l["money_in"] for l in parsed["lines"]), 2)
    t_out = round(sum(l["money_out"] for l in parsed["lines"]), 2)
    out = {"opening": parsed["opening"], "closing": parsed["closing"], "money_in": t_in,
           "money_out": t_out, "computed_closing": None, "difference": None, "ok": None,
           "first_running_break": None}
    if parsed["opening"] is None:
        return out
    computed = round(parsed["opening"] + t_in - t_out, 2)
    out["computed_closing"] = computed
    if parsed["closing"] is not None:
        out["difference"] = round(parsed["closing"] - computed, 2)
        out["ok"] = abs(out["difference"]) < 0.005
    run = parsed["opening"]
    for l in parsed["lines"]:
        run = round(run + l["money_in"] - l["money_out"], 2)
        if l["balance"] is not None and abs(l["balance"] - run) >= 0.005:
            out["first_running_break"] = {"row": l["row"], "txid": l["txid"],
                                          "expected": run, "statement": l["balance"]}
            break
    return out


# ------------------------------------------------------------------ classify
def build_roster(rules: dict, learned: dict | None = None) -> dict:
    """phone_key -> (name, source). rules.json wins over names learned from past sheets."""
    roster = {}
    for ph, name in (learned or {}).items():
        k = phone_key(ph)
        if k:
            roster[k] = (name, "learned")
    for ph, name in (rules.get("roster") or {}).items():
        k = phone_key(ph)
        if k:
            roster[k] = (name, "roster")
    return roster


TOPUP_SOURCES = {"bank_ipn": "Bank deposit", "mpesa_stk": "M-Pesa STK"}


def topup_bank(line: dict, rules: dict):
    """The bank a top-up came from, from ``rules['topup_banks']`` (narration pattern ->
    ERPNext bank account). Matched case-insensitively against name, narration, payee
    and method. Returns the rule dict or None — never a guess."""
    hay = " ".join(str(line.get(k) or "") for k in ("name", "narration", "payee", "method")).lower()
    for r in rules.get("topup_banks") or []:
        pats = r.get("match")
        for pat in ([pats] if isinstance(pats, str) else (pats or [])):
            if pat and pat.lower() in hay and r.get("account"):
                return r
    return None


def classify_topup(line: dict, rules: dict) -> dict:
    """Money IN = a top-up of the wallet. It posts to ERPNext (Dr Hauz-Pay Wallet,
    Cr the bank it came from) and never to QuickBooks. The bank found here is only a
    suggestion for the reviewer; when the statement does not say which bank, the line
    is flagged "bank?"."""
    source = TOPUP_SOURCES.get(line["method"].lower(), line["method"] or "Deposit")
    bank = topup_bank(line, rules)
    who = bank["label"] if bank and bank.get("label") else (line["name"] or "")
    return {"route": CASH_IN, "kind": "voucher", "category": "",
            "recipient": "Hauz Pay top-up" + (f" · {who}" if who else ""),
            "reason": "", "source": source,
            "bank_account": bank["account"] if bank else None,
            "bank_label": bank.get("label") if bank else None,
            "needs_bank": bank is None, "erp_only": True,
            "why": (f"Top-up from {bank.get('label') or bank['account']}" if bank
                    else "bank? — the statement does not say which bank; pick it")}


def classify(line: dict, rules: dict, roster: dict) -> dict:
    """Decide where ONE non-charge statement line goes. Returns
    ``{route, kind, category, recipient, reason, why}`` — ``kind`` is the sheet table
    (voucher / wage), ``why`` is the human reason shown in the preview."""
    if line["is_charge"]:
        return {"route": CHARGE, "kind": "voucher", "category": "OT",
                "recipient": "Hauz Pay transaction charges", "reason": "", "why": "Transaction charge"}
    if line["money_in"] > 0:
        return classify_topup(line, rules)

    pk = phone_key(line["payee"])
    if pk and pk in roster:
        name, src = roster[pk]
        return {"route": WAGE, "kind": "wage", "category": "",
                "recipient": f"254{pk} - {name}", "reason": line["narration"] or "Casual wage",
                "why": "Staff roster" if src == "roster" else "Paid as a wage before"}

    known = {phone_key(k) or k: v for k, v in (rules.get("known_payees") or {}).items()}
    hit = known.get(pk) if pk else known.get(line["payee"])
    if hit and hit.get("cat", "").upper() in CATEGORY_CODES:
        return {"route": VOUCHER, "kind": "voucher", "category": hit["cat"].upper(),
                "recipient": hit.get("label") or line["name"], "reason": "", "why": "Known payee"}

    hay = f"{line['narration']} {line['name']} {line['payee']}".lower()
    for kw in rules.get("keyword_rules") or []:
        if kw.get("match", "").lower() and kw["match"].lower() in hay \
                and kw.get("cat", "").upper() in CATEGORY_CODES:
            return {"route": VOUCHER, "kind": "voucher", "category": kw["cat"].upper(),
                    "recipient": kw.get("label") or line["name"], "reason": "",
                    "why": f"Keyword '{kw['match']}'"}

    who = line["name"] or line["payee"] or line["narration"] or "Unknown payee"
    if line["payee"] and line["payee"] not in who:
        who = f"{line['payee']} - {who}"
    return {"route": FLAGGED, "kind": "voucher", "category": "OT", "recipient": who,
            "reason": "", "why": "Unknown payee — pick a category"}


def suggestion(c: dict) -> str:
    """The classification as the words the reviewer reads in the line's notes.

    Always "Suggested: ..." (or says there is no suggestion): the import proposes, the
    approval screen decides. A top-up's bank in particular is NEVER written as the
    line's ``posting_account`` - that field is a human decision and beats the map.
    """
    route = c["route"]
    if route == CASH_IN:
        if c.get("bank_account"):
            return f"Suggested: Cr {c['bank_account']} ({c.get('bank_label') or 'from the rules'})"
        return "No bank suggested: the statement does not say which bank. Pick it on approval"
    if route == WAGE:
        return f"Suggested: wage ({c['why']})"
    if route == FLAGGED:
        return f"Suggested: {c['category']} (unknown payee, pick a category)"
    if route == CHARGE:
        return f"Suggested: {c['category']} (transaction charges)"
    return f"Suggested: {c['category']} ({c['why']})"


# ------------------------------------------------------------------ plan
def _legacy_key(row: dict):
    return (row["date"], round(float(row["amount"]), 2), bool(row.get("cash_in")))


def plan(parsed: dict, rules: dict, sheets: dict, learned_roster: dict | None = None,
         known_refs: dict | None = None) -> dict:
    """Turn a parsed statement into a per-week plan. Writes nothing.

    ``sheets``: week_ending(date) -> {name, status, opening_balance, rows:[{date, amount,
    cash_in, phone, is_charges, src_ref}]} — the live Hauz-Pay sheet for that week, if any.
    ``known_refs``: src_ref -> sheet name, across EVERY Hauz-Pay sheet (a txid imported
    into any week is never imported again).
    """
    roster = build_roster(rules, learned_roster)
    known_refs = known_refs or {}

    # Opening balance of each week, reconstructed from the statement's running balance.
    by_week, week_open = {}, {}
    run = parsed["opening"]
    for l in parsed["lines"]:
        we = week_saturday(l["date"])
        if we not in by_week:
            by_week[we] = []
            week_open[we] = round(run, 2) if run is not None else None
        by_week[we].append(l)
        if run is not None:
            run += l["money_in"] - l["money_out"]

    weeks = []
    # Route counts are of NEW lines only; "flagged" is the review queue.
    totals = {"lines": 0, "new": 0, "duplicate": 0, "on_sheet": 0, "skipped_locked": 0,
              CASH_IN: 0, WAGE: 0, VOUCHER: 0, FLAGGED: 0, CHARGE: 0, "needs_bank": 0}
    for we in sorted(by_week):
        sheet = sheets.get(we)
        status = sheet["status"] if sheet else "New"
        locked = status in LOCKED_STATUSES
        # Legacy rows (no src_ref) on this sheet that a statement line may already be.
        legacy_rows = [r for r in (sheet or {}).get("rows", [])
                       if not r.get("src_ref") and r.get("date") and float(r.get("amount") or 0)]
        legacy_charges = any(r.get("is_charges") for r in legacy_rows)
        legacy_rows = [r for r in legacy_rows if not r.get("is_charges")]
        # Pass 1: classify, and settle what needs no legacy match.
        lines, pending = [], []
        for l in by_week[we]:
            c = classify(l, rules, roster)
            amount = l["money_in"] or l["money_out"]
            item = {"row": l["row"], "date": l["date"].isoformat(), "txid": l["txid"],
                    "payee": l["payee"], "name": l["name"], "narration": l["narration"],
                    "expense_category": l["expense_category"], "amount": amount,
                    "direction": "in" if l["money_in"] else "out", **c,
                    "suggestion": suggestion(c), "state": "new", "note": ""}
            if l["txid"] and l["txid"] in known_refs:
                item["state"], item["note"] = "duplicate", f"Already imported ({known_refs[l['txid']]})"
            elif locked:
                item["state"], item["note"] = "skipped_locked", f"Week is {status}"
            elif c["route"] == CHARGE:
                if legacy_charges:
                    item["state"], item["note"] = "on_sheet", "Week already has a charges line"
            else:
                pending.append((item, (l["date"], amount, c["route"] == CASH_IN), phone_key(l["payee"])))
            lines.append(item)

        # Pass 2: legacy rows with no id. Every same-phone match is taken BEFORE any
        # date+amount-only match, so a line can never steal the row of a different payee
        # who happened to be paid the same amount that day.
        used = set()
        for want_phone in (True, False):
            for item, key, pk in pending:
                if item["state"] != "new":
                    continue
                for i, r in enumerate(legacy_rows):
                    if i in used or _legacy_key(r) != key:
                        continue
                    if want_phone and not (pk and r.get("phone") == pk):
                        continue
                    used.add(i)
                    item["state"] = "on_sheet"
                    item["note"] = "Same date and amount already on the sheet" + (
                        f" ({r.get('label')})" if r.get("label") else "")
                    break

        charges_new, charges_refs, charges_skipped = 0.0, [], 0
        for item in lines:
            c, amount = item, item["amount"]
            totals["lines"] += 1
            totals[item["state"]] = totals.get(item["state"], 0) + 1
            if item["state"] == "new":
                totals[c["route"]] += 1
                if c.get("needs_bank"):
                    totals["needs_bank"] += 1
                if c["route"] == CHARGE:
                    charges_new += amount
                    charges_refs.append(item["txid"])
            elif c["route"] == CHARGE:
                charges_skipped += 1

        w_in = round(sum(l["money_in"] for l in by_week[we]), 2)
        w_out = round(sum(l["money_out"] for l in by_week[we]), 2)
        sheet_open = (sheet or {}).get("opening_balance")
        stmt_open = week_open[we]
        weeks.append({
            "week_ending": we.isoformat(),
            "sheet": (sheet or {}).get("name"),
            "status": status,
            "action": "skip" if locked else ("use" if sheet else "create"),
            "lines": lines,
            "counts": {k: sum(1 for x in lines if x["state"] == k)
                       for k in ("new", "duplicate", "on_sheet", "skipped_locked")},
            "flagged": sum(1 for x in lines if x["state"] == "new" and x["route"] == FLAGGED),
            "needs_bank": sum(1 for x in lines if x["state"] == "new" and x.get("needs_bank")),
            "charges": {"amount": round(charges_new, 2), "refs": charges_refs,
                        "count": len(charges_refs), "skipped": charges_skipped},
            "money_in": w_in, "money_out": w_out,
            "statement_opening": stmt_open,
            "statement_closing": round(stmt_open + w_in - w_out, 2) if stmt_open is not None else None,
            "sheet_opening": sheet_open,
            "opening_basis": None,
            "opening_difference": (round(float(sheet_open) - stmt_open, 2)
                                   if sheet is not None and stmt_open is not None and sheet_open is not None
                                   else None),
        })
    return {"period": parsed["period"], "balance": balance_check(parsed), "weeks": weeks,
            "totals": totals, "warnings": list(parsed.get("warnings") or [])}
