# Copyright (c) 2026, Vimit Converters Limited
# For license information, please see license.txt

"""Posting Map — the account defaults petty cash posts through.

One row per (company, source_type, source_key). The row is a *default*, not a
rule: `is_default` decides whether the posting run pre-fills it, and `approved`
decides whether it may reach a ledger at all. An unapproved or missing map row
blocks the posting run rather than guessing an account.
"""

import frappe
from frappe import _
from frappe.model.document import Document


# Source families that really do have exactly one variant, so `source_key` just
# repeats the type. This set must match what the MIRROR writes onto entries —
# nothing else — because a key that disagrees with the entries matches no line and
# fails silently.
#
# Parking and Loan were in here and should not have been. Parking carries a number
# plate per vehicle (KBT 972, KCB 430, KAY 635, KAP 466, KBQ 788) and Loan carries
# nothing at all, so normalising them to "Parking" and "Loan" produced rows that
# could never match an entry. Neither family has been mappable since this was
# written, and the only symptom was lines staying unmapped after somebody mapped
# them. Map Parking with a BLANK key and is_default to catch every plate at once.
SINGLETON_SOURCES = {"Bike Fuel", "Forklift"}

# Account root types that cannot post without a party on the Journal Entry line.
PARTY_REQUIRED_ACCOUNT_TYPES = {"Receivable", "Payable"}


class PostingMap(Document):
	def autoname(self):
		self.name = "{0}-{1}-{2}".format(
			(self.company or "").strip(),
			(self.source_type or "").strip(),
			(self.source_key or "").strip(),
		)

	def validate(self):
		self.normalise_source_key()
		self.validate_account_company()
		self.validate_party_type()
		self.validate_approval_is_actionable()

	def normalise_source_key(self):
		"""Singleton families repeat the type; voucher categories are upper-case.

		A blank key is left blank on purpose: with `is_default` it is the family
		default that catches every key in the family, which is how Parking is
		mapped once rather than once per plate.
		"""
		self.source_key = (self.source_key or "").strip()
		if not self.source_key:
			return
		if self.source_type in SINGLETON_SOURCES:
			self.source_key = self.source_type
		elif self.source_type == "Voucher Category":
			self.source_key = self.source_key.upper()

	def validate_account_company(self):
		"""An account from another company would post into the wrong books."""
		if not self.erp_account:
			return

		account = frappe.db.get_value(
			"Account",
			self.erp_account,
			["company", "is_group", "disabled"],
			as_dict=True,
		)
		if not account:
			frappe.throw(_("Account {0} does not exist.").format(self.erp_account))

		if account.company != self.company:
			frappe.throw(
				_("Account {0} belongs to {1}, but this map row is for {2}.").format(
					self.erp_account, account.company, self.company
				)
			)
		if account.is_group:
			frappe.throw(
				_("{0} is a group account. Postings must target a leaf account.").format(
					self.erp_account
				)
			)
		if account.disabled:
			frappe.throw(_("Account {0} is disabled.").format(self.erp_account))

	def validate_party_type(self):
		"""Receivable/Payable accounts reject a Journal Entry line with no party."""
		if not self.erp_account:
			return

		account_type = frappe.db.get_value("Account", self.erp_account, "account_type")
		if account_type in PARTY_REQUIRED_ACCOUNT_TYPES and not self.erp_party_type:
			frappe.throw(
				_(
					"{0} is a {1} account, so every Journal Entry line against it needs a "
					"party. Set Party Type."
				).format(self.erp_account, account_type)
			)

	def validate_approval_is_actionable(self):
		"""Approving a row with nothing to post is a silent dead end.

		Unless the row says never_post, where having no account is the whole
		point: the route is deliberately kept out of the books, and approving
		that decision is exactly what makes the lines stop showing as blocked.
		Without this exemption a never_post row could never be approved, so the
		one mechanism for saying "this correctly posts nowhere" did not work.
		"""
		if self.never_post:
			if not (self.never_post_reason or "").strip():
				frappe.throw(_("Say why this route is kept out of the books — an "
				               "unexplained exclusion cannot be reviewed."))
			return
		if self.approved and not self.erp_account:
			frappe.throw(
				_("Cannot approve a map row with no ERP Account — there would be nothing to post.")
			)


def get_mapping(company, source_type, source_key, approved_only=True):
	"""Resolve the default account for one petty cash source.

	Returns the map row as a dict, or None. None means the posting run must
	block that line rather than inventing an account — see `posting.py`.
	"""
	if source_type in SINGLETON_SOURCES:
		source_key = source_type
	elif source_type == "Voucher Category" and source_key:
		source_key = source_key.upper()

	filters = {
		"company": company,
		"source_type": source_type,
		"source_key": source_key,
		"is_default": 1,
	}
	if approved_only:
		filters["approved"] = 1

	rows = frappe.get_all(
		"Posting Map",
		filters=filters,
		fields=[
			"name",
			"erp_account",
			"erp_party_type",
			"qbo_account",
			"qbo_tax_code",
		],
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def set_default(company, source_type, source_key, erp_account=None, qbo_account=None,
				qbo_tax_code=None, erp_party_type=None, approved=0):
	"""Promote a chosen account to the default for this source.

	Called from the 'set as default' checkbox wherever an account is picked.
	Creates the map row if it is new, updates it if it already exists — the
	choice is never applied silently, only when this is called explicitly.
	"""
	if not frappe.has_permission("Posting Map", "write"):
		frappe.throw(_("Not permitted to change posting defaults."), frappe.PermissionError)

	if source_type in SINGLETON_SOURCES:
		source_key = source_type
	elif source_type == "Voucher Category" and source_key:
		source_key = source_key.upper()

	name = "{0}-{1}-{2}".format(company, source_type, source_key)

	if frappe.db.exists("Posting Map", name):
		doc = frappe.get_doc("Posting Map", name)
	else:
		doc = frappe.new_doc("Posting Map")
		doc.company = company
		doc.source_type = source_type
		doc.source_key = source_key

	doc.is_default = 1
	if erp_account is not None:
		doc.erp_account = erp_account
	if erp_party_type is not None:
		doc.erp_party_type = erp_party_type
	if qbo_account is not None:
		doc.qbo_account = qbo_account
	if qbo_tax_code is not None:
		doc.qbo_tax_code = qbo_tax_code
	doc.approved = int(approved or 0)

	doc.save()
	return doc.name
