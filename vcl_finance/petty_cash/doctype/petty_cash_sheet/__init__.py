# Re-export the whitelisted convenience endpoint so the Compass SPA can reach it at
# the short path `vcl_finance.petty_cash.doctype.petty_cash_sheet.create_for_week`
# (the function itself lives in petty_cash_sheet.py). Without this, Frappe resolves
# that path to this package and raises AttributeError, blocking "start this week".
from .petty_cash_sheet import create_for_week  # noqa: F401
