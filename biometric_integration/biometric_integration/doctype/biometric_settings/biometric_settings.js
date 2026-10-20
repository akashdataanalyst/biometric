frappe.ui.form.on("Biometric Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Run Self-Test"), () => biometric.self_test(1)).addClass("btn-primary");
		frm.add_custom_button(__("Live Monitor"), () => frappe.set_route("biometric-live-monitor"), __("Open"));
		frm.add_custom_button(__("Simulator (test without machine)"), () => frappe.set_route("biometric-simulator"), __("Open"));
		frm.add_custom_button(__("Devices"), () => frappe.set_route("List", "Biometric Device"), __("Open"));
		frm.add_custom_button(__("Punch Logs"), () => frappe.set_route("List", "Biometric Punch Log"), __("Open"));
		frm.add_custom_button(__("Import File"), () => frappe.new_doc("Biometric Log Import"), __("Open"));

		frm.add_custom_button(__("Show API Token"), () =>
			frappe.call("biometric_integration.api.setup_info.reveal_token").then((r) =>
				frappe.msgprint({ title: __("API Token"), message: `<code style="font-size:14px">${r.message || __("Not set")}</code>` })
			), __("API Token"));
		frm.add_custom_button(__("Generate New API Token"), () =>
			frappe.confirm(__("Machines / agents using the old token will stop working until updated. Continue?"), () =>
				frappe.call("biometric_integration.api.setup_info.generate_token").then((r) => {
					frm.reload_doc();
					frappe.msgprint({ title: __("New API Token"), message: `<code style="font-size:14px">${r.message}</code>` });
				})
			), __("API Token"));

		frm.add_custom_button(__("Device Simulator (run on any PC)"), () =>
			window.open("/api/method/biometric_integration.api.setup_info.download_simulator"), __("Download"));
		frm.add_custom_button(__("LAN Agent (for machines on office LAN)"), () =>
			window.open("/api/method/biometric_integration.api.setup_info.download_lan_agent"), __("Download"));

		frappe.call("biometric_integration.api.setup_info.get_token_help").then((r) =>
			frm.get_field("token_help").$wrapper.html(r.message || ""));

		const w = frm.get_field("setup_status").$wrapper;
		w.html(`<div class="text-muted">${__("Checking setup...")}</div>`);
		frappe.call({
			method: "biometric_integration.api.simulator.run_self_test",
			args: { network: 0 },
			callback: (r) => w.html(biometric.render_checks(r.message) +
				`<p class="text-muted small" style="margin-top:8px">${__("Click 'Run Self-Test' to also test that machines can reach this server over the internet.")}</p>`),
		});
	},
});
