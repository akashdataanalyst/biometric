"""Connection details and step-by-step setup instructions shown in the UI,
plus pre-configured downloads of the LAN agent and the device simulator."""

import io
import json
import os
import zipfile
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_url
from frappe.utils.password import get_decrypted_password

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


def connection_info() -> dict:
	s = frappe.get_cached_doc("Biometric Settings")
	raw = (s.server_url or "").strip()
	if raw and "://" not in raw:
		raw = "http://" + raw
	parsed = urlparse(raw) if raw else urlparse(get_url())
	host = parsed.hostname or urlparse(get_url()).hostname
	# a port typed in the address (erp.example.com:8081) wins over the 'Port for Machines' default
	port = (parsed.port if s.server_url else None) or cint(s.adms_port) or 80
	machine_base = f"http://{host}" + ("" if port == 80 else f":{port}")
	return {
		"host": host,
		"port": port,
		"machine_base": machine_base,
		"https_base": get_url(),
		"adms_url": f"{machine_base}/iclock/cdata",
		"hikvision_path": "/biometric/hikvision",
		"webhook_url": f"{get_url()}/biometric/push",
		"webhook_url_alt": f"{get_url()}/api/method/biometric_integration.api.push.punch",
		"server_url_set": bool(s.server_url),
	}


def _steps(items):
	return "<ol style='padding-left:18px;line-height:1.9'>" + "".join(f"<li>{i}</li>" for i in items) + "</ol>"


def _code(text):
	return f"<code style='font-size:13px'>{escape_html(str(text))}</code>"


@frappe.whitelist()
def get_device_setup(device: str) -> dict:
	frappe.has_permission("Biometric Device", "read", device, throw=True)
	d = frappe.get_doc("Biometric Device", device)
	info = connection_info()
	warn = ""
	if not info["server_url_set"]:
		warn = (
			"<div class='alert alert-warning'>"
			+ _("Set 'Public Server Address' in Biometric Settings first. Showing this site's address for now.")
			+ "</div>"
		)
	p = d.protocol
	if p.startswith("ADMS"):
		html = _steps(
			[
				_("On the machine open <b>Menu &rarr; COMM. &rarr; Cloud Server Setting</b> (on some models: <b>ADMS</b> or <b>Web Server</b>)."),
				_("Server Mode: <b>ADMS</b> (if asked). Enable Domain Name: <b>ON</b> if you use a name instead of an IP."),
				_("Server Address: {0}").format(_code(info["host"])),
				_("Server Port: {0}").format(_code(info["port"])),
				_("Enable Proxy Server: <b>OFF</b>. HTTPS: <b>OFF</b>."),
				_("Check <b>COMM. &rarr; Ethernet</b>: correct Gateway and DNS (e.g. 8.8.8.8) so the machine can reach the internet."),
				_("Save. Within a minute the cloud / server icon on the machine screen shows connected and this device turns <b>Online</b>."),
				_("Serial number of this device: {0} (must match the machine).").format(_code(d.serial_no)),
			]
		)
	elif p.startswith("Hikvision"):
		html = _steps(
			[
				_("Open the terminal's web page (or iVMS-4200) &rarr; <b>Configuration &rarr; Network &rarr; Advanced &rarr; HTTP Listening</b> (some firmware: <b>Event Alarm &rarr; Alarm Host</b>)."),
				_("IP / Domain: {0}").format(_code(info["host"])),
				_("Port: {0}, Protocol: <b>HTTP</b>").format(_code(info["port"])),
				_("URL: {0}").format(_code(f"{info['hikvision_path']}?sn={d.serial_no}")),
				_("Under <b>Access Control &rarr; Attendance</b> enable attendance status if you want the terminal to ask Check-In / Check-Out."),
				_("Save and verify a face / finger. The punch shows in the Live Monitor."),
			]
		)
	elif p.startswith("Generic"):
		sample = json.dumps(
			{"device_id": d.serial_no, "user_id": "101", "timestamp": "2026-10-18 09:01:00", "type": "IN"}, indent=2
		)
		html = _steps(
			[
				_("Send an HTTP <b>POST</b> to {0}").format(_code(info["webhook_url"])),
				_("Header {0} with the API Token from Biometric Settings (or this device's own token).").format(
					_code("X-Biometric-Token: <token>")
				),
				_("JSON body (one punch or a list; most field names are detected automatically):")
				+ f"<pre style='margin-top:6px'>{escape_html(sample)}</pre>",
				_("Works for vendor software / cloud (Matrix COSEC, Suprema BioStar, Realtime, Mantra ...), middleware or your own scripts."),
			]
		)
	elif p.startswith("LAN Agent"):
		html = _steps(
			[
				_("For machines on the office network that have no ADMS / cloud option (they talk on TCP port 4370)."),
				_("Click <b>Download LAN Agent</b> above. It comes pre-filled with this server, token and device."),
				_("On any office PC that stays on (Windows or Linux): install Python 3, run {0}, then {1}.").format(
					_code("pip install pyzk requests"), _code("python lan_sync_agent.py")
				),
				_("Set the machine's IP in {0} if it is not filled in. The agent pulls new punches every minute.").format(
					_code("config.json")
				),
			]
		)
	else:
		html = _steps(
			[
				_("Export punches from the machine (USB pen drive: e.g. {0}) or from its software as CSV / Excel.").format(
					_code("1_attlog.dat")
				),
				_("Open <b>Biometric Log Import &rarr; New</b>, choose this device, attach the file and click <b>Start Import</b>."),
				_("Columns are detected automatically; fill Column Mapping only if needed."),
			]
		)
	test = (
		"<p class='text-muted' style='margin-top:8px'>"
		+ _("No machine yet? Use <b>Test &rarr; Simulate Punch</b> above or open the Biometric Simulator.")
		+ "</p>"
	)
	return {"html": warn + html + test, "info": info}


@frappe.whitelist()
def get_token_help() -> str:
	info = connection_info()
	return (
		"<div style='line-height:1.9'>"
		+ _("Webhook URL")
		+ f": {_code(info['webhook_url'])}<br>"
		+ _("Header")
		+ f": {_code('X-Biometric-Token: <API Token>')}<br>"
		+ _("ADMS address for machines")
		+ f": {_code(info['host'])} "
		+ _("port")
		+ f" {_code(info['port'])}</div>"
	)


@frappe.whitelist()
def generate_token() -> str:
	frappe.only_for(("System Manager", "HR Manager"))
	token = frappe.generate_hash(length=32)
	s = frappe.get_doc("Biometric Settings")
	s.api_token = token
	s.save()
	return token


@frappe.whitelist()
def reveal_token() -> str:
	frappe.only_for(("System Manager", "HR Manager"))
	return get_decrypted_password("Biometric Settings", "Biometric Settings", "api_token", raise_exception=False) or ""


def _zip_response(filename: str, files: dict):
	buf = io.BytesIO()
	with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
		for name, content in files.items():
			z.writestr(name, content)
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = buf.getvalue()
	frappe.local.response.type = "download"


@frappe.whitelist()
def download_lan_agent(device: str | None = None):
	frappe.only_for(("System Manager", "HR Manager"))
	info = connection_info()
	devices = []
	filters = {"protocol": "LAN Agent (ZK Pull)", "status": ["!=", "Disabled"]}
	if device:
		filters["name"] = device
	for d in frappe.get_all("Biometric Device", filters=filters, fields=["name", "device_ip", "device_name"]):
		devices.append({"serial": d.name, "name": d.device_name, "ip": d.device_ip or "192.168.1.201", "port": 4370, "password": 0})
	if not devices:
		devices = [{"serial": "OFFICE-GATE-1", "name": "Office Gate", "ip": "192.168.1.201", "port": 4370, "password": 0}]
	config = {
		"server_url": info["https_base"],
		"token": reveal_token(),
		"interval_seconds": 60,
		"devices": devices,
	}
	_zip_response(
		"biometric_lan_agent.zip",
		{
			"lan_sync_agent.py": open(os.path.join(TOOLS_DIR, "lan_sync_agent.py")).read(),
			"config.json": json.dumps(config, indent=2),
			"README.txt": open(os.path.join(TOOLS_DIR, "LAN_AGENT_README.txt")).read(),
		},
	)


@frappe.whitelist()
def download_simulator():
	frappe.only_for(("System Manager", "HR Manager"))
	info = connection_info()
	readme = open(os.path.join(TOOLS_DIR, "SIMULATOR_README.txt")).read()
	readme = readme.replace("{{MACHINE_BASE}}", info["machine_base"]).replace("{{HTTPS_BASE}}", info["https_base"])
	_zip_response(
		"biometric_device_simulator.zip",
		{
			"device_simulator.py": open(os.path.join(TOOLS_DIR, "device_simulator.py")).read(),
			"README.txt": readme,
		},
	)
