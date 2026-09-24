"""Which line of the signed accounts each QBO and ERPNext account belongs to.

Pure Python, no frappe import, so the arithmetic can be tested without a bench.
The seeds here were carried over unchanged from the Excel version Tanuj reviewed
on 24-Sep-2026 (`~/projects/reports/watermark_account_map_balances_2026-09-24/
add_balances.py`); the doctypes are their permanent home once seeded, and these
constants are only the fallback when a record says nothing.

Sign convention everywhere: a balance is Dr + / Cr -. A line's figure is shown on
its natural side: ``nature * sum(balances)``, nature +1 for Debit lines and -1 for
Credit lines, so assets, costs, liabilities, equity and income all read positive.
"""

# code, label, note, audited amount at 31-12-2025 (natural side), nature, section.
# Source: FSV2025.pdf, the signed FY2025 statements approved 16-Jun-2026.
FS_LINES_FY2025 = [
    ("PPE", "Property, plant and equipment", "6", 113780418, "Debit", "Balance sheet"),
    ("LAND", "Land", "", 7500000, "Debit", "Balance sheet"),
    ("CWIP", "Capital work in progress (land and buildings)", "", 335601312, "Debit", "Balance sheet"),
    ("INV", "Inventories", "8", 96032195, "Debit", "Balance sheet"),
    ("TR", "Trade receivables", "9", 151706399, "Debit", "Balance sheet"),
    ("OR", "Other receivables", "9", 761142, "Debit", "Balance sheet"),
    ("PRE", "Prepayments", "9", 554304, "Debit", "Balance sheet"),
    ("DEP", "Deposits", "9", 22222255, "Debit", "Balance sheet"),
    ("VAT", "VAT recoverable", "9", 16834749, "Debit", "Balance sheet"),
    ("CASHH", "Cash on hand", "12", 1593425, "Debit", "Balance sheet"),
    ("CASHB", "Cash at bank", "12", 4163096, "Debit", "Balance sheet"),
    ("TAXR", "Taxes recoverable", "", 1677985, "Debit", "Balance sheet"),
    ("SHARE", "Share capital", "10", 3000000, "Credit", "Balance sheet"),
    ("REVAL", "Revaluation surplus", "", 4012078, "Credit", "Balance sheet"),
    ("GENRES", "General reserves (incl. retained profit)", "", 138833372, "Credit", "Balance sheet"),
    ("DTAX", "Deferred tax", "7", 1475375, "Credit", "Balance sheet"),
    ("BORNC", "Borrowings - bank loans (non-current)", "11", 189542209, "Credit", "Balance sheet"),
    ("TP", "Trade payables", "13", 239954676, "Credit", "Balance sheet"),
    ("DIR", "Payable to directors and related parties", "13", 99195641, "Credit", "Balance sheet"),
    ("ACCR", "Accruals", "13", 9045801, "Credit", "Balance sheet"),
    ("OTHP", "Other payables", "13", 566281, "Credit", "Balance sheet"),
    ("OD", "Borrowings - bank overdraft (current)", "11", 66802115, "Credit", "Balance sheet"),
    ("TAXP", "Taxes payable", "", -269, "Credit", "Balance sheet"),
    ("NOEQ", "No audited line - needs explaining", "", 0, "Debit", "Balance sheet"),
    ("REV", "Revenue", "", 495524911, "Credit", "P&L"),
    ("OI", "Other income", "15", 0, "Credit", "P&L"),
    ("COS", "Cost of sales", "Annex 1", 377233476, "Debit", "P&L"),
    ("STAFF", "Staff costs", "Annex 2", 35417028, "Debit", "P&L"),
    ("OPEX", "Operating and administrative expenses", "Annex 3", 24748945, "Debit", "P&L"),
    ("FIN", "Finance cost", "3", 38168391, "Debit", "P&L"),
    ("TAX", "Tax charge", "4", 6047367, "Debit", "P&L"),
]
FY2025_AS_AT = "2025-12-31"
FY2025_SOURCE = "FSV2025.pdf - signed FY2025 statements, approved 16-Jun-2026"

# Where an account with no home goes. It is a real line so that nothing drops out
# of the totals: an unassigned balance shows up here instead of vanishing.
UNASSIGNED = "NOEQ"

# PBT = income less costs before tax, on the natural side of each line.
PBT_PLUS = ("REV", "OI")
PBT_MINUS = ("COS", "STAFF", "OPEX", "FIN")

# ERPNext account -> line, from the Excel build. The last resort after the
# crosswalk's decided and proposed accounts. Anything absent (Holding Bank,
# Reconciliation, SRBNB, import duties and levies) lands on UNASSIGNED.
ERP_LINE_SEED = {
    "1110 - Cash - VCL": "CASHH",
    "95900200001557 - BOB - USD - VCL": "CASHB",
    "ABC BANK - VCL": "CASHB",
    "Co-OP - VCL": "CASHB",
    "95900400000115 - BOB Kshs - VCL": "OD",
    "1310 - Debtors - VCL": "TR",
    "1315 - Provision for Bad Debts (QBO opening) - VCL": "TR",
    "1410 - Stock In Hand - VCL": "INV",
    "1420 - Spares - VCL": "INV",
    "1610 - Employee Advances - VCL": "OR",
    "1720 - Electronic Equipments - VCL": "PPE",
    "1780 - Accumulated Depreciation - VCL": "PPE",
    "2103 - Petty Cash Expense - VCL": "ACCR",
    "2110 - Creditors - VCL": "TP",
    "2130 - 2130 - Creditors - VCL - USD - VCL": "TP",
    "2120-1 - Payroll Bank Account - VCL": "ACCR",
    "2140 - Employees - Payable - VCL": "ACCR",
    "VAT - VCL": "VAT",
    "3300 - Opening Balance Equity - VCL": "GENRES",
    "4110 - Sales - VCL": "REV",
    "5111 - Cost of Goods Sold - VCL": "COS",
    "5111.1 - Consumables - VCL": "COS",
    "5119 - Stock Adjustment - VCL": "COS",
    "5201 - Administrative Expenses - VCL": "OPEX",
    "5201.1 - Travel - Sales and Administration - VCL": "OPEX",
    "5202 - Commission on Sales - VCL": "STAFF",
    "5203 - Depreciation - VCL": "OPEX",
    "5212 - Round Off - VCL": "OPEX",
    "5219 - Exchange Gain/Loss - VCL": "FIN",
    "5224 - Engineering Expense - VCL": "OPEX",
}

_STAFF_PREFIXES = (
    "net salaries", "payroll expenses:nssf", "payroll expenses:paye", "payroll expenses:sha",
    "payroll expenses:lompasago", "nita", "sales commission", "medical", "employee injury",
    "wage expenses",
)


def qbo_line(name, classification, account_type):
    """The audited line a QBO account belongs to, from its name, class and type.

    Used to seed the crosswalk's fs_line, and by the report for any QBO account
    that has no crosswalk row yet (a new account the daily sync brought in).
    """
    name = name or ""
    cls, typ = classification or "", account_type or ""
    n = name.lower()
    if typ == "Accounts Receivable": return "TR"
    if typ == "Accounts Payable": return "TP"
    if name == "Bank of Baroda - KES": return "OD"
    if "petty cash" in n: return "CASHH"
    if typ == "Bank": return "CASHB" if "cash and cash" in n or "undeposited" in n else UNASSIGNED
    if name == "Land": return "LAND"
    if typ == "Fixed Asset": return "PPE"
    if name == "Long-Term Investments": return "CWIP"
    if n.startswith("deposits"): return "DEP"
    if n.startswith("income tax pre-payments") or name == "Taxes Payable": return "TAXP"
    if n.startswith("inventory"): return "INV"
    if n.startswith("prepaid"): return "PRE"
    if name == "Taxes Recoverable": return "TAXR"
    if n.startswith("vat"): return "VAT"
    if cls == "Asset": return "OR"
    if name == "Share capital": return "SHARE"
    if name == "Revaluation Surplus": return "REVAL"
    if cls == "Equity": return "GENRES"
    if typ == "Long Term Liability": return "DTAX" if "tax" in n else "BORNC"
    if n.startswith("payable to directors"): return "DIR"
    if n.startswith("accrued"): return "ACCR"
    if cls == "Liability": return "OTHP"
    if cls == "Revenue": return "OI" if typ == "Other Income" else "REV"
    # expenses: follow the audit annexures where the account plainly belongs
    if n.startswith(("interest expense", "bank charges", "finance charge")): return "FIN"
    if n.startswith("utilities"): return "OPEX" if "mobile" in n else "COS"
    if typ == "Cost of Goods Sold": return "COS"
    if n.startswith(_STAFF_PREFIXES): return "STAFF"
    return "OPEX"


PREFER_SEED = "Decided, then seed, then proposed"
PREFER_PROPOSED = "Decided, then proposed, then seed"


def erp_line_map(crosswalk_rows, seed=None, prefer=PREFER_SEED):
    """ERPNext account -> (line, basis, qbo accounts that point at it).

    An account Tanuj DECIDED (decision Map with an erp_account) always wins.
    Below that, ``prefer`` orders the other two sources:

    - the seed dict was built per ERPNext account and reviewed in the Excel
      version, so it reproduces the figures he has already seen;
    - a proposal is made per QBO account. Several QBO accounts point at one
      ERPNext account (five at 1110 Cash, three at VAT), and the line comes from
      the QBO side, so the ERPNext account inherits whichever line sorts first.
      At 31-12-2025 that moves 5119 Stock Adjustment (a P&L account) onto
      Inventories and VAT onto Taxes recoverable.

    So the seed goes first by default, and the report offers the other order as
    a filter. Ignore and Create rows contribute nothing; a Question row keeps its
    proposal. ``crosswalk_rows`` are dicts with qbo_account, erp_account,
    proposed_erp_account, decision and fs_line.

    Where QBO accounts disagree on the line, the first in qbo_account order wins
    and the basis says "conflict" so it cannot pass unnoticed.
    """
    seed = ERP_LINE_SEED if seed is None else seed
    decided, proposed = {}, {}

    def add(bucket, basis, acct, r):
        if acct not in bucket:
            bucket[acct] = [r["fs_line"], basis, []]
        entry = bucket[acct]
        entry[2].append(r["qbo_account"])
        if entry[0] != r["fs_line"] and "conflict" not in entry[1]:
            entry[1] += " (conflict)"

    for r in sorted(crosswalk_rows, key=lambda r: str(r.get("qbo_account"))):
        if not r.get("fs_line"):
            continue
        decision = r.get("decision") or ""
        if decision == "Map" and r.get("erp_account"):
            add(decided, "Decided", r["erp_account"], r)
        elif decision in ("", "Question") and r.get("proposed_erp_account"):
            add(proposed, "Proposed", r["proposed_erp_account"], r)

    seeded = {acct: (line, "Seed", []) for acct, line in seed.items()}
    proposed = {a: tuple(v) for a, v in proposed.items()}
    out = {}
    for layer in ((proposed, seeded) if prefer == PREFER_SEED else (seeded, proposed)):
        out.update(layer)
    out.update({a: tuple(v) for a, v in decided.items()})
    return out


def natural(nature):
    return 1 if nature == "Debit" else -1


def line_totals(balances, line_of, natures):
    """Sum Dr+/Cr- balances into lines and turn each onto its natural side.

    ``balances`` {account: balance}; ``line_of`` {account: line code}, missing
    accounts go to UNASSIGNED; ``natures`` {line code: "Debit"|"Credit"}.
    """
    raw = {code: 0.0 for code in natures}
    for acct, bal in balances.items():
        code = line_of.get(acct)
        if code not in raw:
            code = UNASSIGNED
        raw[code] += bal or 0.0
    return {code: natural(natures[code]) * raw[code] for code in natures}


def pbt(totals):
    return sum(totals.get(k, 0.0) for k in PBT_PLUS) - sum(totals.get(k, 0.0) for k in PBT_MINUS)
