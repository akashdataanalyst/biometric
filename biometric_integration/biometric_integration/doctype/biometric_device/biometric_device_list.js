frappe.listview_settings["Biometric Device"] = {
	add_fields: ["online_status", "status", "is_virtual"],
	get_indicator(doc) {
		if (doc.status === "Pending Approval") return [__("Pending Approval"), "orange", "status,=,Pending Approval"];
		if (doc.status === "Disabled") return [__("Disabled"), "gray", "status,=,Disabled"];
		const c = { Online: "green", Offline: "red", "Never Connected": "gray" }[doc.online_status] || "gray";
		return [__(doc.online_status || "Never Connected"), c, `online_status,=,${doc.online_status}`];
	},
	onload(list) {
		list.page.add_inner_button(__("Live Monitor"), () => frappe.set_route("biometric-live-monitor"));
		list.page.add_inner_button(__("Simulator"), () => frappe.set_route("biometric-simulator"));
		list.page.add_inner_button(__("Settings"), () => frappe.set_route("Form", "Biometric Settings"));
	},
};
