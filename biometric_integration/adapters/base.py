"""Shared building blocks for all protocol adapters (no Frappe imports here,
so this module can also be unit-tested and reused by the standalone tools)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

IN = "IN"
OUT = "OUT"


@dataclass
class RawPunch:
	user_id: str
	punch_time: datetime  # naive datetime in the device's local time / server time zone
	punch_type: str | None = None  # "IN", "OUT" or None (unknown)
	status_code: str = ""
	verify_mode: str = ""
	raw: str = ""
	device_hint: str = ""  # serial / id of the device if the payload carries it
	extra: dict = field(default_factory=dict)


# ADMS / ZKTeco punch-state codes
ADMS_STATE = {
	"0": IN,  # Check-In
	"1": OUT,  # Check-Out
	"2": OUT,  # Break-Out
	"3": IN,  # Break-In
	"4": IN,  # Overtime-In
	"5": OUT,  # Overtime-Out
}

VERIFY_MODES = {
	"0": "Password",
	"1": "Fingerprint",
	"2": "Card",
	"3": "Password",
	"4": "Card",
	"9": "Other",
	"11": "Card",
	"15": "Face",
	"25": "Palm",
}

_TEXT_TYPES = {
	IN: {
		"in",
		"i",
		"checkin",
		"check-in",
		"check_in",
		"check in",
		"clockin",
		"clock in",
		"entry",
		"enter",
		"breakin",
		"break-in",
		"break in",
		"overtimein",
		"otin",
		"ot-in",
		"ot in",
		"duty on",
		"login",
	},
	OUT: {
		"out",
		"o",
		"checkout",
		"check-out",
		"check_out",
		"check out",
		"clockout",
		"clock out",
		"exit",
		"breakout",
		"break-out",
		"break out",
		"overtimeout",
		"otout",
		"ot-out",
		"ot out",
		"duty off",
		"logout",
	},
}


def punch_type_from_value(value) -> str | None:
	"""Normalise anything a machine may send ("0", 1, "checkIn", "OUT" ...) to IN / OUT / None."""
	if value is None:
		return None
	text = str(value).strip()
	if not text:
		return None
	if text in ADMS_STATE:
		return ADMS_STATE[text]
	low = text.lower()
	for ptype, words in _TEXT_TYPES.items():
		if low in words:
			return ptype
	squashed = low.replace(" ", "").replace("-", "").replace("_", "")
	if squashed.startswith("checkin") or squashed in ("in1", "punchin", "clockin"):
		return IN
	if squashed.startswith("checkout") or squashed in ("out1", "punchout", "clockout"):
		return OUT
	return None


_DT_FORMATS = (
	"%Y-%m-%d %H:%M:%S",
	"%Y-%m-%d %H:%M",
	"%Y-%m-%dT%H:%M:%S",
	"%Y-%m-%dT%H:%M",
	"%Y/%m/%d %H:%M:%S",
	"%Y/%m/%d %H:%M",
	"%d-%m-%Y %H:%M:%S",
	"%d-%m-%Y %H:%M",
	"%d/%m/%Y %H:%M:%S",
	"%d/%m/%Y %H:%M",
	"%d.%m.%Y %H:%M:%S",
	"%d.%m.%Y %H:%M",
	"%d-%b-%Y %H:%M:%S",
	"%d-%b-%Y %H:%M",
	"%d %b %Y %H:%M:%S",
	"%d %b %Y %I:%M %p",
	"%d-%m-%Y %I:%M:%S %p",
	"%d-%m-%Y %I:%M %p",
	"%d/%m/%Y %I:%M:%S %p",
	"%d/%m/%Y %I:%M %p",
	"%Y-%m-%d %I:%M:%S %p",
	"%Y-%m-%d %I:%M %p",
	"%m/%d/%Y %H:%M:%S",
	"%m/%d/%Y %I:%M:%S %p",
	"%Y%m%d%H%M%S",
)


def parse_datetime(value, server_tz=None, date_format: str | None = None) -> datetime:
	"""Parse the many timestamp styles machines use. Returns a naive datetime.

	* ISO strings with an offset (Hikvision: 2026-10-18T09:01:23+05:30) are converted
	  to `server_tz` (a tzinfo) so they line up with ERPNext time.
	* Epoch seconds / milliseconds are treated as UTC and converted the same way.
	* Everything else is assumed to already be local (server) time.
	"""
	if isinstance(value, datetime):
		dt = value
	elif isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{10}(\d{3})?", value.strip())):
		num = float(value)
		if num > 1e11:
			num /= 1000.0
		dt = datetime.fromtimestamp(num, tz=timezone.utc)
	else:
		text = str(value).strip()
		if not text:
			raise ValueError("Empty timestamp")
		dt = None
		if date_format:
			dt = datetime.strptime(text, date_format)
		else:
			iso = text.replace("Z", "+00:00")
			# drop fractional seconds like 09:01:23.123
			iso = re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", iso)
			try:
				dt = datetime.fromisoformat(iso)
			except ValueError:
				for fmt in _DT_FORMATS:
					try:
						dt = datetime.strptime(text, fmt)
						break
					except ValueError:
						continue
		if dt is None:
			raise ValueError(f"Could not understand timestamp '{text}'")

	if dt.tzinfo is not None:
		if server_tz is not None:
			dt = dt.astimezone(server_tz)
		dt = dt.replace(tzinfo=None)
	return dt.replace(microsecond=0)


def clean_user_id(value) -> str:
	text = str(value if value is not None else "").strip()
	if text.endswith(".0") and text[:-2].isdigit():  # Excel turns 101 into 101.0
		text = text[:-2]
	return text


def apply_offset(dt: datetime, minutes: int | None) -> datetime:
	return dt + timedelta(minutes=int(minutes or 0)) if minutes else dt
