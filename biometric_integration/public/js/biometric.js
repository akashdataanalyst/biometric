// Shared helpers for the Biometric Integration app
frappe.provide("biometric");

biometric.STATUS_COLOR = {
	Processed: "green",
	Unmapped: "orange",
	Duplicate: "gray",
	Error: "red",
	Ignored: "gray",
	"Device Not Approved": "yellow",
	"Dry Run": "blue",
	Pending: "yellow",
};

biometric.indicator = (status) =>
	`<span class="indicator-pill ${biometric.STATUS_COLOR[status] || "gray"}">${__(status || "")}</span>`;

biometric.render_checks = (res) => {
	const icon = { ok: "✔", warn: "!", fail: "✖" };
	const color = { ok: "var(--green-600)", warn: "var(--orange-600)", fail: "var(--red-600)" };
	const rows = (res.checks || [])
		.map(
			(c) => `<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--border-color)">
			<div style="width:22px;height:22px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;
				color:#fff;font-size:12px;background:${color[c.level]}">${icon[c.level]}</div>
			<div style="flex:1">
				<div><b>${frappe.utils.escape_html(c.label)}</b>
				${c.detail ? `<span class="text-muted"> — ${frappe.utils.escape_html(c.detail)}</span>` : ""}</div>
				${c.level !== "ok" && c.fix ? `<div class="text-muted small">${__("Fix")}: ${frappe.utils.escape_html(c.fix)}</div>` : ""}
			</div></div>`
		)
		.join("");
	const summary =
		res.fails === 0 && res.warns === 0
			? `<div class="alert alert-success">${__("Everything looks good. Machines can send punches.")}</div>`
			: res.fails === 0
			? `<div class="alert alert-warning">${__("Working, with {0} item(s) to review.", [res.warns])}</div>`
			: `<div class="alert alert-danger">${__("{0} problem(s) to fix before machines can work.", [res.fails])}</div>`;
	return summary + rows;
};

biometric.self_test = (network = 1) => {
	const d = new frappe.ui.Dialog({ title: __("Self-Test"), size: "large" });
	d.$body.html(`<div class="text-muted">${__("Running checks...")}</div>`);
	d.show();
	frappe.call({
		method: "biometric_integration.api.simulator.run_self_test",
		args: { network },
		callback: (r) => d.$body.html(biometric.render_checks(r.message)),
		error: () => d.$body.html(`<div class="alert alert-danger">${__("Self-test failed to run")}</div>`),
	});
};

biometric.simulate_dialog = (device, on_done) => {
	const d = new frappe.ui.Dialog({
		title: __("Simulate a Punch"),
		fields: [
			{ fieldname: "device", fieldtype: "Link", options: "Biometric Device", label: __("Device"), reqd: 1, default: device },
			{ fieldname: "employee", fieldtype: "Link", options: "Employee", label: __("Employee") },
			{ fieldname: "user_id", fieldtype: "Data", label: __("Or Device User ID"), description: __("Try an unknown ID to see how unmapped punches look") },
			{ fieldtype: "Column Break" },
			{ fieldname: "punch_type", fieldtype: "Select", label: __("Punch"), options: "Auto\nIN\nOUT", default: "Auto" },
			{ fieldname: "punch_time", fieldtype: "Datetime", label: __("Time"), default: frappe.datetime.now_datetime() },
			{
				fieldname: "via_http",
				fieldtype: "Check",
				label: __("Send over the network (tests DNS, port, nginx)"),
			},
		],
		primary_action_label: __("Send Punch"),
		primary_action(values) {
			frappe.call({
				method: "biometric_integration.api.simulator.simulate_punch",
				args: values,
				freeze: true,
				freeze_message: __("Sending as the machine would..."),
				callback: (r) => {
					d.hide();
					biometric.show_result(r.message);
					on_done && on_done(r.message);
				},
			});
		},
	});
	d.show();
};

biometric.show_result = (res) => {
	const log = res.log || {};
	const req = res.request || {};
	const head = log.status
		? `<p>${biometric.indicator(log.status)} &nbsp;
			${log.employee ? `<b>${frappe.utils.escape_html(log.employee_name || log.employee)}</b> · ` : ""}
			${log.log_type ? `<b>${log.log_type}</b> · ` : ""}${frappe.datetime.str_to_user(log.punch_time)}</p>
			${log.message ? `<p class="text-muted">${frappe.utils.escape_html(log.message)}</p>` : ""}
			${log.employee_checkin ? `<p><a href="/desk/employee-checkin/${log.employee_checkin}">${__("Open Employee Checkin")} ${log.employee_checkin}</a></p>` : ""}`
		: `<div class="alert alert-danger">${__("No punch log was created. See the server response below.")}</div>`;
	const params = req.params && Object.keys(req.params).length ? "?" + new URLSearchParams(req.params).toString() : "";
	const d = new frappe.ui.Dialog({ title: __("Simulator Result"), size: "large" });
	d.$body.html(`${head}
		<h6 style="margin-top:16px">${__("What the machine sent")}</h6>
		<pre style="white-space:pre-wrap;font-size:12px">${frappe.utils.escape_html(`${req.method} ${req.url}${params}\n${Object.entries(req.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}\n\n${req.body}`)}</pre>
		<h6>${__("What the server replied")} (HTTP ${res.http_status})</h6>
		<pre style="white-space:pre-wrap;font-size:12px">${frappe.utils.escape_html(res.response || "")}</pre>`);
	d.show();
};
