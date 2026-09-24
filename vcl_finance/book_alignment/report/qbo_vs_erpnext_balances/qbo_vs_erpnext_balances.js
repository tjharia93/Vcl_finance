// QBO vs ERPNext balances against the signed accounts. Logic lives in the .py;
// this only declares filters and bolds the section, PBT and check rows.
frappe.query_reports["QBO vs ERPNext Balances"] = {
	filters: [
		{
			fieldname: "as_at",
			label: __("As at"),
			fieldtype: "Date",
			default: "2025-12-31",
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group by"),
			fieldtype: "Select",
			options: "Audited line\nAccount",
			default: "Audited line",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: "Vimit Converters Limited",
			reqd: 1,
		},
		{
			fieldname: "erp_basis",
			label: __("ERPNext line basis"),
			fieldtype: "Select",
			options: "Decided, then seed, then proposed\nDecided, then proposed, then seed",
			default: "Decided, then seed, then proposed",
		},
	],
	tree: true,
	name_field: "row_id",
	parent_field: "parent_row",
	initial_depth: 2,
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		return data && data.bold ? `<b>${value}</b>` : value;
	},
};
