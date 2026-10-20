"""Scheduled jobs."""

from datetime import timedelta

import frappe
from frappe.utils import add_days, cint, get_datetime, now_datetime


def every_five_minutes():
	if not frappe.db.table_exists("Biometric Device"):
		return
	settings = frappe.get_cached_doc("Biometric Settings")
	mark_offline_devices(settings)
	if cint(settings.update_shift_last_sync):
		update_shift_last_sync()


def mark_offline_devices(settings=None):
	settings = settings or frappe.get_cached_doc("Biometric Settings")
	minutes = cint(settings.offline_after_minutes) or 10
	cutoff = now_datetime() - timedelta(minutes=minutes)
	gone = frappe.get_all(
		"Biometric Device",
		filters={"online_status": "Online", "last_heartbeat": ["<", cutoff], "protocol": ["!=", "File Import"]},
		fields=["name", "device_name", "is_virtual"],
	)
	for d in gone:
		frappe.db.set_value("Biometric Device", d.name, "online_status", "Offline", update_modified=False)
		if not d.is_virtual:
			frappe.publish_realtime("biometric_device_offline", {"device": d.name, "device_name": d.device_name})


def sync_upto():
	"""Checkins are complete up to the moment the slowest *live* machine last talked to us.

	Machines silent for more than a day are ignored so that one dead machine
	does not hold back attendance for everyone (it is shown as Offline instead).
	Returns None when no real machine is live: then nothing is known to be complete."""
	now = now_datetime()
	rows = frappe.get_all(
		"Biometric Device",
		filters={
			"status": "Active",
			"is_virtual": 0,
			"protocol": ["!=", "File Import"],
			"last_heartbeat": [">", now - timedelta(days=1)],
		},
		pluck="last_heartbeat",
	)
	if not rows:
		return None
	return min([get_datetime(r) for r in rows] + [now])


def update_shift_last_sync():
	upto = sync_upto()
	if not upto:
		# no live machine (only simulators / file imports): moving the sync time would
		# let Auto Attendance mark everyone Absent for punches that were never received
		return
	for st in frappe.get_all("Shift Type", filters={"enable_auto_attendance": 1}, fields=["name", "last_sync_of_checkin"]):
		if not st.last_sync_of_checkin or get_datetime(st.last_sync_of_checkin) < upto:
			frappe.db.set_value("Shift Type", st.name, "last_sync_of_checkin", upto, update_modified=False)


def daily():
	if not frappe.db.table_exists("Biometric Punch Log"):
		return
	days = cint(frappe.db.get_single_value("Biometric Settings", "keep_logs_days"))
	if days > 0:
		frappe.db.delete(
			"Biometric Punch Log",
			{
				"punch_time": ["<", add_days(now_datetime(), -days)],
				"status": ["in", ["Processed", "Duplicate", "Ignored", "Dry Run"]],
			},
		)
