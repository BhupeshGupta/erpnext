# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import cint, validate_email_add
from frappe import throw, msgprint, _

from frappe.model.document import Document

class Warehouse(Document):
	nsm_parent_field = 'parent_warehouse'

	def autoname(self):
		suffix = " - " + frappe.db.get_value("Company", self.company, "abbr")
		if not self.warehouse_name.endswith(suffix):
			self.name = self.warehouse_name + suffix

	def validate(self):
		if self.email_id and not validate_email_add(self.email_id):
				throw(_("Please enter valid Email Id"))

		self.update_parent_account()

	def update_parent_account(self):

		if not getattr(self, "__islocal", None) \
			and (self.create_account_under != frappe.db.get_value("Warehouse", self.name, "create_account_under")):

				self.validate_parent_account()

				warehouse_account = frappe.db.get_value("Account",
					{"account_type": "Warehouse", "company": self.company, "master_name": self.name},
					["name", "parent_account"])

				if warehouse_account and warehouse_account[1] != self.create_account_under:
					acc_doc = frappe.get_doc("Account", warehouse_account[0])
					acc_doc.parent_account = self.create_account_under
					acc_doc.save()

	def on_update(self):
		self.create_account_head()
		self.update_nsm_model()

	def create_account_head(self):
		if cint(frappe.defaults.get_global_default("auto_accounting_for_stock")):
			if not frappe.db.get_value("Account", {"account_type": "Warehouse",
					"master_name": self.name}):
				if self.get("__islocal") or not frappe.db.get_value(
						"Stock Ledger Entry", {"warehouse": self.name}):
					self.validate_parent_account()
					ac_doc = frappe.get_doc({
						"doctype": "Account",
						'account_name': self.warehouse_name,
						'parent_account': self.create_account_under,
						'group_or_ledger':'Ledger',
						'company':self.company,
						"account_type": "Warehouse",
						"master_name": self.name,
						"freeze_account": "No"
					})
					ac_doc.ignore_permissions = True
					ac_doc.insert()
					msgprint(_("Account head {0} created").format(ac_doc.name))

	def validate_parent_account(self):
		if not self.company:
			frappe.throw(_("Warehouse {0}: Company is mandatory").format(self.name))

		if not self.create_account_under:
			parent_account = frappe.db.get_value("Account",
				{"account_name": "Stock Assets", "company": self.company})

			if parent_account:
				self.create_account_under = parent_account
			else:
				frappe.throw(_("Please enter parent account group for warehouse {0}").format(self.name))
		elif frappe.db.get_value("Account", self.create_account_under, "company") != self.company:
			frappe.throw(_("Warehouse {0}: Parent account {1} does not bolong to the company {2}")
				.format(self.name, self.create_account_under, self.company))


	def on_trash(self):
		# delete bin
		bins = frappe.db.sql("select * from `tabBin` where warehouse = %s",
			self.name, as_dict=1)
		for d in bins:
			if d['actual_qty'] or d['reserved_qty'] or d['ordered_qty'] or \
					d['indented_qty'] or d['projected_qty'] or d['planned_qty']:
				throw(_("Warehouse {0} can not be deleted as quantity exists for Item {1}").format(self.name, d['item_code']))
			else:
				frappe.db.sql("delete from `tabBin` where name = %s", d['name'])

		warehouse_account = frappe.db.get_value("Account",
			{"account_type": "Warehouse", "master_name": self.name})
		if warehouse_account:
			frappe.delete_doc("Account", warehouse_account)

		if frappe.db.sql("""select name from `tabStock Ledger Entry`
				where warehouse = %s""", self.name):
			throw(_("Warehouse can not be deleted as stock ledger entry exists for this warehouse."))

		self.update_nsm_model()

	def before_rename(self, olddn, newdn, merge=False):
		# Add company abbr if not provided
		from erpnext.setup.doctype.company.company import get_name_with_abbr
		new_warehouse = get_name_with_abbr(newdn, self.company)

		if merge:
			if not frappe.db.exists("Warehouse", new_warehouse):
				frappe.throw(_("Warehouse {0} does not exist").format(new_warehouse))

			if self.company != frappe.db.get_value("Warehouse", new_warehouse, "company"):
				frappe.throw(_("Both Warehouse must belong to same Company"))

			frappe.db.sql("delete from `tabBin` where warehouse=%s", olddn)

		from erpnext.accounts.utils import rename_account_for
		rename_account_for("Warehouse", olddn, newdn, merge, self.company)

		return new_warehouse

	def after_rename(self, olddn, newdn, merge=False):
		if merge:
			self.recalculate_bin_qty(newdn)

	def recalculate_bin_qty(self, newdn):
		from erpnext.utilities.repost_stock import repost_stock
		frappe.db.auto_commit_on_many_writes = 1
		frappe.db.set_default("allow_negative_stock", 1)

		for item in frappe.db.sql("""select distinct item_code from (
			select name as item_code from `tabItem` where ifnull(is_stock_item, 'Yes')='Yes'
			union
			select distinct item_code from tabBin) a"""):
				repost_stock(item[0], newdn)

		frappe.db.set_default("allow_negative_stock",
			frappe.db.get_value("Stock Settings", None, "allow_negative_stock"))
		frappe.db.auto_commit_on_many_writes = 0


	def update_nsm_model(self):
		"""update lft, rgt indices for nested set model"""
		import frappe
		import frappe.utils.nestedset
		frappe.utils.nestedset.update_nsm(self)
