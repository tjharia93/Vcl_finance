"""after_install for the whole app: each module's seed, in turn."""


def after_install():
    from vcl_finance.petty_cash.install import after_install as petty_cash
    from vcl_finance.book_alignment.install import seed as book_alignment

    petty_cash()
    book_alignment()
