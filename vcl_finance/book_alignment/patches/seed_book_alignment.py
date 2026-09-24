"""Seed the audited FY2025 lines and one crosswalk row per QBO account."""
from vcl_finance.book_alignment.install import seed


def execute():
    seed()
