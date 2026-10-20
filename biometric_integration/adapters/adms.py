"""ADMS / Push / "iclock" protocol.

Used by eSSL (K21, K23, K28, K30, K90, X990, MB160, uFace, iFace ...), ZKTeco,
Identix and most other ZK-firmware machines that have a
"Cloud Server Setting" / "ADMS" menu.

Flow (device is always the client, so no port forwarding is needed at the office):

    GET  /iclock/cdata?SN=XXX&options=all     -> handshake, server returns options
    POST /iclock/cdata?SN=XXX&table=ATTLOG    -> attendance punches (tab separated lines)
    POST /iclock/cdata?SN=XXX&table=OPERLOG   -> operation log / user info (we only acknowledge)
    GET  /iclock/getrequest?SN=XXX            -> heartbeat, server replies with queued commands
    POST /iclock/devicecmd?SN=XXX             -> device reports command results
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl

from biometric_integration.adapters.base import (
	ADMS_STATE,
	VERIFY_MODES,
	RawPunch,
	clean_user_id,
	parse_datetime,
)

_LINE_RE = re.compile(r"^\s*(\S+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)\s*(\d*)\s*(\d*)")


def parse_attlog(body: str, server_tz=None) -> tuple[list[RawPunch], list[str]]:
	"""Parse ATTLOG lines. Also used for the eSSL / ZKTeco USB export file (xxx_attlog.dat).

	Line: PIN <TAB> YYYY-MM-DD HH:MM:SS <TAB> STATUS <TAB> VERIFY <TAB> WORKCODE ...
	Returns (punches, bad_lines)."""
	punches, bad = [], []
	for raw_line in (body or "").splitlines():
		line = raw_line.strip("\r\n")
		if not line.strip():
			continue
		parts = [p.strip() for p in line.split("\t")]
		if len(parts) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", parts[1] or ""):
			pin, ts = parts[0], parts[1]
			status = parts[2] if len(parts) > 2 else ""
			verify = parts[3] if len(parts) > 3 else ""
		else:
			m = _LINE_RE.match(line)
			if not m:
				bad.append(line)
				continue
			pin, ts, status, verify = m.group(1), m.group(2), m.group(3), m.group(4)
		try:
			punch_time = parse_datetime(ts, server_tz)
		except ValueError:
			bad.append(line)
			continue
		punches.append(
			RawPunch(
				user_id=clean_user_id(pin),
				punch_time=punch_time,
				punch_type=ADMS_STATE.get(status),
				status_code=status,
				verify_mode=VERIFY_MODES.get(verify, verify),
				raw=line,
			)
		)
	return punches, bad


def adms_timezone(value) -> str | None:
	"""Machine's TimeZone option from hours ("5.5", "+5:30", "330").

	ZK firmware reads a value within -12..12 as hours and anything larger as minutes,
	so a half-hour zone like India must be sent as minutes (330), never as 5.5."""
	text = str(value if value is not None else "").strip().replace(" ", "")
	if not text:
		return None
	sign = -1 if text.startswith("-") else 1
	text = text.lstrip("+-")
	try:
		if ":" in text:
			h, m = text.split(":", 1)
			hours = int(h) + int(m) / 60
		else:
			hours = float(text)
	except ValueError:
		return None
	if hours > 14:  # already minutes
		return str(sign * int(hours))
	if hours == int(hours):
		return str(sign * int(hours))
	return str(sign * round(hours * 60))


def handshake_response(sn: str, timezone_hours: str | None = None, realtime: bool = True) -> str:
	"""Options returned on the first GET /iclock/cdata?SN=..&options=all."""
	lines = [
		f"GET OPTION FROM: {sn}",
		"ATTLOGStamp=None",
		"OPERLOGStamp=9999",
		"ATTPHOTOStamp=None",
		"ErrorDelay=30",
		"Delay=10",
		"TransTimes=00:00;14:05",
		"TransInterval=1",
		"TransFlag=TransData AttLog OpLog EnrollUser ChgUser",
		f"Realtime={1 if realtime else 0}",
		"Encrypt=None",
	]
	tz = adms_timezone(timezone_hours)
	if tz is not None:
		lines.append(f"TimeZone={tz}")
	return "\n".join(lines) + "\n"


def parse_info_param(info: str | None) -> dict:
	"""getrequest?INFO=Ver 8.0.4.2-20190117,12,12,300,192.168.1.201,10,7,12,0,111

	Order: firmware, users, fingerprints, attendance logs, ip, fp algorithm,
	face algorithm, faces, ... (varies a little by firmware)."""
	if not info:
		return {}
	parts = [p.strip() for p in info.split(",")]
	keys = ["firmware", "user_count", "fp_count", "att_count", "device_ip", "fp_algo", "face_algo", "face_count"]
	data = {}
	for key, value in zip(keys, parts):
		data[key] = value
	return data


def parse_options_body(body: str) -> dict:
	"""Body sent after an INFO command: '~DeviceName=K23,FWVersion=Ver 6.60...,IPAddress=..' (comma or newline separated)."""
	data = {}
	for chunk in re.split(r"[,\n]", body or ""):
		if "=" in chunk:
			k, v = chunk.split("=", 1)
			data[k.strip().lstrip("~")] = v.strip()
	mapped = {}
	for src, dst in (
		("DeviceName", "model"),
		("FWVersion", "firmware"),
		("FirmVer", "firmware"),
		("IPAddress", "device_ip"),
		("UserCount", "user_count"),
		("FPCount", "fp_count"),
		("TransactionCount", "att_count"),
		("FaceCount", "face_count"),
		("MAC", "mac_address"),
	):
		if data.get(src):
			mapped[dst] = data[src]
	return mapped


def parse_command_results(body: str) -> list[dict]:
	"""devicecmd body: one line per command: ID=12&Return=0&CMD=DATA"""
	results = []
	for line in (body or "").splitlines():
		line = line.strip()
		if not line or "ID=" not in line:
			continue
		data = dict(parse_qsl(line, keep_blank_values=True))
		if "ID" in data:
			results.append(data)
	return results


def build_command(cmd_id: str | int, command_type: str, **kw) -> str:
	"""Return the text line a device expects from /iclock/getrequest."""
	ct = command_type
	if ct == "Reboot":
		body = "REBOOT"
	elif ct == "Fetch Device Info":
		body = "INFO"
	elif ct == "Re-upload All Logs":
		body = "CHECK"
	elif ct == "Fetch Logs for Date Range":
		body = f"DATA QUERY ATTLOG StartTime={kw['from_time']}\tEndTime={kw['to_time']}"
	elif ct == "Push User to Device":
		name = (kw.get("user_name") or "").replace("\t", " ")[:24]
		card = kw.get("card") or ""
		body = f"DATA UPDATE USERINFO PIN={kw['user_id']}\tName={name}\tPri=0\tPasswd=\tCard={card}\tGrp=1\tTZ=0000000100000000\tVerify=0"
	elif ct == "Delete User from Device":
		body = f"DATA DELETE USERINFO PIN={kw['user_id']}"
	elif ct == "Clear Device Logs":
		body = "CLEAR LOG"
	elif ct == "Custom":
		body = kw["custom"]
	else:
		raise ValueError(f"Unknown command type {command_type}")
	return f"C:{cmd_id}:{body}"
