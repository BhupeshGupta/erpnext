# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import cstr, flt, cint
from frappe import _

def execute(filters=None):
	account_details = {}
	for acc in frappe.db.sql("""select name, group_or_ledger from tabAccount""", as_dict=1):
			account_details.setdefault(acc.name, acc)

	validate_filters(filters, account_details)

	columns = get_columns()

	res = get_result(filters, account_details)

	return columns, res

def validate_filters(filters, account_details):
	if filters.get("account") and not account_details.get(filters.account):
		frappe.throw(_("Account {0} does not exists").format(filters.account))

	if filters.get("account") and filters.get("group_by_account") \
			and account_details[filters.account].group_or_ledger == "Ledger"\
			and cint(filters.get("show_like_accounts")) == 0:
		frappe.throw(_("Can not filter based on Account, if grouped by Account"))

	if filters.get("voucher_no") and filters.get("group_by_voucher"):
		frappe.throw(_("Can not filter based on Voucher No, if grouped by Voucher"))

	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date"))

def get_columns():
	return [_("Posting Date") + ":Date:100", _("Account") + ":Link/Account:200", _("Debit") + ":Float:100",
		_("Credit") + ":Float:100", _("Voucher Type") + "::120", _("Voucher No") + ":Dynamic Link/Voucher Type:160",
		_("Against Account") + "::120", _("Cost Center") + ":Link/Cost Center:100", _("Remarks") + "::400"]

def get_result(filters, account_details):
	gl_entries = get_gl_entries(filters)

	data = get_data_with_opening_closing(filters, account_details, gl_entries)

	result = get_result_as_list(data)

	return result

def get_gl_entries(filters):
	group_by_condition = "group by voucher_type, voucher_no, account" \
		if filters.get("group_by_voucher") else "group by name"

	gl_entries = frappe.db.sql("""select posting_date, account,
			sum(ifnull(debit, 0)) as debit, sum(ifnull(credit, 0)) as credit,
			voucher_type, voucher_no, cost_center, remarks, is_opening, against
		from `tabGL Entry`
		where {conditions} {group_by_condition}
		order by posting_date"""\
		.format(conditions=get_conditions(filters), group_by_condition=group_by_condition),
		filters, as_dict=1, debug=True)

	return gl_entries

def get_conditions(filters):
	conditions = []
	if filters.get("account"):
		account_like = filters.get("account")
		if cint(filters.get("show_like_accounts")) == 1:
			account_like = filters.get("account").split("-")[0].strip() + " - "
			account_list_dict = frappe.db.sql("""SELECT lft, rgt FROM `tabAccount` WHERE NAME LIKE "{}%" AND
			NAME NOT LIKE '%- AGE';""".format(account_like), as_dict=True)
		conditions.append("""account in (select name from tabAccount
			where ({}) and docstatus<2)""".format(" or ".join(["(lft>={} and rgt<={})".format(a.lft, a.rgt) for a in account_list_dict])))
	else:
		conditions.append("posting_date between %(from_date)s and %(to_date)s")

	if filters.get("voucher_no"):
		conditions.append("voucher_no=%(voucher_no)s")

	from flows.utils import get_insight_depth_condition
	depth_condition = get_insight_depth_condition(filters["insight_depth"], old_styp_format_escaped=True)
	if depth_condition:
		conditions.append(depth_condition)

	if cint(filters.get("show_like_accounts")) == 0:
		conditions.append("company=%(company)s")

	from frappe.widgets.reportview import build_match_conditions
	match_conditions = build_match_conditions("GL Entry")
	if match_conditions: conditions.append(match_conditions)

	return "{}".format(" and ".join(conditions)) if conditions else ""

def get_data_with_opening_closing(filters, account_details, gl_entries):
	data = []
	gle_map = initialize_gle_map(gl_entries)

	opening, total_debit, total_credit, gle_map = get_accountwise_gle(filters, gl_entries, gle_map)

	# Opening for filtered account
	if filters.get("account"):
		data += [get_balance_row("Opening", opening), {}]

	for acc, acc_dict in gle_map.items():
		if acc_dict.entries:
			# Opening for individual ledger, if grouped by account
			if filters.get("group_by_account"):
				data.append(get_balance_row("Opening", acc_dict.opening))

			data += acc_dict.entries

			# Totals and closing for individual ledger, if grouped by account
			if filters.get("group_by_account"):
				data += [{"account": "Totals", "debit": acc_dict.total_debit,
					"credit": acc_dict.total_credit},
					get_balance_row("Closing (Opening + Totals)",
						(acc_dict.opening + acc_dict.total_debit - acc_dict.total_credit)), {}]

	# Total debit and credit between from and to date
	if total_debit or total_credit:
		data.append({"account": "Totals", "debit": total_debit, "credit": total_credit})

	# Closing for filtered account
	if filters.get("account"):
		data.append(get_balance_row("Closing (Opening + Totals)",
			(opening + total_debit - total_credit)))

	return data

def initialize_gle_map(gl_entries):
	gle_map = frappe._dict()
	for gle in gl_entries:
		gle_map.setdefault(strip_account(gle.account), frappe._dict({
			"opening": 0,
			"entries": [],
			"total_debit": 0,
			"total_credit": 0,
			"closing": 0
		}))
	return gle_map

def get_accountwise_gle(filters, gl_entries, gle_map):
	opening, total_debit, total_credit = 0, 0, 0

	for gle in gl_entries:
		amount = flt(gle.debit, 3) - flt(gle.credit, 3)
		if filters.get("account") and (gle.posting_date<filters.from_date or cstr(gle.is_opening)=="Yes"):
			gle_map[strip_account(gle.account)].opening += amount
			opening += amount
		elif gle.posting_date <= filters.to_date:
			gle_map[strip_account(gle.account)].entries.append(gle)
			gle_map[strip_account(gle.account)].total_debit += flt(gle.debit, 3)
			gle_map[strip_account(gle.account)].total_credit += flt(gle.credit, 3)

			total_debit += flt(gle.debit, 3)
			total_credit += flt(gle.credit, 3)

	return opening, total_debit, total_credit, gle_map

def get_balance_row(label, balance):
	return {
		"account": label,
		"debit": balance if balance > 0 else 0,
		"credit": -1*balance if balance < 0 else 0,
	}

def get_result_as_list(data):
	result = []
	for d in data:
		result.append([d.get("posting_date"), d.get("account"), d.get("debit"),
			d.get("credit"), d.get("voucher_type"), d.get("voucher_no"),
			d.get("against"), d.get("cost_center"), d.get("remarks")])

	return result


def strip_account(account):
	return account.split('-')[0].strip().lower()