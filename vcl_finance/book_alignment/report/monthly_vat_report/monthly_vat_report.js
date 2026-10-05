// Monthly VAT report: every document that moved the VAT account in a month, by
// category, with the net payable and a check against the ledger. Logic lives in
// the .py; this declares the filters and bolds the subtotal / total rows.
const MONTHS = [
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
];

frappe.query_reports["Monthly VAT Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company") || "Vimit Converters Limited",
			reqd: 1,
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Int",
			default: new Date().getFullYear(),
			reqd: 1,
		},
		{
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: MONTHS.join("\n"),
			// the month just ended: VAT is reported in arrears
			default: MONTHS[(new Date().getMonth() + 11) % 12],
			reqd: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.bold) {
			value = `<b>${value}</b>`;
		}
		return value;
	},
};
