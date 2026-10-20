"""Data for the Live Monitor page."""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime

from biometric_integration.engine import LOG, clear_employee_cache, mapping_target_field, reprocess_logs


@frappe.whitelist()
def get_dashboard(date: str | None = None, include_test: int = 1) -> dict:
	frappe.only_for(("System Manager", "HR Manager", "HR User"))
	day = getdate(date) if date else getdate()
	start, end = f"{day} 00:00:00", f"{day} 23:59:59"
	base = {"punch_time": ["between", [start, end]]}
	if not cint(include_test):
		base["is_test"] = 0

	def count(**extra):
		return frappe.db.count(LOG, {**base, **extra})

	devices = frappe.get_all(
		"Biometric Device",
		fields=[
			"name",
			"device_name",
			"protocol",
			"model",
			"status",
			"online_status",
			"last_heartbeat",
			"last_punch_on",
			"branch",
			"location",
			"is_virtual",
		],
		order_by="is_virtual asc, device_name asc",
	)
	test_cond = "" if cint(include_test) else " and is_test = 0"
	today_by_device = dict(
		frappe.db.sql(
			f"select device, count(*) from `tabBiometric Punch Log` where punch_time between %s and %s{test_cond} group by device",
			(start, end),
		)
	)
	for d in devices:
		d.today = today_by_device.get(d.name, 0)

	recent = frappe.get_all(
		LOG,
		filters=base,
		fields=[
			"name",
			"punch_time",
			"user_id",
			"employee",
			"employee_name",
			"log_type",
			"status",
			"device",
			"source",
			"is_test",
			"message",
			"employee_checkin",
		],
		order_by="punch_time desc",
		limit=100,
	)
	names = {d.name: d.device_name for d in devices}
	for r in recent:
		r.device_name = names.get(r.device, r.device)

	unmapped = frappe.db.sql(
		"""select user_id, device, count(*) as punches, max(punch_time) as last_punch
		from `tabBiometric Punch Log` where status='Unmapped' and punch_time >= %s
		group by user_id, device order by last_punch desc limit 50""",
		(now_datetime() - timedelta(days=30),),
		as_dict=True,
	)
	for u in unmapped:
		u.device_name = names.get(u.device, u.device)

	employees_in = frappe.db.sql(
		f"""select count(distinct employee) from `tabBiometric Punch Log`
		where punch_time between %s and %s and status='Processed'{test_cond}""",
		(start, end),
	)[0][0]

	return {
		"date": str(day),
		"stats": {
			"devices_total": len([d for d in devices if not d.is_virtual]),
			"devices_online": len([d for d in devices if d.online_status == "Online" and not d.is_virtual]),
			"punches": count(),
			"employees": employees_in,
			"processed": count(status="Processed"),
			"unmapped": count(status="Unmapped"),
			"errors": count(status="Error"),
			"pending_devices": len([d for d in devices if d.status == "Pending Approval"]),
		},
		"devices": devices,
		"recent": recent,
		"unmapped": unmapped,
		"server_time": str(now_datetime()),
	}


@frappe.whitelist()
def map_user(user_id: str, employee: str) -> dict:
	"""Save the machine user ID on the employee (Attendance Device ID or Emp Code,
	per 'Match Employee By') and process the waiting punches."""
	frappe.only_for(("System Manager", "HR Manager"))
	user_id = (user_id or "").strip()
	field = mapping_target_field()
	other = frappe.db.get_value("Employee", {field: user_id, "name": ["!=", employee]}, "name")
	if other:
		frappe.throw(_("Machine ID {0} already belongs to {1}").format(user_id, other))
	emp = frappe.get_doc("Employee", employee)
	emp.set(field, user_id)
	emp.save()  # on_update hook reprocesses waiting punches
	clear_employee_cache()
	left = frappe.get_all(LOG, filters={"status": "Unmapped", "user_id": user_id}, pluck="name")
	result = reprocess_logs(left) if left else {}
	return {"employee": employee, "user_id": user_id, "field": field, "result": result}


@frappe.whitelist()
def approve_device(device: str) -> str:
	frappe.only_for(("System Manager", "HR Manager"))
	doc = frappe.get_doc("Biometric Device", device)
	doc.status = "Active"
	doc.save()
	return doc.status
