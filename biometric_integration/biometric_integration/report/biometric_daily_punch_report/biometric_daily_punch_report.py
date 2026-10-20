"""First IN, last OUT, worked hours and missing punches per employee per day,
built from Employee Checkin (so it includes every machine and manual checkins)."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, time_diff_in_hours


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or add_days(getdate(), -6))
	to_date = getdate(filters.to_date or getdate())
	conditions = ["c.time between %(start)s and %(end)s"]
	values = {"start": f"{from_date} 00:00:00", "end": f"{to_date} 23:59:59"}
	for field in ("employee", "company", "branch", "department"):
		if filters.get(field):
			conditions.append(f"e.{field} = %({field})s")
			values[field] = filters.get(field)
	if filters.get("device"):
		conditions.append("c.device_id = %(device)s")
		values["device"] = filters.device

	rows = frappe.db.sql(
		f"""select c.employee, e.employee_name, e.department, e.branch, date(c.time) as day,
			c.time, c.log_type, c.device_id
		from `tabEmployee Checkin` c join `tabEmployee` e on e.name = c.employee
		where {' and '.join(conditions)}
		order by c.employee, c.time""",
		values,
		as_dict=True,
	)

	grouped = {}
	for r in rows:
		grouped.setdefault((r.employee, r.day), []).append(r)

	data = []
	for (emp, day), punches in grouped.items():
		first, last = punches[0], punches[-1]
		hours = 0.0
		# pair punches in order: (1st,2nd), (3rd,4th) ...
		for i in range(0, len(punches) - 1, 2):
			hours += time_diff_in_hours(punches[i + 1].time, punches[i].time)
		missing = len(punches) % 2 == 1
		if filters.get("only_missing") and not missing:
			continue
		data.append(
			{
				"date": day,
				"employee": emp,
				"employee_name": first.employee_name,
				"department": first.department,
				"branch": first.branch,
				"first_in": first.time.strftime("%H:%M:%S"),
				"last_out": last.time.strftime("%H:%M:%S") if len(punches) > 1 else None,
				"punches": len(punches),
				"hours": flt(hours, 2),
				"missing_punch": _("Yes") if missing else "",
				"devices": ", ".join(sorted({p.device_id or "-" for p in punches})),
			}
		)
	data.sort(key=lambda d: (d["date"], d["employee"]), reverse=False)
	return columns(), data


def columns():
	return [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 120},
		{"label": _("Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 170},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Department", "width": 140},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 110},
		{"label": _("First In"), "fieldname": "first_in", "fieldtype": "Data", "width": 90},
		{"label": _("Last Out"), "fieldname": "last_out", "fieldtype": "Data", "width": 90},
		{"label": _("Punches"), "fieldname": "punches", "fieldtype": "Int", "width": 80},
		{"label": _("Hours"), "fieldname": "hours", "fieldtype": "Float", "precision": 2, "width": 80},
		{"label": _("Missing Punch"), "fieldname": "missing_punch", "fieldtype": "Data", "width": 110},
		{"label": _("Machines"), "fieldname": "devices", "fieldtype": "Data", "width": 160},
	]
