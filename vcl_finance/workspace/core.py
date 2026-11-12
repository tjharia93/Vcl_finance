"""Pure finance policies. No Frappe, credentials or database access."""
import calendar
import hashlib
import json
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KINDS = {"Review", "Close", "Exception", "Plan", "Snapshot", "Pack"}
PLAN_TYPES = {"Annual budget", "Rolling forecast", "Cash forecast"}
SCENARIOS = {"Base", "Downside", "Upside"}
READ_FC = KINDS
READ_BPA = {"Plan", "Snapshot", "Pack"}
READ_OPS = {"Review"}
IGNORED_HASH_KEYS = {"modified", "modified_by", "creation", "owner", "docstatus", "idx", "__onload", "__islocal", "_user_tags", "_comments", "_assign", "_liked_by", "workflow_state"}


def decimal_amount(value):
    try:
        result = Decimal(str(value if value is not None else 0).replace(",", "").replace(" ", ""))
        if not result.is_finite():
            raise ValueError("Enter a finite monetary amount.")
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid monetary amount.") from None


def money(value):
    return decimal_amount(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fingerprint(value):
    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items() if k not in IGNORED_HASH_KEYS and not k.startswith("_")}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def periods(start, count, weekly=False):
    d = date.fromisoformat(str(start)[:10])
    if weekly:
        d -= timedelta(days=d.weekday())
    else:
        d = d.replace(day=1)
    out = []
    for _ in range(count):
        end = d + timedelta(days=6) if weekly else d.replace(day=calendar.monthrange(d.year, d.month)[1])
        out.append({"start": str(d), "end": str(end)})
        d = end + timedelta(days=1)
    return out


def summarize_drafts(rows, kind, currency):
    """Only sum company-base amounts. Never substitute transaction amounts."""
    totals, transaction = {}, {}
    for row in rows:
        direction = row.get("payment_type") or "Drafts"
        base = "total_debit" if kind == "je" else ("base_paid_amount" if direction in {"Pay", "Internal Transfer"} else "base_received_amount") if kind == "pe" else "base_grand_total"
        amount = row.get(base)
        bucket = totals.setdefault(direction, {"count": 0, "base_amount": Decimal(0), "missing": 0})
        bucket["count"] += 1
        if amount is None:
            bucket["missing"] += 1
        else:
            bucket["base_amount"] += decimal_amount(amount)
        c = row.get("currency") or (row.get("paid_from_account_currency") if direction in {"Pay", "Internal Transfer"} else row.get("paid_to_account_currency")) or currency
        raw = row.get("grand_total") if kind in {"pi", "si"} else row.get("paid_amount") if direction in {"Pay", "Internal Transfer"} else row.get("received_amount") if kind == "pe" else row.get("total_debit")
        key = (direction, c)
        transaction[key] = transaction.get(key, Decimal(0)) + decimal_amount(raw)
    return {"base_currency": currency, "directions": {k: {**v, "base_amount": None if v["missing"] else float(money(v["base_amount"]))} for k, v in totals.items()},
            "transaction_totals": [{"direction": k[0], "currency": k[1], "amount": float(money(v))} for k, v in transaction.items()]}


def validate_plan(payload):
    ptype = payload.get("plan_type")
    if ptype not in PLAN_TYPES or payload.get("scenario") not in SCENARIOS:
        raise ValueError("Choose a plan type and a Base, Downside or Upside scenario.")
    horizon = payload.get("periods") or []
    expected = 13 if ptype == "Cash forecast" else 12
    if len(horizon) != expected:
        raise ValueError(f"This plan needs {expected} periods.")
    generated = periods(horizon[0]["start"], expected, ptype == "Cash forecast")
    if horizon != generated:
        raise ValueError("Periods must be consecutive whole months or Monday–Sunday weeks.")
    seen = set()
    if not payload.get("lines"):
        raise ValueError("Add at least one planning line.")
    for line in payload.get("lines", []):
        key = (line.get("account"), line.get("cost_center"), line.get("product_line"), line.get("category"))
        if key in seen:
            raise ValueError("Combine duplicate account, cost centre, product line and category rows.")
        seen.add(key)
        if len(line.get("values", [])) != expected:
            raise ValueError("Every line must have a value for each period; use null for an unknown value.")
        for v in line["values"]:
            if v is not None:
                money(v)
        line["values"] = [None if v is None else float(money(v)) for v in line["values"]]
        if line.get("category") not in {"Revenue", "Expense", "Collection", "Payment", "Opening cash", "Financing"}:
            raise ValueError("Choose an accounting or cash category.")
        if ptype != "Cash forecast" and not line.get("account"):
            raise ValueError("Monthly financial plans require a ledger account on every line.")
    if not payload.get("assumptions"):
        raise ValueError("Record the assumptions and their owner before saving a plan.")
    if any(not a.get("owner") or not str(a.get("description", "")).strip() for a in payload["assumptions"]):
        raise ValueError("Every assumption needs an owner and a description.")
    return payload


def cash_outlook(payload):
    """Carry opening cash forward; unknown inputs propagate instead of becoming zero."""
    previous, result = None, []
    for i, period in enumerate(payload["periods"]):
        groups = {k: [] for k in ["Opening cash", "Collection", "Payment", "Financing"]}
        for line in payload["lines"]:
            groups[line["category"]].append(line["values"][i])
        def total(values):
            return None if not values or any(v is None for v in values) else sum((money(v) for v in values), Decimal(0))
        opening_values = [v for v in groups["Opening cash"] if v is not None]
        opening = total(opening_values) if opening_values else previous
        incoming, outgoing, financing = (total(groups[k]) for k in ["Collection", "Payment", "Financing"])
        closing = None if any(v is None for v in [opening, incoming, outgoing, financing]) else opening + incoming - outgoing + financing
        result.append({**period, **{k: None if v is None else float(v) for k, v in {"opening": opening, "collections": incoming, "payments": outgoing, "financing": financing, "closing": closing}.items()}})
        previous = closing
    return result


def transition(status, action, actor, preparer, fc=False, final=False, final_required=False):
    rules = {("Draft", "submit"): "In review", ("Changes requested", "submit"): "In review",
             ("In review", "return"): "Changes requested", ("In review", "approve"): "Approved",
             ("Approved", "publish"): "Published", ("Approved", "post"): "Posted"}
    if (status, action) not in rules:
        raise ValueError(f"Cannot {action} a record in {status}.")
    if action in {"approve", "return"}:
        if not fc:
            raise PermissionError("A Financial Controller must review this record.")
        if actor == preparer:
            raise PermissionError("The preparer cannot review their own work.")
    if action == "publish" and final_required and not final:
        raise PermissionError("Tanuj's final authorisation is required.")
    return rules[(status, action)]



def close_tasks(month_end):
    start = date.fromisoformat(str(month_end)[:10])
    def due(n):
        d, i = start, 0
        while i < n:
            d += timedelta(days=1)
            if d.weekday() < 5:
                i += 1
        return str(d)
    template = json.loads(Path(__file__).with_name("close_template.json").read_text(encoding="utf-8"))
    return [{**task, "id": str(i + 1), "owner": "", "due_date": due(task["day"]), "status": "Open", "evidence": [], "comment": ""} for i, task in enumerate(template)]
