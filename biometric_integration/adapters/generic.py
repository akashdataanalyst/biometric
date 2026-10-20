"""Generic JSON webhook - the "universal" adapter.

Anything that can make an HTTP POST can push punches: Matrix COSEC / Suprema
BioStar / Realtime / Mantra middleware, a vendor's cloud webhook, a custom
script, Zapier, the LAN agent in tools/, etc.

Accepted bodies (all of these work):
    {"device_id": "GATE-1", "user_id": "101", "timestamp": "2026-10-18 09:01:00", "type": "IN"}
    [{"emp_code": "101", "time": "2026-10-18T09:01:00+05:30"}, ...]
    {"device_id": "GATE-1", "punches": [{"pin": 101, "punch_time": 1760758260}]}

Field names are detected automatically from a list of common aliases. Custom names
can be configured in Biometric Settings > Webhook field mapping.
"""

from __future__ import annotations

import json

from biometric_integration.adapters.base import RawPunch, clean_user_id, parse_datetime, punch_type_from_value

USER_ALIASES = [
	"user_id",
	"userid",
	"userId",
	"pin",
	"PIN",
	"emp_code",
	"empcode",
	"employee_code",
	"employee_no",
	"employeeNo",
	"employeeNoString",
	"enroll_id",
	"enrollid",
	"badge",
	"badge_no",
	"card_no",
	"user",
	"uid",
	"id_no",
	"attendance_device_id",
]
TIME_ALIASES = [
	"timestamp",
	"punch_time",
	"punchTime",
	"time",
	"datetime",
	"dateTime",
	"date_time",
	"log_time",
	"logTime",
	"event_time",
	"eventTime",
	"att_time",
	"checktime",
	"check_time",
]
TYPE_ALIASES = [
	"type",
	"log_type",
	"punch_type",
	"punchType",
	"direction",
	"status",
	"state",
	"punch_state",
	"attendanceStatus",
	"in_out",
	"inout",
	"io_mode",
	"event",
]
DEVICE_ALIASES = [
	"device_id",
	"deviceId",
	"device",
	"serial",
	"serial_no",
	"sn",
	"SN",
	"device_sn",
	"terminal",
	"terminal_id",
	"machine",
	"machine_no",
]
LIST_KEYS = ["punches", "data", "logs", "records", "events", "attendance", "items", "rows"]


def _pick(record: dict, preferred: str | None, aliases: list[str]):
	if preferred and preferred in record:
		return record.get(preferred)
	for key in aliases:
		if key in record and record[key] not in (None, ""):
			return record[key]
	lower = {k.lower(): v for k, v in record.items() if isinstance(k, str)}
	for key in aliases:
		if key.lower() in lower and lower[key.lower()] not in (None, ""):
			return lower[key.lower()]
	return None


def parse_payload(payload, mapping: dict | None = None, server_tz=None, default_device: str | None = None):
	"""Returns (list of (device_sn, RawPunch), list of error strings)."""
	mapping = mapping or {}
	if isinstance(payload, (bytes, str)):
		payload = json.loads(payload)

	top_device = default_device
	records = None
	if isinstance(payload, list):
		records = payload
	elif isinstance(payload, dict):
		top_device = _pick(payload, mapping.get("device"), DEVICE_ALIASES) or top_device
		for key in LIST_KEYS:
			if isinstance(payload.get(key), list):
				records = payload[key]
				break
		if records is None:
			records = [payload]
	else:
		raise ValueError("Body must be a JSON object or list")

	out, errors = [], []
	for idx, rec in enumerate(records):
		if not isinstance(rec, dict):
			errors.append(f"Row {idx + 1}: not an object")
			continue
		user = _pick(rec, mapping.get("user"), USER_ALIASES)
		ts = _pick(rec, mapping.get("time"), TIME_ALIASES)
		if user in (None, "") or ts in (None, ""):
			errors.append(f"Row {idx + 1}: user id or time missing")
			continue
		try:
			punch_time = parse_datetime(ts, server_tz)
		except ValueError as e:
			errors.append(f"Row {idx + 1}: {e}")
			continue
		type_value = _pick(rec, mapping.get("type"), TYPE_ALIASES)
		device = _pick(rec, mapping.get("device"), DEVICE_ALIASES) or top_device
		out.append(
			(
				str(device) if device not in (None, "") else None,
				RawPunch(
					user_id=clean_user_id(user),
					punch_time=punch_time,
					punch_type=punch_type_from_value(type_value),
					status_code="" if type_value is None else str(type_value),
					verify_mode=str(_pick(rec, None, ["verify", "verify_mode", "verifyMode", "mode"]) or ""),
					raw=json.dumps(rec, default=str)[:2000],
				),
			)
		)
	return out, errors
