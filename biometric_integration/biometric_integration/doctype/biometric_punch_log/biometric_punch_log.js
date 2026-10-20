frappe.ui.form.on("Biometric Punch Log", {
	refresh(frm) {
		if (["Unmapped", "Error", "Device Not Approved", "Pending"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Reprocess"), () =>
				frappe.call("biometric_integration.engine.reprocess", { names: [frm.doc.name] }).then(() => frm.reload_doc()));
		}
		if (frm.doc.status === "Unmapped") {
			frm.add_custom_button(__("Map to Employee"), () =>
				frappe.prompt({ fieldname: "employee", fieldtype: "Link", options: "Employee", label: __("Employee"), reqd: 1 }, (v) =>
					frappe.call("biometric_integration.api.monitor.map_user", { user_id: frm.doc.user_id, employee: v.employee })
						.then(() => frm.reload_doc()), __("Who is device user {0}?", [frm.doc.user_id])));
		}
	},
});
