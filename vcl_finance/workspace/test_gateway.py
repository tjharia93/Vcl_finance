"""Gateway policy tests with isolated Frappe doubles; never contacts a site."""
import importlib
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Denied(Exception):
    pass


class Changed(Exception):
    pass


class GatewayPolicies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = types.ModuleType("frappe")
        cls.f.whitelist = lambda *a, **k: lambda fn: fn
        cls.f.PermissionError, cls.f.TimestampMismatchError = Denied, Changed
        cls.f._dict, cls.f.as_json = Row, json.dumps
        cls.f.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        utils = types.ModuleType("frappe.utils")
        from datetime import date
        utils.getdate = lambda x: date.fromisoformat(str(x))
        utils.now = lambda: "2026-11-12 12:00:00"
        utils.nowdate = lambda: "2026-11-12"
        cls.modules = patch.dict(sys.modules, {"frappe": cls.f, "frappe.utils": utils})
        cls.modules.start()
        cls.api = importlib.import_module("vcl_finance.workspace.api")
        cls.guards = importlib.import_module("vcl_finance.workspace.guards")

    @classmethod
    def tearDownClass(cls):
        cls.modules.stop()
        sys.modules.pop("vcl_finance.workspace.api", None)
        sys.modules.pop("vcl_finance.workspace.guards", None)

    def setUp(self):
        self.f.session = Row(user="planner")
        self.f.db = Mock()
        self.f.get_roles = Mock(return_value=["VCL Financial Planner"])
        self.f.get_list = Mock(return_value=[Row(name="Allowed", default_currency="KES")])
        self.settings = Row(phase_1=1, phase_2=1, phase_3=1, phase_4=1, final_approver="tanuj")
        self.f.get_single = Mock(return_value=self.settings)
        self.f.get_doc = Mock()
        self.f.permissions = Mock()
        self.f.permissions.get_user_permissions.return_value = {}
        self.c = {"company": "Allowed", "currency": "KES", "user": "planner", "caps": {"fc": False, "bpa": True, "ops": False}, "final": False, "settings": self.settings}

    def doc(self, kind="Plan", status="Draft", revision=3):
        return Row(name="FIN-1", kind=kind, company="Allowed", title="Test", status=status, revision=revision,
                   modified="2026-11-12", owner="maker", payload_json=json.dumps({"preparer": "maker", "history": []}))

    def test_guest_and_cross_company_denied(self):
        with self.assertRaises(Denied): self.api._ctx("Other")
        self.f.session.user = "Guest"
        with self.assertRaises(Denied): self.api._ctx("Allowed")

    def test_disabled_phase_cannot_be_called_directly(self):
        self.settings.phase_3 = 0
        with self.assertRaises(Denied): self.api.save_record("Allowed", "Plan", "Forged", {})

    def test_bpa_cannot_access_native_drafts(self):
        with self.assertRaises(Denied): self.api.drafts("Allowed")

    def test_record_boundary_and_concurrent_revision(self):
        d = self.doc(); self.f.get_doc.return_value = d
        with self.assertRaises(Changed): self.api._load("FIN-1", self.c, 2)
        d.company = "Other"
        with self.assertRaises(Denied): self.api._load("FIN-1", self.c, 3)

    def test_post_does_not_fabricate_a_posted_journal(self):
        with patch.object(self.api, "_load", return_value=self.doc("Review", "Approved")):
            with self.assertRaises(ValueError): self.api.transition_record("Allowed", "FIN-1", 3, "post")

    def test_retry_returns_committed_result_before_revision_check(self):
        d = self.doc(status="Approved", revision=4)
        d.payload_json = json.dumps({"requests": {"same-id": {"action": "approve"}}})
        with patch.object(self.api, "_load", return_value=d):
            result = self.api.transition_record("Allowed", d.name, 3, "approve", request_id="same-id")
        self.assertEqual(result["revision"], 4)

    def test_foreign_maker_cannot_submit_another_persons_record(self):
        with patch.object(self.api, "_load", return_value=self.doc()):
            with self.assertRaises(Denied): self.api.transition_record("Allowed", "FIN-1", 3, "submit")

    def test_plan_cannot_rewrite_another_record_kind(self):
        with patch.object(self.api, "_load", return_value=self.doc("Snapshot")):
            with self.assertRaisesRegex(ValueError, "type cannot be changed"):
                self.api.save_record("Allowed", "Plan", "Forged", {}, "FIN-1", 3)

    def test_stream_reads_beyond_first_thousand_with_native_permissions(self):
        self.f.get_list.side_effect = [[Row(name=str(i)) for i in range(1000)], [Row(name="last")]]
        self.assertEqual(len(list(self.api._rows("GL Entry", {"company": "Allowed"}, ["name"]))), 1001)
        self.assertEqual(self.f.get_list.call_args.kwargs["limit_start"], 1000)

    def test_mixed_roles_union_and_operations_owner_filter(self):
        self.f.get_roles.return_value = ["VCL Financial Planner", "VCL Finance Operator"]
        self.assertTrue(self.guards.record_has_permission(self.doc("Review"), user="planner"))
        self.f.get_roles.return_value = ["VCL Finance Operator"]
        self.assertFalse(self.guards.record_has_permission(self.doc("Review"), user="operator"))

    def test_direct_record_write_and_delete_denied(self):
        d = self.doc(); d.flags = Row()
        with self.assertRaises(Denied): self.guards.record_validate(d)
        with self.assertRaises(Denied): self.guards.record_delete(d)

    def test_client_supplied_write_flags_cannot_bypass_guard(self):
        d = self.doc(); d.flags = Row(finance_workspace_write=True, ignore_permissions=True)
        with self.assertRaises(Denied): self.guards.record_validate(d)
        with self.guards.allow_record_write(): self.guards.record_validate(d)
        with self.assertRaises(Denied): self.guards.record_validate(d)

    def test_frappe_ptype_hook_rejects_raw_write(self):
        self.f.get_roles.return_value = ["VCL Financial Planner"]
        self.assertFalse(self.guards.record_has_permission(self.doc(), user="planner", ptype="write"))

    def test_planner_source_permission_cannot_expose_raw_payroll_or_bank_rows(self):
        self.f.get_meta = Mock(return_value=Row(permissions=[Row(role="VCL Financial Planner", read=1), Row(role="Accounts User", read=1)]))
        self.assertEqual(self.guards.gl_query("planner"), "1=0")
        self.assertFalse(self.guards.gl_has_permission(Row(), user="planner", ptype="read"))
        with self.guards.allow_management_gl(): self.assertEqual(self.guards.gl_query("planner"), "")
        self.assertEqual(self.guards.gl_query("planner"), "1=0")
        self.f.get_roles.return_value = ["VCL Financial Planner", "Accounts User"]
        self.assertEqual(self.guards.gl_query("planner"), "")

    def test_edited_journal_invalidates_review(self):
        class Review(Row):
            def save(self, **kw): self.saved = True
        review = Review(status="Approved", payload_json=json.dumps({"document_hash": "old"}), revision=1, flags=Row())
        self.f.db.exists.return_value = True
        self.f.db.get_value.return_value = "FIN-1"
        self.f.get_doc.return_value = review
        self.f.utils = Row(now=lambda: "2026-11-12")
        journal = Row(company="Allowed", name="JE-1", is_new=lambda: False, as_dict=lambda: {"accounts": [{"debit": 25}]})
        self.guards.journal_validate(journal)
        self.assertEqual(review.status, "Changes requested")
        self.assertEqual(review.revision, 2)
        self.assertTrue(review.saved)

    def test_cancelled_source_cannot_remain_labelled_posted(self):
        class Review(Row):
            def save(self, **kw): self.saved = True
        review = Review(status="Posted", payload_json="{}", revision=3)
        self.f.db.exists.return_value = True
        self.f.db.get_value.return_value = "FIN-1"
        self.f.get_doc.return_value = review
        self.f.utils = Row(now=lambda: "2026-11-12")
        self.guards.journal_on_cancel(Row(company="Allowed", name="JE-1"))
        self.assertEqual(review.status, "Cancelled")
        self.assertEqual(json.loads(review.payload_json)["posting"]["erpnext"], "Cancelled")
        self.assertEqual(review.revision, 4)

    def test_review_cannot_replace_preparation_evidence(self):
        self.f.get_roles.return_value = ["VCL Financial Controller"]
        d = self.doc("Close")
        d.payload_json = json.dumps({"tasks": [{"id": "1", "status": "Prepared", "owner": "maker", "prepared_by": "maker", "evidence": ["original"]}]})
        with patch.object(self.api, "_load", return_value=d):
            with self.assertRaisesRegex(ValueError, "Review cannot replace"):
                self.api.save_close_task("Allowed", d.name, 3, "1", "Reviewed", "maker", ["replacement"], "Reviewed")

    def test_native_budget_publication_keeps_the_approved_monthly_shape(self):
        class Budget(Row):
            def insert(self):
                self.budget_distribution = [Row(amount=self.budget_amount / 12)] * 12
                self.name = "BUD-1"
            def set(self, field, values): self[field] = [Row(v) for v in values]
            def save(self): pass
            def add_comment(self, *args): pass
            def submit(self): self.submitted = True
        created = []
        self.f.get_meta = Mock(return_value=Row(has_field=lambda field: True, fields=[Row(fieldname="action_if_annual_budget_exceeded")]))
        def make(values):
            doc = Budget(values); created.append(doc); return doc
        self.f.get_doc = make
        from vcl_finance.workspace import core
        p = {"fiscal_year": "2026", "periods": core.periods("2026-01-01", 12), "lines": [
            {"category": "Expense", "account": "Rent", "cost_center": "Production", "values": [100] + [200] * 11},
            {"category": "Expense", "account": "Rent", "cost_center": "Production", "values": [20] * 12}]}
        self.assertEqual(self.api._publish_budget(self.c, p, "FIN-1"), ["BUD-1"])
        self.assertEqual(len(created), 1)
        self.assertEqual([r.amount for r in created[0].budget_distribution], [120] + [220] * 11)
        self.assertEqual(created[0].action_if_annual_budget_exceeded, "Ignore")
        self.assertTrue(created[0].submitted)


if __name__ == "__main__": unittest.main()
