"""Server side of the ADMS / iclock push protocol (eSSL, ZKTeco, Identix ...)."""

import frappe
from frappe.utils import cint, now_datetime

from biometric_integration.adapters import adms
from biometric_integration.engine import get_settings, ingest, resolve_device, server_tz, touch_device

PROTOCOL = "ADMS (eSSL / ZKTeco Push)"
CMD = "Biometric Device Command"


def handle(endpoint: str, method: str, args: dict, body: bytes) -> str:
	sn = (args.get("SN") or args.get("sn") or "").strip()
	if not sn:
		return "ERROR: SN missing"
	if sn == "__PING__":
		# used by the self-test to check the public URL reaches this app
		return "OK PING biometric_integration"
	settings = get_settings()
	if not cint(settings.enabled):
		return "ERROR: integration disabled"

	device = resolve_device(sn, PROTOCOL, {"device_ip": frappe.local.request_ip})
	if not device:
		# Not registered and auto-register is off: refuse, the device keeps its data.
		return "ERROR: device not registered"

	text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else (body or "")

	if endpoint in ("cdata", "push", "registry"):
		if method == "GET" or not args.get("table"):
			touch_device(device.name, device_ip=frappe.local.request_ip)
			return adms.handshake_response(sn, settings.device_timezone or site_utc_offset_hours(), realtime=True)

		table = (args.get("table") or "").upper()
		if table == "ATTLOG":
			punches, bad = adms.parse_attlog(text, server_tz())
			if bad:
				frappe.log_error(
					title=f"Biometric: unreadable lines from {sn}",
					message="\n".join(bad[:100]),
				)
			ingest(device, punches, "ADMS")
			return f"OK: {len(punches) + len(bad)}"
		if table == "OPTIONS":
			touch_device(device.name, **adms.parse_options_body(text))
			return "OK"
		# OPERLOG, ATTPHOTO, BIODATA, USERINFO ... acknowledge so the device moves on
		touch_device(device.name)
		lines = len([line for line in text.splitlines() if line.strip()])
		return f"OK: {lines}"

	if endpoint == "getrequest":
		info = adms.parse_info_param(args.get("INFO"))
		info.setdefault("device_ip", frappe.local.request_ip)
		touch_device(device.name, **info)
		return pending_commands(device.name) or "OK"

	if endpoint == "devicecmd":
		touch_device(device.name)
		for res in adms.parse_command_results(text):
			_complete_command(device.name, res)
		return "OK"

	touch_device(device.name)
	return "OK"


def site_utc_offset_hours() -> float | None:
	"""UTC offset of the site's time zone, e.g. 5.5 for Asia/Kolkata. The machine sets
	its clock from the HTTP Date header (UTC) plus this, so it must match the site."""
	tz = server_tz()
	if tz is None:
		return None
	offset = now_datetime().replace(tzinfo=tz).utcoffset()
	return offset.total_seconds() / 3600 if offset is not None else None


def pending_commands(device_name: str, limit: int = 10) -> str:
	rows = frappe.get_all(
		CMD,
		filters={"device": device_name, "status": "Pending"},
		fields=["name", "cmd_id", "command_text"],
		order_by="creation asc",
		limit=limit,
	)
	if not rows:
		return ""
	for r in rows:
		frappe.db.set_value(CMD, r.name, {"status": "Sent", "sent_on": now_datetime()}, update_modified=False)
	return "\n".join(r.command_text for r in rows) + "\n"


def _complete_command(device_name: str, res: dict):
	cmd_id = cint(res.get("ID"))
	name = frappe.db.get_value(CMD, {"device": device_name, "cmd_id": cmd_id}, "name")
	if not name:
		return
	code = res.get("Return", "")
	frappe.db.set_value(
		CMD,
		name,
		{
			"status": "Success" if str(code).strip() in ("0", "") or cint(code) > 0 else "Failed",
			"return_code": code,
			"response": frappe.as_json(res)[:1000],
			"completed_on": now_datetime(),
		},
		update_modified=False,
	)
