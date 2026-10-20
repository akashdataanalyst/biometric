"""The processing engine.

Every punch from every protocol ends up here:

    RawPunch -> Biometric Punch Log (raw, de-duplicated)
             -> employee lookup (Attendance Device ID / Emp Code / Employee ID)
             -> Employee Checkin (IN / OUT)
             -> realtime event for the Live Monitor
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, get_system_timezone, now_datetime

from biometric_integration.adapters.base import IN, OUT, RawPunch, apply_offset

LOG = "Biometric Punch Log"
DEVICE = "Biometric Device"

SOURCE_FOR_PROTOCOL = {
	"ADMS (eSSL / ZKTeco Push)": "ADMS",
	"Hikvision ISAPI": "Hikvision",
	"Generic Webhook": "Webhook",
	"LAN Agent (ZK Pull)": "LAN Agent",
	"File Import": "File Import",
}


# --------------------------------------------------------------------------- helpers


def get_settings():
	return frappe.get_cached_doc("Biometric Settings")


def server_tz():
	try:
		return ZoneInfo(get_system_timezone())
	except Exception:
		return None


def _norm_uid(uid: str, strip_zeros: bool) -> str:
	uid = (uid or "").strip()
	if strip_zeros and uid.isdigit():
		return uid.lstrip("0") or "0"
	return uid


MATCH_MODES = {
	"Device ID, Emp Code or Employee ID": ["attendance_device_id", "employee_number", "name"],
	"Attendance Device ID": ["attendance_device_id"],
	"Employee Number (Emp Code)": ["employee_number"],
	"Employee ID": ["name"],
	"Custom Employee Field": [],
}


def match_fields(settings=None) -> list[str]:
	"""Employee fields a machine user ID is matched against, highest priority first."""
	settings = settings or get_settings()
	mode = settings.get("match_employee_by") or "Device ID, Emp Code or Employee ID"
	fields = list(MATCH_MODES.get(mode, MATCH_MODES["Device ID, Emp Code or Employee ID"]))
	custom = (settings.get("match_custom_field") or "").strip()
	if custom and frappe.get_meta("Employee").has_field(custom):
		fields.insert(0 if mode == "Custom Employee Field" else len(fields), custom)
	return list(dict.fromkeys(fields)) or ["attendance_device_id"]


def mapping_target_field(settings=None) -> str:
	"""Field to write when HR maps an unknown machine ID to an employee."""
	for f in match_fields(settings):
		if f != "name":
			return f
	frappe.throw(_("'Match Employee By' is Employee ID only, so the machine user ID must equal the Employee ID."))


def machine_id_of(employee: str) -> str | None:
	"""The ID this employee should have on the machine, per the matching setting."""
	values = frappe.db.get_value("Employee", employee, match_fields(), as_dict=True) or {}
	for f in match_fields():
		if values.get(f):
			return str(values[f])
	return None


def _employee_map() -> dict:
	cache = getattr(frappe.local, "_bio_emp_map", None)
	if cache is not None:
		return cache
	settings = get_settings()
	strip = cint(settings.strip_leading_zeros)
	fields = match_fields(settings)
	rows = frappe.get_all(
		"Employee",
		fields=list(dict.fromkeys(["name", "employee_name", "status", "company", *fields])),
	)
	cache = {}
	for field in fields:  # higher priority field fills the map first
		layer = {}
		for r in rows:
			value = r.get(field)
			if not value:
				continue
			key = _norm_uid(str(value), strip)
			# Active employee wins if two share the same id
			if key not in layer or (r.status == "Active" and layer[key].status != "Active"):
				layer[key] = r
		for key, r in layer.items():
			cache.setdefault(key, r)
	frappe.local._bio_emp_map = cache
	return cache


def clear_employee_cache():
	frappe.local._bio_emp_map = None


def find_employee(user_id: str):
	return _employee_map().get(_norm_uid(user_id, cint(get_settings().strip_leading_zeros)))


def log_name(device: str, user_id: str, punch_time) -> str:
	key = f"{device}|{user_id}|{get_datetime(punch_time).strftime('%Y-%m-%d %H:%M:%S')}"
	return "BPL-" + hashlib.sha1(key.encode()).hexdigest()[:16].upper()


# --------------------------------------------------------------------------- devices


def resolve_device(identifier: str | None, protocol: str, meta: dict | None = None, register: bool = True):
	"""Find a device by serial no (or MAC for Hikvision). Auto-registers unknown
	devices when allowed in settings. Returns a Biometric Device doc or None."""
	meta = meta or {}
	identifier = (identifier or "").strip()
	name = None
	if identifier and frappe.db.exists(DEVICE, identifier):
		name = identifier
	if not name and meta.get("mac_address"):
		name = frappe.db.get_value(DEVICE, {"mac_address": meta["mac_address"]}, "name")
	if not name and identifier:
		name = frappe.db.get_value(DEVICE, {"mac_address": identifier.upper()}, "name")
	if name:
		return frappe.get_doc(DEVICE, name)

	settings = get_settings()
	if not register or not cint(settings.auto_register_devices) or not identifier:
		return None

	doc = frappe.get_doc(
		{
			"doctype": DEVICE,
			"serial_no": identifier,
			"device_name": meta.get("model") or identifier,
			"protocol": protocol,
			"model": meta.get("model") or "",
			"mac_address": meta.get("mac_address") or "",
			"device_ip": meta.get("device_ip") or "",
			"status": "Active" if cint(settings.auto_approve_devices) else "Pending Approval",
			"online_status": "Online",
			"last_heartbeat": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.publish_realtime(
		"biometric_device_registered",
		{"device": doc.name, "status": doc.status, "protocol": protocol},
		after_commit=True,
	)
	return doc


def touch_device(device_name: str, **fields):
	"""Mark device as seen (heartbeat) and store any info it reported."""
	allowed = {
		"device_ip",
		"firmware",
		"user_count",
		"fp_count",
		"face_count",
		"att_count",
		"model",
		"mac_address",
	}
	values = {k: v for k, v in fields.items() if k in allowed and v not in (None, "")}
	if values.get("model") and frappe.db.get_value(DEVICE, device_name, "model"):
		values.pop("model")  # keep what the user typed
	values.update({"last_heartbeat": now_datetime(), "online_status": "Online"})
	frappe.db.set_value(DEVICE, device_name, values, update_modified=False)


# --------------------------------------------------------------------------- ingest


def ingest(device, punches: list[RawPunch], source: str, is_test: int = 0, create_checkin: bool = True) -> dict:
	"""Store and process a batch of punches from one device. Returns a summary."""
	if isinstance(device, str):
		device = frappe.get_doc(DEVICE, device)
	# virtual (simulator) devices and simulator calls always produce test punches
	is_test = cint(is_test or device.is_virtual or frappe.flags.biometric_test)
	if frappe.flags.biometric_dry_run:
		create_checkin = False

	summary = {"received": len(punches), "new": 0, "already_received": 0, "processed": 0, "items": []}
	latest = None
	for p in punches:
		punch_time = apply_offset(p.punch_time, device.time_offset_minutes)
		name = log_name(device.name, p.user_id, punch_time)
		if frappe.db.exists(LOG, name):
			summary["already_received"] += 1
			summary["items"].append({"log": name, "status": "Already Received", "user_id": p.user_id})
			continue

		log = frappe.get_doc(
			{
				"doctype": LOG,
				"device": device.name,
				"user_id": p.user_id,
				"punch_time": punch_time,
				"device_punch_type": p.punch_type or "",
				"status_code": p.status_code,
				"verify_mode": p.verify_mode,
				"source": source,
				"is_test": cint(is_test),
				"raw_data": p.raw,
				"received_on": now_datetime(),
				"status": "Pending",
			}
		)
		try:
			log.insert(ignore_permissions=True, set_name=name)
		except frappe.DuplicateEntryError:
			# the machine re-sent this batch while the first request was still running
			frappe.clear_last_message()
			summary["already_received"] += 1
			summary["items"].append({"log": name, "status": "Already Received", "user_id": p.user_id})
			continue
		summary["new"] += 1
		process_log(log, device=device, create_checkin=create_checkin)
		if log.status == "Processed":
			summary["processed"] += 1
		summary["items"].append(
			{
				"log": log.name,
				"status": log.status,
				"user_id": log.user_id,
				"employee": log.employee,
				"employee_name": log.employee_name,
				"log_type": log.log_type,
				"employee_checkin": log.employee_checkin,
				"message": log.message,
			}
		)
		if log.status not in ("Error", "Ignored") and (latest is None or punch_time > latest):
			latest = punch_time

	if summary["new"]:
		frappe.db.sql(
			"update `tabBiometric Device` set total_punches = ifnull(total_punches, 0) + %s where name = %s",
			(summary["new"], device.name),
		)
	values = {"last_heartbeat": now_datetime(), "online_status": "Online"}
	if device.protocol == "File Import":
		values = {}  # a file upload says nothing about the machine being online
	if latest and (not device.last_punch_on or latest > get_datetime(device.last_punch_on)):
		values["last_punch_on"] = latest
	if values:
		frappe.db.set_value(DEVICE, device.name, values, update_modified=False)
	return summary


def _finish(log, status, message=None, **fields):
	"""Save the final status of a punch log and notify the Live Monitor."""
	log.status = status
	log.message = message
	for k, v in fields.items():
		setattr(log, k, v)
	log.db_update()
	return _publish(log)


def process_log(log, device=None, create_checkin: bool = True):
	"""Turn one punch log into an Employee Checkin. Safe to call again (reprocess)."""
	settings = get_settings()
	device = device or frappe.get_cached_doc(DEVICE, log.device)
	punch_time = get_datetime(log.punch_time)

	if device.status == "Pending Approval":
		return _finish(log, "Device Not Approved", _("Approve device {0} to process its punches").format(device.name))
	if device.status == "Disabled":
		return _finish(log, "Ignored", _("Device is disabled"))
	if settings.ignore_punches_before and punch_time.date() < get_datetime(settings.ignore_punches_before).date():
		return _finish(log, "Ignored", _("Older than 'Ignore punches before' date in settings"))
	future_limit = add_to_date(now_datetime(), minutes=cint(settings.future_tolerance_minutes or 15))
	if punch_time > future_limit:
		return _finish(log, "Error", _("Punch time is in the future. Check the date and time set on the device."))

	emp = find_employee(log.user_id)
	if not emp:
		return _finish(
			log,
			"Unmapped",
			_(
				"No employee matches machine ID '{0}'. Set it as the Employee's Attendance Device ID or Emp Code and the punch is processed automatically."
			).format(log.user_id),
			employee=None,
			employee_name=None,
		)
	log.employee, log.employee_name = emp.name, emp.employee_name
	if emp.status != "Active":
		return _finish(log, "Error", _("Employee {0} is {1}").format(emp.name, emp.status))

	window = cint(settings.duplicate_window_seconds)
	if window > 0:
		dup = frappe.db.get_value(
			"Employee Checkin",
			{
				"employee": emp.name,
				"time": ["between", [punch_time - timedelta(seconds=window), punch_time + timedelta(seconds=window)]],
			},
			"name",
		)
		if dup:
			return _finish(log, "Duplicate", _("Employee already punched within {0} seconds").format(window), employee_checkin=dup)

	log_type = decide_log_type(emp.name, punch_time, log.device_punch_type or None, device, settings)
	log.log_type = log_type or ""

	if not create_checkin:
		return _finish(log, "Dry Run", _("Test only - Employee Checkin not created"))

	frappe.db.savepoint("bio_checkin")
	try:
		checkin = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": emp.name,
				"time": punch_time,
				"log_type": log_type,
				"device_id": device.name,
			}
		)
		if device.get("latitude") or device.get("longitude"):
			# needed when HR Settings > Allow Geolocation Tracking is on
			checkin.latitude, checkin.longitude = device.latitude, device.longitude
		checkin.flags.ignore_permissions = True
		checkin.insert(ignore_permissions=True)
	except Exception as e:
		frappe.db.rollback(save_point="bio_checkin")
		msg = frappe.utils.strip_html(str(e)) or e.__class__.__name__
		status = "Duplicate" if "same timestamp" in msg.lower() else "Error"
		frappe.clear_last_message()
		frappe.local.message_log = []
		return _finish(log, status, msg[:500])

	return _finish(log, "Processed", None, employee_checkin=checkin.name, shift=checkin.get("shift"))


def decide_log_type(employee, punch_time, device_type, device, settings) -> str | None:
	direction = device.punch_direction or "As per Settings"
	if direction == "Always IN":
		return IN
	if direction == "Always OUT":
		return OUT
	mode = settings.log_type_mode or "Alternate IN / OUT"
	if mode.startswith("Leave Blank"):
		return None
	if mode == "Device Punch State Only":
		return device_type
	if mode.startswith("Device Punch State") and device_type:
		return device_type
	# Alternate: look at the previous checkin of this employee in the last N hours
	hours = cint(settings.alternate_window_hours or 14)
	last = frappe.db.get_value(
		"Employee Checkin",
		{
			"employee": employee,
			"time": ["between", [punch_time - timedelta(hours=hours), punch_time - timedelta(seconds=1)]],
		},
		"log_type",
		order_by="time desc",
	)
	return OUT if last == IN else IN


def _publish(log):
	try:
		device_name = frappe.db.get_value(DEVICE, log.device, "device_name")
	except Exception:
		device_name = log.device
	frappe.publish_realtime(
		"biometric_punch",
		{
			"name": log.name,
			"device": log.device,
			"device_name": device_name,
			"user_id": log.user_id,
			"employee": log.employee,
			"employee_name": log.employee_name,
			"punch_time": str(log.punch_time),
			"log_type": log.log_type,
			"status": log.status,
			"message": log.message,
			"source": log.source,
			"is_test": log.is_test,
			"employee_checkin": log.employee_checkin,
		},
		after_commit=True,
	)
	return log


# --------------------------------------------------------------------------- reprocessing


RETRY_STATUSES = ("Unmapped", "Error", "Device Not Approved", "Pending")


@frappe.whitelist()
def reprocess(names=None, filters=None):
	"""Re-run processing for selected punch logs (Unmapped / Error / Pending ...)."""
	frappe.only_for(("System Manager", "HR Manager", "HR User"))
	names = frappe.parse_json(names) if names else None
	if not names:
		f = frappe.parse_json(filters) if filters else {}
		f = f or {}
		f.setdefault("status", ["in", RETRY_STATUSES])
		names = frappe.get_all(LOG, filters=f, pluck="name", limit=5000)
	return reprocess_logs(names)


def reprocess_logs(names, statuses=RETRY_STATUSES) -> dict:
	clear_employee_cache()
	result = {"total": len(names)}
	# oldest first, so Alternate IN / OUT sees each employee's previous punch
	names = frappe.get_all(LOG, filters={"name": ["in", list(names)]}, order_by="punch_time asc", pluck="name") if names else []
	for name in names:
		log = frappe.get_doc(LOG, name)
		if log.status not in statuses:
			result["skipped"] = result.get("skipped", 0) + 1
			continue
		process_log(log, create_checkin=True)
		result[log.status] = result.get(log.status, 0) + 1
	return result


def on_employee_update(doc, method=None):
	"""When HR sets / changes a machine ID or Emp Code, pick up waiting punches."""
	clear_employee_cache()
	if not frappe.db.table_exists("Biometric Punch Log"):
		return
	fields = match_fields()
	changed = [f for f in fields if doc.get(f) and (f == "name" or doc.has_value_changed(f) or doc.is_new())]
	if not changed:
		return
	strip = cint(get_settings().strip_leading_zeros)
	names = []
	for f in changed:
		raw = str(doc.get(f)).strip()
		uid = _norm_uid(raw, strip)
		names += frappe.get_all(
			LOG,
			filters={"status": "Unmapped", "user_id": ["in", list({raw, uid})]},
			pluck="name",
			limit=2000,
		)
		if strip and uid.isdigit():
			# padded ids like 000101
			names += frappe.db.sql(
				"""select name from `tabBiometric Punch Log`
				where status='Unmapped' and user_id regexp %s limit 2000""",
				(f"^0+{uid}$",),
				pluck=True,
			)
	names = list(dict.fromkeys(names))
	if not names:
		return
	if len(names) <= 300:
		reprocess_logs(names)
	else:
		frappe.enqueue(
			"biometric_integration.engine.reprocess_logs",
			names=names,
			queue="long",
			enqueue_after_commit=True,
		)
