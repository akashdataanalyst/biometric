frappe.pages["biometric-simulator"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Biometric Simulator"), single_column: true });
	new BiometricSimulator(page);
};

const BIO_PROTOCOLS = [
	["ADMS (eSSL / ZKTeco Push)", __("eSSL / ZKTeco (K23, K28, X990 ...)"), "fingerprint"],
	["Hikvision ISAPI", __("Hikvision face terminal"), "camera"],
	["Generic Webhook", __("Any machine / software via webhook"), "link"],
	["LAN Agent (ZK Pull)", __("ZK machine on office LAN"), "network"],
];

class BiometricSimulator {
	constructor(page) {
		this.page = page;
		page.add_inner_button(__("Live Monitor"), () => frappe.set_route("biometric-live-monitor"));
		page.add_inner_button(__("Settings"), () => frappe.set_route("Form", "Biometric Settings"));
		this.$body = $(`<div class="bio-sim">
			<style>
				.bio-sim { padding: 4px 0 30px; max-width: 1100px; }
				.bio-step { background: var(--card-bg); border:1px solid var(--border-color); border-radius:10px; padding:16px 18px; margin-bottom:16px; }
				.bio-step h4 { font-size:15px; margin:0 0 4px; }
				.bio-step p.help { color: var(--text-muted); margin-bottom: 12px; }
				.bio-protos { display:grid; grid-template-columns: repeat(auto-fit, minmax(210px,1fr)); gap:10px; }
				.bio-proto { border:1px solid var(--border-color); border-radius:8px; padding:10px 12px; cursor:pointer; background: var(--fg-color); text-align:left; }
				.bio-proto:hover { border-color: var(--primary); }
				.bio-proto.active { border-color: var(--primary); box-shadow: 0 0 0 1px var(--primary); }
				.bio-proto b { display:block; }
				.bio-proto span { color: var(--text-muted); font-size:12px; }
				.bio-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
				.bio-results table { width:100%; font-size:13px; }
				.bio-results td, .bio-results th { padding:7px 6px; border-bottom:1px solid var(--border-color); }
				.bio-results th { color: var(--text-muted); font-weight:500; }
			</style>
			<div class="bio-step"><h4>1. ${__("Choose a machine")}</h4>
				<p class="help">${__("No machine yet? Create a virtual one. It talks the exact same protocol as the real machine, so everything after this point is tested for real.")}</p>
				<div class="bio-protos"></div>
				<div class="bio-device-field" style="max-width:420px;margin-top:12px"></div>
			</div>
			<div class="bio-step"><h4>2. ${__("Employees")}</h4>
				<p class="help">${__("The machine sends a user ID (PIN). It is matched to the Employee Attendance Device ID, Emp Code (Employee Number) or Employee ID, as set in Settings > Match Employee By.")}</p>
				<div class="bio-emp-status text-muted"></div>
				<div class="bio-actions"><button class="btn btn-default btn-sm bio-assign">${__("Auto-assign test IDs (101, 102 ...)")}</button></div>
			</div>
			<div class="bio-step"><h4>3. ${__("Send punches")}</h4>
				<div class="bio-punch-form"></div>
				<div class="bio-actions">
					<button class="btn btn-primary btn-sm bio-send">${__("Send Punch")}</button>
					<button class="btn btn-default btn-sm bio-unknown">${__("Send Unknown ID (test unmapped)")}</button>
					<button class="btn btn-default btn-sm bio-day">${__("Simulate a Full Day (everyone IN & OUT)")}</button>
				</div>
			</div>
			<div class="bio-step bio-results"><h4>${__("Results")}</h4><div class="bio-result-list text-muted">${__("Nothing sent yet.")}</div></div>
			<div class="bio-step"><h4>4. ${__("Check & clean up")}</h4>
				<div class="bio-actions">
					<button class="btn btn-default btn-sm bio-selftest">${__("Run Self-Test")}</button>
					<button class="btn btn-default btn-sm bio-monitor">${__("Open Live Monitor")}</button>
					<button class="btn btn-default btn-sm bio-download">${__("Download PC Simulator (test from outside)")}</button>
					<button class="btn btn-danger btn-sm bio-clear">${__("Delete All Test Data")}</button>
				</div>
				<p class="help" style="margin-top:10px">${__("The PC simulator runs on any laptop with Python and behaves like a real eSSL machine over the internet: handshake, heartbeat, punches and remote commands. Use it to prove the AWS port, DNS and nginx are right before the real machine arrives.")}</p>
			</div></div>`).appendTo(page.body);

		this.results = [];
		this.make_protocols();
		this.make_fields();
		this.bind();
		this.load_employee_status();
	}

	make_protocols() {
		const $p = this.$body.find(".bio-protos");
		BIO_PROTOCOLS.forEach(([proto, label]) => {
			$(`<button class="bio-proto" data-proto="${proto}"><b>${label}</b><span>${proto}</span></button>`)
				.appendTo($p)
				.on("click", () =>
					frappe.call({
						method: "biometric_integration.api.simulator.create_virtual_device",
						args: { protocol: proto },
						callback: (r) => {
							this.device_field.set_value(r.message);
							frappe.show_alert({ message: __("Using virtual device {0}", [r.message]), indicator: "green" });
						},
					})
				);
		});
	}

	make_fields() {
		this.device_field = frappe.ui.form.make_control({
			parent: this.$body.find(".bio-device-field"),
			df: {
				fieldname: "device",
				fieldtype: "Link",
				options: "Biometric Device",
				label: __("Or pick any device (real ones too)"),
				change: () => this.highlight(),
			},
			render_input: true,
		});
		this.form = new frappe.ui.FieldGroup({
			parent: this.$body.find(".bio-punch-form"),
			fields: [
				{ fieldname: "employee", fieldtype: "Link", options: "Employee", label: __("Employee") },
				{ fieldname: "user_id", fieldtype: "Data", label: __("Or Device User ID") },
				{ fieldtype: "Column Break" },
				{ fieldname: "punch_type", fieldtype: "Select", label: __("Punch"), options: "Auto\nIN\nOUT", default: "Auto" },
				{ fieldname: "punch_time", fieldtype: "Datetime", label: __("Time (blank = now)") },
				{ fieldtype: "Column Break" },
				{ fieldname: "via_http", fieldtype: "Check", label: __("Send over the network"),
					description: __("Calls this server's public address like the machine would (tests AWS port / DNS / nginx).") },
				{ fieldname: "dry_run", fieldtype: "Check", label: __("Dry run (don't create checkin)") },
			],
		});
		this.form.make();
	}

	highlight() {
		const dev = this.device_field.get_value();
		this.$body.find(".bio-proto").removeClass("active");
		if (!dev) return;
		frappe.db.get_value("Biometric Device", dev, "protocol").then((r) =>
			this.$body.find(`.bio-proto[data-proto="${(r.message || {}).protocol}"]`).addClass("active"));
	}

	device() {
		const d = this.device_field.get_value();
		if (!d) frappe.throw(__("Choose a machine in step 1 first"));
		return d;
	}

	bind() {
		this.$body.find(".bio-assign").on("click", () =>
			frappe.call({
				method: "biometric_integration.api.simulator.auto_assign_device_ids",
				freeze: true,
				callback: (r) => {
					frappe.show_alert({ message: __("{0} employees got a test ID", [r.message.assigned.length]), indicator: "green" });
					this.load_employee_status();
				},
			}));
		this.$body.find(".bio-send").on("click", () => this.send(this.form.get_values()));
		this.$body.find(".bio-unknown").on("click", () =>
			this.send({ ...this.form.get_values(), employee: null, user_id: String(90000 + Math.floor(Math.random() * 9999)) }));
		this.$body.find(".bio-day").on("click", () => {
			const device = this.device();
			frappe.prompt([
				{ fieldname: "date", fieldtype: "Date", label: __("Date"), default: frappe.datetime.get_today(), reqd: 1 },
				{ fieldname: "in_time", fieldtype: "Time", label: __("IN around"), default: "09:00:00", reqd: 1 },
				{ fieldname: "out_time", fieldtype: "Time", label: __("OUT around"), default: "18:00:00", reqd: 1 },
				{ fieldname: "limit", fieldtype: "Int", label: __("Max employees"), default: 20 },
			], (v) =>
				frappe.call({
					method: "biometric_integration.api.simulator.simulate_day",
					args: { device, ...v },
					freeze: true,
					freeze_message: __("Punching for everyone..."),
					callback: (r) => {
						const m = r.message;
						frappe.msgprint({ title: __("Full day simulated"),
							message: __("{0} punches sent.", [m.punches]) + "<br>" +
								Object.entries(m.statuses).map(([k, v]) => `${biometric.indicator(k)} ${v}`).join("<br>") });
					},
				}), __("Simulate a Full Day"), __("Go"));
		});
		this.$body.find(".bio-selftest").on("click", () => biometric.self_test(1));
		this.$body.find(".bio-monitor").on("click", () => frappe.set_route("biometric-live-monitor"));
		this.$body.find(".bio-download").on("click", () => window.open("/api/method/biometric_integration.api.setup_info.download_simulator"));
		this.$body.find(".bio-clear").on("click", () =>
			frappe.confirm(__("Delete all test punches and the Employee Checkins they created? Real punches are not touched."), () =>
				frappe.call({
					method: "biometric_integration.api.simulator.clear_test_data",
					args: { delete_virtual_devices: 1 },
					freeze: true,
					callback: (r) => {
						const m = r.message;
						frappe.msgprint(__("Deleted {0} test punches, {1} checkins and {2} virtual devices. {3} checkins already used in Attendance were kept.",
							[m.logs, m.checkins, m.devices, m.kept_with_attendance]));
						this.device_field.set_value("");
						this.results = [];
						this.render_results();
					},
				})));
	}

	load_employee_status() {
		frappe.call("biometric_integration.api.simulator.employee_match_status").then((r) => {
			const m = r.message;
			this.$body.find(".bio-emp-status").html(
				__("{0} of {1} active employees can be matched. Matching by: {2}.", [`<b>${m.mapped}</b>`, m.total, `<b>${m.mode}</b>`]));
		});
	}

	send(values) {
		const device = this.device();
		frappe.call({
			method: "biometric_integration.api.simulator.simulate_punch",
			args: { device, ...values },
			freeze: true,
			freeze_message: __("Sending as the machine would..."),
			callback: (r) => {
				this.results.unshift(r.message);
				this.render_results();
			},
		});
	}

	render_results() {
		const $l = this.$body.find(".bio-result-list");
		if (!this.results.length) return $l.html(__("Nothing sent yet."));
		$l.removeClass("text-muted").html(`<table><thead><tr><th>${__("Time")}</th><th>${__("Employee")}</th><th>${__("IN/OUT")}</th>
			<th>${__("Status")}</th><th>${__("Message")}</th><th></th></tr></thead><tbody>${this.results.map((res, i) => {
				const log = res.log || {};
				return `<tr><td>${log.punch_time ? frappe.datetime.str_to_user(log.punch_time) : ""}</td>
					<td>${frappe.utils.escape_html(log.employee_name || log.employee || "—")}</td>
					<td><b>${log.log_type || ""}</b></td>
					<td>${log.status ? biometric.indicator(log.status) : `<span class="indicator-pill red">${__("Failed")}</span>`}</td>
					<td class="small text-muted">${frappe.utils.escape_html(log.message || (log.status ? "" : (res.response || "").slice(0, 120)))}</td>
					<td><button class="btn btn-xs btn-default" data-i="${i}">${__("Details")}</button></td></tr>`;
			}).join("")}</tbody></table>`);
		$l.find("button[data-i]").on("click", (e) => biometric.show_result(this.results[$(e.currentTarget).attr("data-i")]));
	}
}
