"""The old finance dev routes redirect under /fc-dev (TRK-00270). No bench needed:

    cd ~/projects/Vcl_finance && python3 -m unittest vcl_finance.tests.test_fc_dev_redirects -v

`resolve` copies how Frappe's website path resolver applies `website_redirects`:
the path arrives without its leading slash, the source is stripped of "/ " and
anchored with "$", re.match picks the rule and re.sub builds the target.
"""
import importlib.util
import pathlib
import re
import unittest

_p = pathlib.Path(__file__).resolve().parents[1] / "hooks.py"
_spec = importlib.util.spec_from_file_location("vcl_finance_hooks", _p)
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)


def resolve(path):
    for rule in H.website_redirects:
        pattern = rule["source"].strip("/ ") + "$"
        if re.match(pattern, path):
            return re.sub(pattern, rule["target"], path), rule.get("redirect_http_status") or 301
    return None


class FcDevRedirects(unittest.TestCase):
    def test_finance_dev(self):
        self.assertEqual(resolve("finance-dev"), ("/fc-dev/finance", 302))

    def test_petty_cash_dev(self):
        self.assertEqual(resolve("petty-cash-dev"), ("/fc-dev/petty-cash", 302))

    def test_sub_path_kept(self):
        self.assertEqual(resolve("finance-dev/cheques"), ("/fc-dev/finance/cheques", 302))
        self.assertEqual(resolve("petty-cash-dev/week/3"), ("/fc-dev/petty-cash/week/3", 302))

    def test_new_routes_do_not_loop(self):
        for p in ("fc-dev", "fc-dev/finance", "fc-dev/petty-cash", "fc-dev/finance/cheques"):
            self.assertIsNone(resolve(p), p)

    def test_live_portal_pages_untouched(self):
        # /petty-cash is the custodians' www page in this app; it must never redirect.
        for p in ("petty-cash", "petty-cash/sheet", "petty-cash-devx", "finance-developer", "app/finance-dev"):
            self.assertIsNone(resolve(p), p)

    def test_targets_are_absolute(self):
        for rule in H.website_redirects:
            self.assertTrue(rule["target"].startswith("/fc-dev/"), rule)


if __name__ == "__main__":
    unittest.main()
