frappe.pages["biometric-live-monitor"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Biometric Live Monitor"), single_column: true });
	wrapper.monitor = new BiometricLiveMonitor(page);
};

frappe.pages["biometric-live-monitor"].on_page_show = function (wrapper) {
	wrapper.monitor && wrapper.monitor.refresh();
};

class BiometricLiveMonitor {
	constructor(page) {
		this.page = page;
		this.date_field = page.add_field({
			fieldname: "date",
			fieldtype: "Date",
			label: __("Date"),
			default: frappe.datetime.get_today(),
			change: () => this.refresh(),
		});
		this.test_field = page.add_field({
			fieldname: "include_test",
			fieldtype: "Check",
			label: __("Include test punches"),
			default: 1,
			change: () => this.refresh(),
		});
		page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
		page.add_inner_button(__("Simulate Punch"), () => biometric.simulate_dialog(null, () => this.refresh()));
		page.add_inner_button(__("Self-Test"), () => biometric.self_test(1));
		page.add_menu_item(__("Simulator"), () => frappe.set_route("biometric-simulator"));
		page.add_menu_item(__("Settings"), () => frappe.set_route("Form", "Biometric Settings"));
		page.add_menu_item(__("All Punch Logs"), () => frappe.set_route("List", "Biometric Punch Log"));

		this.$body = $(`<div class="bio-monitor">
			<style>
				.bio-monitor { padding: 4px 0 24px; }
				.bio-stats { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
				.bio-stat { background: var(--card-bg); border:1px solid var(--border-color); border-radius:10px; padding:12px 14px; }
				.bio-stat .v { font-size:24px; font-weight:600; line-height:1.2; }
				.bio-stat .l { color: var(--text-muted); font-size:12px; }
				.bio-grid { display:grid; grid-template-columns: minmax(0,2fr) minmax(0,1fr); gap:16px; }
				@media (max-width: 991px) { .bio-grid { grid-template-columns: 1fr; } }
				.bio-card { background: var(--card-bg); border:1px solid var(--border-color); border-radius:10px; padding:12px 14px; margin-bottom:16px; }
				.bio-card h5 { margin: 0 0 10px; font-size: 14px; display:flex; justify-content:space-between; align-items:center; }
				.bio-table { width:100%; font-size:13px; }
				.bio-table th { color: var(--text-muted); font-weight:500; padding:6px 6px; border-bottom:1px solid var(--border-color); }
				.bio-table td { padding:7px 6px; border-bottom:1px solid var(--border-color); vertical-align: middle; }
				.bio-new { animation: bioflash 2.5s ease-out; }
				@keyframes bioflash { from { background: var(--green-highlight-color, #e6f7ec); } to { background: transparent; } }
				.bio-dot { width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:6px; }
				.bio-live { font-size:12px; color: var(--green-600); }
				.bio-empty { color: var(--text-muted); padding: 18px 0; text-align:center; }
				.bio-dev { display:flex; justify-content:space-between; gap:8px; padding:8px 0; border-bottom:1px solid var(--border-color); }
				.bio-dev:last-child, .bio-table tr:last-child td { border-bottom: none; }
			</style>
			<div class="bio-stats"></div>
			<div class="bio-grid">
				<div><div class="bio-card"><h5><span>${__("Live punches")}</span><span class="bio-live">● ${__("live")}</span></h5>
					<div class="bio-feed"></div></div></div>
				<div>
					<div class="bio-card"><h5><span>${__("Machines")}</span>
						<a class="small" href="/desk/biometric-device">${__("Manage")}</a></h5><div class="bio-devices"></div></div>
					<div class="bio-card"><h5><span>${__("Unmapped device IDs (30 days)")}</span></h5><div class="bio-unmapped"></div></div>
				</div>
			</div></div>`).appendTo(page.body);

		this.bind_realtime();
		this.refresh();
		this.timer = setInterval(() => {
			if (frappe.get_route_str() === "biometric-live-monitor") this.refresh(true);
		}, 30000);
	}

	bind_realtime() {
		frappe.realtime.on("biometric_punch", (p) => {
			if (this.date_field.get_value() !== frappe.datetime.get_today()) return;
			if (p.is_test && !this.test_field.get_value()) return;
			this.prepend_row(p);
			clearTimeout(this._t);
			this._t = setTimeout(() => this.refresh(true), 1500);
		});
		frappe.realtime.on("biometric_device_registered", (d) => {
			frappe.show_alert({ message: __("New machine connected: {0} ({1})", [d.device, __(d.status)]), indicator: "blue" }, 8);
			this.refresh(true);
		});
		frappe.realtime.on("biometric_device_offline", (d) => {
			frappe.show_alert({ message: __("Machine offline: {0}", [d.device_name || d.device]), indicator: "red" }, 8);
			this.refresh(true);
		});
	}

	refresh(silent) {
		frappe.call({
			method: "biometric_integration.api.monitor.get_dashboard",
			args: { date: this.date_field.get_value(), include_test: this.test_field.get_value() },
			freeze: false,
			callback: (r) => r.message && this.render(r.message),
		});
	}

	render(data) {
		const s = data.stats;
		const stat = (v, l, color) =>
			`<div class="bio-stat"><div class="v" style="${color ? `color:var(--${color}-600)` : ""}">${v}</div><div class="l">${l}</div></div>`;
		this.$body.find(".bio-stats").html(
			stat(`${s.devices_online}/${s.devices_total}`, __("Machines online"), s.devices_total && s.devices_online < s.devices_total ? "red" : "green") +
				stat(s.punches, __("Punches")) +
				stat(s.employees, __("Employees punched")) +
				stat(s.processed, __("Checkins created"), "green") +
				stat(s.unmapped, __("Unmapped"), s.unmapped ? "orange" : null) +
				stat(s.errors, __("Errors"), s.errors ? "red" : null) +
				(s.pending_devices ? stat(s.pending_devices, __("Machines to approve"), "orange") : "")
		);

		const rows = data.recent.map((p) => this.row_html(p)).join("");
		this.$body.find(".bio-feed").html(
			data.recent.length
				? `<table class="bio-table"><thead><tr><th>${__("Time")}</th><th>${__("Employee")}</th><th>${__("Device ID")}</th>
					<th>${__("IN/OUT")}</th><th>${__("Machine")}</th><th>${__("Status")}</th></tr></thead><tbody>${rows}</tbody></table>`
				: `<div class="bio-empty">${__("No punches yet for this date. Punch on a machine or use Simulate Punch.")}</div>`
		);

		const dot = { Online: "var(--green-500)", Offline: "var(--red-500)", "Never Connected": "var(--gray-400)" };
		this.$body.find(".bio-devices").html(
			data.devices.length
				? data.devices
						.map(
							(d) => `<div class="bio-dev"><div>
							<span class="bio-dot" style="background:${dot[d.online_status] || "var(--gray-400)"}"></span>
							<a href="/desk/biometric-device/${encodeURIComponent(d.name)}"><b>${frappe.utils.escape_html(d.device_name || d.name)}</b></a>
							${d.is_virtual ? `<span class="text-muted small"> (${__("test")})</span>` : ""}
							<div class="text-muted small" style="margin-left:15px">${frappe.utils.escape_html(d.model || d.protocol || "")}
							${d.status !== "Active" ? ` · <b style="color:var(--orange-600)">${__(d.status)}</b>` : ""}</div></div>
							<div class="text-right small"><div><b>${d.today}</b> ${__("today")}</div>
							<div class="text-muted">${d.last_heartbeat ? frappe.datetime.comment_when(d.last_heartbeat) : __("never")}</div></div></div>`
						)
						.join("")
				: `<div class="bio-empty">${__("No machines yet. A machine appears here by itself when it connects, or create a test one in the Simulator.")}</div>`
		);

		const $u = this.$body.find(".bio-unmapped");
		$u.html(
			data.unmapped.length
				? `<table class="bio-table"><tbody>${data.unmapped
						.map(
							(u) => `<tr><td><b>${frappe.utils.escape_html(u.user_id)}</b><div class="text-muted small">${frappe.utils.escape_html(u.device_name || "")}</div></td>
							<td class="small">${u.punches} ${__("punches")}</td>
							<td class="text-right"><button class="btn btn-xs btn-default bio-map" data-uid="${frappe.utils.escape_html(u.user_id)}">${__("Map")}</button></td></tr>`
						)
						.join("")}</tbody></table>`
				: `<div class="bio-empty">${__("All device IDs are mapped to employees.")}</div>`
		);
		$u.find(".bio-map").on("click", (e) => this.map_dialog($(e.currentTarget).attr("data-uid")));
	}

	row_html(p, is_new) {
		const time = frappe.datetime.str_to_user(p.punch_time).split(" ").slice(-1)[0];
		const emp = p.employee
			? `<a href="/desk/employee/${p.employee}">${frappe.utils.escape_html(p.employee_name || p.employee)}</a>`
			: `<span class="text-muted">—</span>`;
		const type = p.log_type ? `<b style="color:var(--${p.log_type === "IN" ? "green" : "blue"}-600)">${p.log_type}</b>` : "";
		return `<tr class="${is_new ? "bio-new" : ""}" title="${frappe.utils.escape_html(p.message || "")}">
			<td>${time}</td><td>${emp}</td><td>${frappe.utils.escape_html(p.user_id || "")}</td><td>${type}</td>
			<td class="small">${frappe.utils.escape_html(p.device_name || p.device || "")}${p.is_test ? ` <span class="text-muted">(${__("test")})</span>` : ""}</td>
			<td><a href="/desk/biometric-punch-log/${p.name}">${biometric.indicator(p.status)}</a></td></tr>`;
	}

	prepend_row(p) {
		let $tbody = this.$body.find(".bio-feed tbody");
		if (!$tbody.length) {
			this.refresh(true);
			return;
		}
		$tbody.find(`a[href="/desk/biometric-punch-log/${p.name}"]`).closest("tr").remove();
		$tbody.prepend(this.row_html(p, true));
	}

	map_dialog(user_id) {
		frappe.prompt(
			{ fieldname: "employee", fieldtype: "Link", options: "Employee", label: __("Employee"), reqd: 1 },
			(v) =>
				frappe.call({
					method: "biometric_integration.api.monitor.map_user",
					args: { user_id, employee: v.employee },
					freeze: true,
					callback: (r) => {
						frappe.show_alert({ message: __("Mapped {0} to {1}. Waiting punches processed.", [user_id, v.employee]), indicator: "green" });
						this.refresh();
					},
				}),
			__("Who is device user {0}?", [user_id]),
			__("Map & Process")
		);
	}
}
