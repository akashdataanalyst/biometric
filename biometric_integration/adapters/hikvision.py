"""Hikvision face / fingerprint terminals (DS-K1T341, DS-K1T671, DS-K1T804 ...).

Configure on the device: Configuration > Network > Advanced > HTTP Listening
(or "Event Alarm > Linkage > Notify Surveillance Center"). The device then POSTs
every access event to our URL, either as JSON or as multipart/form-data with a
JSON part named "event_log" / "AccessControllerEvent".
"""

from __future__ import annotations

import json
import re
from email.parser import BytesParser
from email.policy import default as default_policy

from biometric_integration.adapters.base import RawPunch, clean_user_id, parse_datetime, punch_type_from_value


def _extract_json(body: bytes, content_type: str | None) -> dict | None:
	content_type = content_type or ""
	if "multipart" in content_type.lower():
		msg = BytesParser(policy=default_policy).parsebytes(
			b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
		)
		for part in msg.iter_parts():
			payload = part.get_payload(decode=True) or b""
			name = (part.get_param("name", header="content-disposition") or "").lower()
			ctype = part.get_content_type()
			if ctype == "application/json" or name in ("event_log", "accesscontrollerevent") or payload.strip().startswith(b"{"):
				try:
					return json.loads(payload.decode("utf-8", "ignore"))
				except ValueError:
					continue
		return None
	text = body.decode("utf-8", "ignore").strip()
	if not text:
		return None
	if text.startswith("{"):
		try:
			return json.loads(text)
		except ValueError:
			pass
	# Some firmware sends event_log=<json> form encoded
	m = re.search(r"\{.*\}", text, re.S)
	if m:
		try:
			return json.loads(m.group(0))
		except ValueError:
			return None
	return None


def parse_event(body: bytes, content_type: str | None, server_tz=None) -> tuple[list[RawPunch], dict]:
	"""Returns (punches, meta). meta carries mac / ip / device name to identify the device."""
	data = _extract_json(body, content_type)
	if not data:
		return [], {}
	meta = {
		"mac_address": (data.get("macAddress") or "").upper(),
		"device_ip": data.get("ipAddress") or "",
		"event_type": data.get("eventType") or "",
		"device_id": str(data.get("deviceID") or ""),
	}
	ev = data.get("AccessControllerEvent") or {}
	meta["model"] = ev.get("deviceName") or ""
	user_id = clean_user_id(ev.get("employeeNoString") or ev.get("employeeNo") or "")
	if not user_id or user_id == "0":
		# heartbeat, door event, stranger / failed verification etc.
		return [], meta
	ts = data.get("dateTime") or ev.get("dateTime")
	punch = RawPunch(
		user_id=user_id,
		punch_time=parse_datetime(ts, server_tz),
		punch_type=punch_type_from_value(ev.get("attendanceStatus") or ev.get("label")),
		status_code=str(ev.get("attendanceStatus") or ""),
		verify_mode=str(ev.get("currentVerifyMode") or ""),
		raw=json.dumps(data)[:4000],
		device_hint=meta["mac_address"] or meta["device_id"],
	)
	return [punch], meta
