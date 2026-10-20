"""Built-in device simulator and self-test - test everything without a machine.

The simulator builds the *exact* payload a real machine would send (ADMS text,
Hikvision JSON, webhook JSON) and pushes it through the same code path. With
"Send over the network" it even calls the public URL over HTTP, so DNS, AWS
security group, nginx and the route are tested too.
"""

import json
import random
from datetime import timedelta

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime
from frappe.utils.scheduler import is_scheduler_disabled

from biometric_integration.api import adms as adms_api
from biometric_integration.api import push as push_api
from biometric_integration.api.setup_info import connection_info, reveal_token
from biometric_integration.engine import LOG, _employee_map, get_settings, log_name, machine_id_of, mapping_target_field

ROLES = ("System Manager", "HR Manager")

VIRTUAL = {
	"ADMS (eSSL / ZKTeco Push)": ("SIM-ESSL-K23", "Simulator - eSSL K23", "eSSL K23 (virtual)"),
	"Hikvision ISAPI": ("SIM-HIKVISION", "Simulator - Hikvision", "Hikvision DS-K1T341 (virtual)"),
	"Generic Webhook": ("SIM-WEBHOOK", "Simulator - Webhook", "Any machine via webhook (virtual)"),
	"LAN Agent (ZK Pull)": ("SIM-LAN-AGENT", "Simulator - LAN Agent", "ZKTeco on LAN (virtual)"),
}


@frappe.whitelist()
def create_virtual_device(protocol: str = "ADMS (eSSL / ZKTeco Push)") -> str:
	frappe.only_for(ROLES)
	if protocol not in VIRTUAL:
		frappe.throw(_("Choose one of: {0}").format(", ".join(VIRTUAL)))
	serial, name, model = VIRTUAL[protocol]
	if frappe.db.exists("Biometric Device", serial):
		frappe.db.set_value("Biometric Device", serial, "status", "Active")
		return serial
	frappe.get_doc(
		{
			"doctype": "Biometric Device",
			"serial_no": serial,
			"device_name": name,
			"protocol": protocol,
			"model": model,
			"status": "Active",
			"is_virtual": 1,
			"location": "Simulator",
		}
	).insert()
	return serial


def _user_id_for(employee: str | None, user_id: str | None) -> str:
	if user_id:
		return str(user_id).strip()
	if not employee:
		frappe.throw(_("Choose an Employee or type a Device User ID"))
	uid = machine_id_of(employee)
	if not uid:
		frappe.throw(
			_("Employee {0} has no machine ID / Emp Code. Set it on the Employee (or use 'Auto-assign IDs').").format(employee)
		)
	return uid


def _build_request(device, user_id, punch_time, punch_type):
	"""Return (method, path, args, headers, body) exactly as the machine would send it."""
	ts = punch_time.strftime("%Y-%m-%d %H:%M:%S")
	p = device.protocol
	if p.startswith("ADMS"):
		state = {"IN": "0", "OUT": "1"}.get(punch_type, "255")
		body = f"{user_id}\t{ts}\t{state}\t1\t0\t0\t0\t0\t0\t0\n"
		args = {"SN": device.serial_no, "table": "ATTLOG", "Stamp": str(random.randint(1000, 999999))}
		return "POST", "/iclock/cdata", args, {"Content-Type": "text/plain"}, body
	if p.startswith("Hikvision"):
		status = {"IN": "checkIn", "OUT": "checkOut"}.get(punch_type, "")
		event = {
			"ipAddress": "192.168.1.64",
			"portNo": 80,
			"protocol": "HTTP",
			"macAddress": device.mac_address or "SI:MU:LA:TO:R0:01",
			"channelID": 1,
			"dateTime": punch_time.strftime("%Y-%m-%dT%H:%M:%S"),
			"activePostCount": 1,
			"eventType": "AccessControllerEvent",
			"eventState": "active",
			"eventDescription": "Access Controller Event",
			"AccessControllerEvent": {
				"deviceName": "Access Controller",
				"majorEventType": 5,
				"subEventType": 75,
				"cardReaderNo": 1,
				"employeeNoString": user_id,
				"serialNo": random.randint(1, 99999),
				"userType": "normal",
				"currentVerifyMode": "cardOrFaceOrFp",
				"attendanceStatus": status,
				"mask": "no",
			},
		}
		return "POST", "/biometric/hikvision", {"sn": device.serial_no}, {"Content-Type": "application/json"}, json.dumps(event)
	body = {"device_id": device.serial_no, "user_id": user_id, "timestamp": ts, "type": punch_type or ""}
	if p.startswith("LAN Agent"):
		body = {"device_id": device.serial_no, "source": "LAN Agent", "punches": [{"user_id": user_id, "timestamp": ts, "status": {"IN": 0, "OUT": 1}.get(punch_type, 255)}]}
	headers = {"Content-Type": "application/json", "X-Biometric-Token": reveal_token() or ""}
	return "POST", "/biometric/push", {}, headers, json.dumps(body)


def _send(device, method, path, args, headers, body, via_http):
	if via_http:
		info = connection_info()
		base = info["machine_base"] if path.startswith("/iclock") or "hikvision" in path else info["https_base"]
		url = base + path
		try:
			r = requests.request(method, url, params=args, data=body.encode(), headers=headers, timeout=15)
			return url, r.status_code, r.text[:2000]
		except Exception as e:
			return url, 0, f"Could not reach {url}: {e}"
	# in-process: same handlers the URL calls
	url = path
	if path.startswith("/iclock"):
		return url, 200, adms_api.handle("cdata", method, args, body.encode())
	if "hikvision" in path:
		return url, 200, frappe.as_json(push_api.handle_hikvision(args, body.encode(), headers.get("Content-Type")))
	result = push_api.handle_generic(args, body.encode(), headers)
	return url, result.pop("_http_status", 200), frappe.as_json(result)


@frappe.whitelist()
def simulate_punch(
	device: str,
	employee: str | None = None,
	user_id: str | None = None,
	punch_type: str = "Auto",
	punch_time: str | None = None,
	via_http: int = 0,
	dry_run: int = 0,
) -> dict:
	"""Send one punch exactly as the selected machine would."""
	frappe.only_for(ROLES)
	dev = frappe.get_doc("Biometric Device", device)
	if dev.protocol == "File Import":
		frappe.throw(_("File Import devices don't send punches. Use Biometric Log Import, or pick another device."))
	uid = _user_id_for(employee, user_id)
	ptime = get_datetime(punch_time) if punch_time else now_datetime()
	ptype = punch_type if punch_type in ("IN", "OUT") else None
	method, path, args, headers, body = _build_request(dev, uid, ptime, ptype)

	frappe.flags.biometric_test = True
	frappe.flags.biometric_trusted = not cint(via_http)
	frappe.flags.biometric_dry_run = bool(cint(dry_run)) and not cint(via_http)
	try:
		url, status, response = _send(dev, method, path, args, headers, body, cint(via_http))
	finally:
		frappe.flags.biometric_test = False
		frappe.flags.biometric_trusted = False
		frappe.flags.biometric_dry_run = False

	if cint(via_http):
		frappe.db.commit()  # the HTTP request ran in another worker
	name = log_name(dev.name, uid, ptime)
	log = frappe.db.get_value(
		LOG,
		name,
		["name", "status", "employee", "employee_name", "log_type", "employee_checkin", "message", "punch_time"],
		as_dict=True,
	)
	shown_headers = {k: ("****" if k == "X-Biometric-Token" else v) for k, v in headers.items()}
	return {
		"request": {"method": method, "url": url, "params": args, "headers": shown_headers, "body": body},
		"http_status": status,
		"response": response,
		"log": log,
		"ok": bool(log and log.status in ("Processed", "Dry Run", "Duplicate")),
	}


@frappe.whitelist()
def simulate_day(device: str, date: str | None = None, in_time: str = "09:00", out_time: str = "18:00", limit: int = 20) -> dict:
	"""Every mapped active employee punches IN and OUT (with a few minutes of random jitter)."""
	frappe.only_for(ROLES)
	day = getdate(date) if date else getdate()
	emps = [
		frappe._dict(name=r.name, machine_id=key)
		for key, r in _employee_map().items()
		if r.status == "Active"
	]
	seen, unique = set(), []
	for e in emps:  # one ID per employee
		if e.name not in seen:
			seen.add(e.name)
			unique.append(e)
	emps = unique[: cint(limit) or 20]
	if not emps:
		frappe.throw(_("No active employee has a machine ID / Emp Code yet. Use 'Auto-assign IDs' first."))
	results = {"punches": 0, "ok": 0, "statuses": {}}
	for e in emps:
		for t, ptype in ((in_time, "IN"), (out_time, "OUT")):
			ptime = get_datetime(f"{day} {t}") + timedelta(minutes=random.randint(-10, 10))
			if ptime > now_datetime():
				continue
			r = simulate_punch(device, user_id=e.machine_id, punch_type=ptype, punch_time=str(ptime))
			results["punches"] += 1
			st = (r.get("log") or {}).get("status") or "No log"
			results["statuses"][st] = results["statuses"].get(st, 0) + 1
			results["ok"] += 1 if r["ok"] else 0
	return results


@frappe.whitelist()
def auto_assign_device_ids(start: int = 101, limit: int = 50) -> dict:
	"""Give Active employees without a machine ID a test ID (101, 102 ...)."""
	frappe.only_for(ROLES)
	field = mapping_target_field()
	used = set(frappe.get_all("Employee", filters={field: ["is", "set"]}, pluck=field))
	emps = frappe.get_all(
		"Employee",
		filters={"status": "Active", field: ["is", "not set"]},
		pluck="name",
		limit=cint(limit),
		order_by="name asc",
	)
	n, assigned = cint(start), []
	for emp in emps:
		while str(n) in used:
			n += 1
		frappe.db.set_value("Employee", emp, field, str(n))
		used.add(str(n))
		assigned.append({"employee": emp, "id": str(n)})
		n += 1
	return {"assigned": assigned}


@frappe.whitelist()
def clear_test_data(delete_virtual_devices: int = 0) -> dict:
	"""Delete simulator punches and the Employee Checkins they created."""
	frappe.only_for(ROLES)
	logs = frappe.get_all(LOG, filters={"is_test": 1}, fields=["name", "employee_checkin"])
	checkins_deleted, kept = 0, 0
	for row in logs:
		if row.employee_checkin and frappe.db.exists("Employee Checkin", row.employee_checkin):
			if frappe.db.get_value("Employee Checkin", row.employee_checkin, "attendance"):
				kept += 1
				continue  # already used in Attendance: keep checkin and its log together
			else:
				frappe.delete_doc("Employee Checkin", row.employee_checkin, ignore_permissions=True, force=True)
				checkins_deleted += 1
		frappe.delete_doc(LOG, row.name, ignore_permissions=True, force=True)
	devices = 0
	if cint(delete_virtual_devices):
		for d in frappe.get_all("Biometric Device", filters={"is_virtual": 1}, pluck="name"):
			frappe.db.delete("Biometric Device Command", {"device": d})
			frappe.delete_doc("Biometric Device", d, ignore_permissions=True, force=True)
			devices += 1
	return {"logs": len(logs) - kept, "checkins": checkins_deleted, "kept_with_attendance": kept, "devices": devices}


# ------------------------------------------------------------------ self test


def _check(label, ok, detail="", fix="", level=None):
	return {"label": label, "ok": bool(ok), "level": level or ("ok" if ok else "fail"), "detail": detail, "fix": fix}


@frappe.whitelist()
def run_self_test(network: int = 1) -> dict:
	frappe.only_for(ROLES + ("HR User",))
	s = get_settings()
	info = connection_info()
	checks = []

	checks.append(_check(_("Integration enabled"), cint(s.enabled), fix=_("Tick 'Enable Biometric Integration' in Biometric Settings")))
	checks.append(
		_check(
			_("Public server address set"),
			bool(s.server_url),
			info["host"] if s.server_url else "",
			_("Enter the address machines will call, e.g. erp.yourcompany.com"),
		)
	)
	checks.append(
		_check(
			_("Scheduler running"),
			not is_scheduler_disabled(),
			fix=_("Run 'bench --site <site> enable-scheduler' (needed for online/offline status and HRMS auto attendance)"),
		)
	)
	has_token = bool(reveal_token())
	checks.append(_check(_("API token created"), has_token, fix=_("Click 'Generate API Token'"), level=None if has_token else "warn"))

	real = frappe.get_all(
		"Biometric Device",
		filters={"is_virtual": 0, "protocol": ["!=", "File Import"]},
		fields=["name", "device_name", "status", "online_status", "last_heartbeat"],
	)
	pending = [d.name for d in real if d.status == "Pending Approval"]
	online = [d for d in real if d.online_status == "Online"]
	checks.append(
		_check(
			_("Real machines connected"),
			bool(online),
			_("{0} registered, {1} online").format(len(real), len(online)),
			_("Configure a machine using the instructions on its Biometric Device page (or use the Simulator to test)."),
			level="ok" if online else "warn",
		)
	)
	if pending:
		checks.append(
			_check(_("Machines waiting for approval"), False, ", ".join(pending), _("Open the device and click Approve"), level="warn")
		)

	total = frappe.db.count("Employee", {"status": "Active"})
	mapped = len({r.name for r in _employee_map().values() if r.status == "Active"})
	checks.append(
		_check(
			_("Employees mapped to machine IDs"),
			mapped > 0,
			_("{0} of {1} active employees can be matched (by {2})").format(mapped, total, s.match_employee_by or "Device ID, Emp Code or Employee ID"),
			_("Set the machine user ID / PIN as the Employee's Attendance Device ID or Emp Code (Employee Number)"),
			level="ok" if total and mapped == total else ("warn" if mapped else "fail"),
		)
	)
	auto = frappe.db.count("Shift Type", {"enable_auto_attendance": 1})
	checks.append(
		_check(
			_("HRMS Auto Attendance enabled on a Shift Type"),
			auto > 0,
			_("{0} shift type(s)").format(auto),
			_("Open Shift Type, tick 'Enable Auto Attendance' and set 'Process Attendance After'. Without it you get checkins but no Attendance."),
			level="ok" if auto else "warn",
		)
	)
	unmapped = frappe.db.count(LOG, {"status": "Unmapped", "punch_time": [">=", now_datetime() - timedelta(days=7)]})
	if unmapped:
		checks.append(
			_check(
				_("Unmapped punches (last 7 days)"),
				False,
				str(unmapped),
				_("Map the device user IDs to employees from the Live Monitor"),
				level="warn",
			)
		)

	# pipeline test: run a fake punch through the full engine without creating a checkin
	# (needs the simulator device, which only HR Manager / System Manager may create)
	try:
		if not set(ROLES).intersection(frappe.get_roles()):
			raise frappe.PermissionError
		dev = create_virtual_device("Generic Webhook")
		uid = next((k for k, r in _employee_map().items() if r.status == "Active"), "SELFTEST")
		r = simulate_punch(dev, user_id=uid, punch_type="IN", punch_time=str(now_datetime() - timedelta(seconds=random.randint(1, 3600))), dry_run=1)
		log = r.get("log") or {}
		ok = log.get("status") in ("Dry Run", "Unmapped", "Duplicate")
		checks.append(
			_check(_("Processing engine (dry run)"), ok, _("Result: {0}").format(log.get("status") or r.get("response")))
		)
		if log.get("name"):
			frappe.delete_doc(LOG, log["name"], ignore_permissions=True, force=True)
	except frappe.PermissionError:
		frappe.clear_last_message()
		checks.append(
			_check(_("Processing engine (dry run)"), True, _("Skipped: needs HR Manager or System Manager"), level="warn")
		)
	except Exception as e:
		checks.append(_check(_("Processing engine (dry run)"), False, str(e)))

	if cint(network):
		url = info["machine_base"] + "/iclock/cdata"
		try:
			resp = requests.get(url, params={"SN": "__PING__"}, timeout=8)
			ok = "OK PING" in resp.text
			checks.append(
				_check(
					_("Machines can reach {0}").format(info["machine_base"]),
					ok,
					_("HTTP {0}").format(resp.status_code) + ("" if ok else f": {resp.text[:120]}"),
					_("Open port {0} in the AWS Security Group, point DNS to the server and make sure nginx serves this site on that port without redirecting to HTTPS. See README > AWS / nginx.").format(info["port"]),
				)
			)
		except Exception as e:
			checks.append(
				_check(
					_("Machines can reach {0}").format(info["machine_base"]),
					False,
					str(e)[:200],
					_("Open port {0} in the AWS Security Group and check DNS / nginx.").format(info["port"]),
				)
			)

	fails = len([c for c in checks if c["level"] == "fail"])
	warns = len([c for c in checks if c["level"] == "warn"])
	return {"checks": checks, "fails": fails, "warns": warns, "ran_on": str(now_datetime())}


@frappe.whitelist()
def employee_match_status() -> dict:
	frappe.only_for(ROLES + ("HR User",))
	s = get_settings()
	total = frappe.db.count("Employee", {"status": "Active"})
	mapped = len({r.name for r in _employee_map().values() if r.status == "Active"})
	return {"total": total, "mapped": mapped, "mode": s.match_employee_by or "Device ID, Emp Code or Employee ID"}
