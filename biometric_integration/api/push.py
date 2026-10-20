"""HTTP push endpoints: Hikvision + generic JSON webhook (+ LAN agent).

URLs (both styles work):
    /biometric/push        or  /api/method/biometric_integration.api.push.punch
    /biometric/hikvision   or  /api/method/biometric_integration.api.push.hikvision
"""

import hmac
import json

import frappe
from frappe.utils import cint
from frappe.utils.password import get_decrypted_password

from biometric_integration.adapters import generic as generic_adapter
from biometric_integration.adapters import hikvision as hikvision_adapter
from biometric_integration.engine import get_settings, ingest, resolve_device, server_tz, touch_device

ALLOWED_SOURCES = {"Webhook", "LAN Agent"}


def _token_ok(given: str | None, device_name: str | None = None) -> bool:
	if frappe.flags.biometric_trusted:
		# in-process call from the built-in simulator (already permission checked)
		return True
	if not given:
		return False
	expected = get_decrypted_password("Biometric Settings", "Biometric Settings", "api_token", raise_exception=False)
	if expected and hmac.compare_digest(str(given), str(expected)):
		return True
	if device_name and frappe.db.exists("Biometric Device", device_name):
		dev_token = get_decrypted_password("Biometric Device", device_name, "device_token", raise_exception=False)
		if dev_token and hmac.compare_digest(str(given), str(dev_token)):
			return True
	return False


def handle_generic(args: dict, body: bytes, headers) -> dict:
	settings = get_settings()
	if not cint(settings.enabled):
		return {"ok": False, "error": "Biometric integration is disabled in Biometric Settings", "_http_status": 503}
	try:
		payload = json.loads(body or b"{}")
	except ValueError:
		return {"ok": False, "error": "Body must be valid JSON", "_http_status": 400}

	token = headers.get("X-Biometric-Token") or args.get("token")
	if isinstance(payload, dict):
		token = token or payload.get("token")
	default_device = args.get("device_id") or args.get("sn") or args.get("device")

	# heartbeat from the LAN agent / any device: {"device_id": "X", "heartbeat": true, "info": {...}}
	if isinstance(payload, dict) and payload.get("heartbeat"):
		dev_id = payload.get("device_id") or default_device
		if not _token_ok(token, dev_id):
			return {"ok": False, "error": "Invalid token", "_http_status": 401}
		protocol = "LAN Agent (ZK Pull)" if payload.get("source") == "LAN Agent" else "Generic Webhook"
		device = resolve_device(dev_id, protocol, payload.get("info") or {})
		if not device:
			return {"ok": False, "error": f"Device {dev_id} is not registered", "_http_status": 404}
		touch_device(device.name, **(payload.get("info") or {}))
		return {"ok": True, "device": device.name, "status": device.status}

	mapping = {
		"user": settings.webhook_user_field,
		"time": settings.webhook_time_field,
		"type": settings.webhook_type_field,
		"device": settings.webhook_device_field,
	}
	try:
		rows, errors = generic_adapter.parse_payload(payload, mapping, server_tz(), default_device)
	except ValueError as e:
		return {"ok": False, "error": str(e), "_http_status": 400}

	source = "Webhook"
	if isinstance(payload, dict) and payload.get("source") in ALLOWED_SOURCES:
		source = payload["source"]
	protocol = "LAN Agent (ZK Pull)" if source == "LAN Agent" else "Generic Webhook"

	# group by device
	by_device: dict[str, list] = {}
	for dev_id, punch in rows:
		by_device.setdefault(dev_id or "WEBHOOK", []).append(punch)

	if not by_device and errors:
		return {"ok": False, "errors": errors, "_http_status": 400}

	result = {"ok": True, "received": 0, "new": 0, "already_received": 0, "processed": 0, "errors": errors, "items": []}
	for dev_id, punches in by_device.items():
		if not _token_ok(token, dev_id):
			return {"ok": False, "error": "Invalid or missing token (send header X-Biometric-Token)", "_http_status": 401}
		device = resolve_device(dev_id, protocol)
		if not device:
			result["errors"].append(f"Device {dev_id} is not registered and auto-register is off")
			continue
		summary = ingest(device, punches, source)
		for key in ("received", "new", "already_received", "processed"):
			result[key] += summary[key]
		result["items"].extend(summary["items"][:200])
	return result


def handle_hikvision(args: dict, body: bytes, content_type: str | None) -> dict:
	settings = get_settings()
	if not cint(settings.enabled):
		return {"ok": False, "error": "disabled"}
	punches, meta = hikvision_adapter.parse_event(body, content_type, server_tz())
	ident = args.get("sn") or args.get("device") or meta.get("mac_address") or meta.get("device_id") or meta.get("device_ip")
	device = resolve_device(ident, "Hikvision ISAPI", meta)
	if not device:
		return {"ok": False, "error": "device not registered"}
	touch_device(device.name, device_ip=meta.get("device_ip"), mac_address=meta.get("mac_address"))
	if not punches:
		return {"ok": True, "ignored": meta.get("event_type") or "non-attendance event"}
	summary = ingest(device, punches, "Hikvision")
	return {"ok": True, "new": summary["new"], "processed": summary["processed"]}


# ---------------------------------------------------------------- /api/method/... versions


def _raw_request():
	req = frappe.local.request
	return dict(req.args or {}), req.get_data() or b"", req.headers


@frappe.whitelist(allow_guest=True, methods=["POST"])
def punch():
	"""Generic webhook. POST JSON, header X-Biometric-Token: <token from Biometric Settings>."""
	args, body, headers = _raw_request()
	result = handle_generic(args, body, headers)
	status = result.pop("_http_status", 200)
	frappe.local.response.http_status_code = status
	return result


@frappe.whitelist(allow_guest=True, methods=["POST"])
def hikvision():
	args, body, headers = _raw_request()
	return handle_hikvision(args, body, headers.get("Content-Type"))
