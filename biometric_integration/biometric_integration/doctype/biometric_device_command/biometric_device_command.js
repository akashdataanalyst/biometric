frappe.ui.form.on("Biometric Device Command", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (["Pending", "Sent"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Cancel Command"), () => frm.call("cancel_command").then(() => frm.reload_doc()));
		}
		if (["Failed", "Cancelled", "Sent"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Send Again"), () => frm.call("retry").then(() => frm.reload_doc()));
		}
	},
	employee(frm) {
		if (!frm.doc.employee) return;
		frappe.db.get_value("Employee", frm.doc.employee, ["attendance_device_id", "employee_name"]).then((r) => {
			frm.set_value("user_id", r.message.attendance_device_id);
			frm.set_value("user_name", r.message.employee_name);
		});
	},
});
