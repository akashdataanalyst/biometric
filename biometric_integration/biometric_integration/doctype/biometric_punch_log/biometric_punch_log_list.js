frappe.listview_settings["Biometric Punch Log"] = {
	add_fields: ["status", "is_test"],
	get_indicator(doc) {
		return [__(doc.status), biometric.STATUS_COLOR[doc.status] || "gray", `status,=,${doc.status}`];
	},
	onload(list) {
		list.page.add_action_item(__("Reprocess"), () => {
			const names = list.get_checked_items(true);
			frappe.call("biometric_integration.engine.reprocess", { names }).then((r) => {
				frappe.show_alert({ message: __("Reprocessed: {0}", [JSON.stringify(r.message)]), indicator: "green" });
				list.refresh();
			});
		});
		list.page.add_inner_button(__("Reprocess All Unmapped / Errors"), () =>
			frappe.call("biometric_integration.engine.reprocess").then((r) => {
				frappe.msgprint({ title: __("Reprocess"), message: `<pre>${JSON.stringify(r.message, null, 2)}</pre>` });
				list.refresh();
			}));
		list.page.add_inner_button(__("Live Monitor"), () => frappe.set_route("biometric-live-monitor"));
	},
};
