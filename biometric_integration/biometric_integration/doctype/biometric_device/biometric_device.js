// Model dropdown depends on Connection Type; the list comes from biometric_device.py (one source).
biometric.model_options = null;

function set_model_options(frm, clear_if_foreign) {
	const apply = () => {
		const options = biometric.model_options[frm.doc.protocol] || [];
		// DocField has no ignore_validation property, so it is set here: the Autocomplete then keeps
		// a typed model that is not in the list instead of clearing it
		frm.set_df_property("model", "ignore_validation", 1);
		frm.set_df_property("model", "options", options);
		if (clear_if_foreign && frm.doc.model && !options.includes(frm.doc.model)) frm.set_value("model", "");
	};
	if (biometric.model_options) return apply();
	frappe
		.xcall("biometric_integration.biometric_integration.doctype.biometric_device.biometric_device.get_model_options")
		.then((r) => {
			biometric.model_options = r || {};
			apply();
		});
}

frappe.ui.form.on("Biometric Device", {
	onload(frm) {
		set_model_options(frm, false);
	},
	protocol(frm) {
		set_model_options(frm, true);
	},
	refresh(frm) {
		if (frm.is_new()) {
			frm.get_field("setup_instructions").$wrapper.html(
				`<p class="text-muted">${__("Save the device to see how to connect it.")}</p>`);
			return;
		}
		if (frm.doc.status === "Pending Approval") {
			frm.dashboard.set_headline_alert(__("This machine connected by itself and is waiting for approval. Its punches are kept and will be processed after you approve."), "orange");
			frm.add_custom_button(__("Approve"), () => {
				frm.set_value("status", "Active");
				frm.save();
			}).addClass("btn-primary");
		}
		const color = { Online: "green", Offline: "red", "Never Connected": "gray" }[frm.doc.online_status];
		frm.page.set_indicator(__(frm.doc.online_status), color);

		frm.add_custom_button(__("Simulate Punch"), () => biometric.simulate_dialog(frm.doc.name, () => frm.reload_doc()), __("Test"));
		frm.add_custom_button(__("Run Self-Test"), () => biometric.self_test(1), __("Test"));
		frm.add_custom_button(__("Punch Logs"), () => frappe.set_route("List", "Biometric Punch Log", { device: frm.doc.name }), __("View"));
		frm.add_custom_button(__("Live Monitor"), () => frappe.set_route("biometric-live-monitor"), __("View"));

		if ((frm.doc.protocol || "").startsWith("ADMS")) {
			const send = (command_type, extra = {}) =>
				frm.call("send_command", { command_type, ...extra }).then((r) =>
					frappe.show_alert({ message: __("Command {0} queued. The machine picks it up on its next heartbeat.", [r.message]), indicator: "green" }));
			frm.add_custom_button(__("Fetch Device Info"), () => send("Fetch Device Info"), __("Machine Commands"));
			frm.add_custom_button(__("Re-upload All Logs"), () => send("Re-upload All Logs"), __("Machine Commands"));
			frm.add_custom_button(__("Fetch Logs for Dates"), () => {
				frappe.prompt([
					{ fieldname: "from_time", fieldtype: "Datetime", label: __("From"), reqd: 1 },
					{ fieldname: "to_time", fieldtype: "Datetime", label: __("To"), reqd: 1, default: frappe.datetime.now_datetime() },
				], (v) => send("Fetch Logs for Date Range", v), __("Fetch Logs"));
			}, __("Machine Commands"));
			frm.add_custom_button(__("Push Employees to Machine"), () =>
				frappe.confirm(__("Send all active employees (of this device's company / branch) with an Attendance Device ID to this machine? They still need to enroll finger / face on the machine."), () =>
					frm.call("push_employees").then((r) => frappe.show_alert({ message: __("{0} users queued", [r.message]), indicator: "green" }))
				), __("Machine Commands"));
			frm.add_custom_button(__("Reboot"), () => frappe.confirm(__("Reboot the machine?"), () => send("Reboot")), __("Machine Commands"));
			frm.add_custom_button(__("Command History"), () => frappe.set_route("List", "Biometric Device Command", { device: frm.doc.name }), __("View"));
		}
		if ((frm.doc.protocol || "").startsWith("LAN Agent")) {
			frm.add_custom_button(__("Download LAN Agent"), () =>
				window.open(`/api/method/biometric_integration.api.setup_info.download_lan_agent?device=${encodeURIComponent(frm.doc.name)}`));
		}
		if (frm.doc.protocol === "File Import") {
			frm.add_custom_button(__("Import File"), () => frappe.new_doc("Biometric Log Import", { device: frm.doc.name }));
		}

		frappe.call({
			method: "biometric_integration.api.setup_info.get_device_setup",
			args: { device: frm.doc.name },
			callback: (r) => frm.get_field("setup_instructions").$wrapper.html(r.message.html),
		});
	},
});
