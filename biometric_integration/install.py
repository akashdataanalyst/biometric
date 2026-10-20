from urllib.parse import urlparse

import frappe
from frappe.utils import get_url


def after_install():
	s = frappe.get_doc("Biometric Settings")
	defaults = {
		"enabled": 1,
		"adms_port": 80,
		"auto_register_devices": 1,
		"auto_approve_devices": 0,
		"match_employee_by": "Device ID, Emp Code or Employee ID",
		"log_type_mode": "Alternate IN / OUT",
		"alternate_window_hours": 14,
		"duplicate_window_seconds": 60,
		"strip_leading_zeros": 1,
		"future_tolerance_minutes": 15,
		"update_shift_last_sync": 1,
		"offline_after_minutes": 10,
		"keep_logs_days": 0,
	}
	for k, v in defaults.items():
		if s.get(k) in (None, ""):
			s.set(k, v)
	if not s.server_url:
		host = urlparse(get_url()).hostname
		if host and host not in ("localhost", "127.0.0.1"):
			s.server_url = host
	if not s.get_password("api_token", raise_exception=False):
		s.api_token = frappe.generate_hash(length=32)
	s.flags.ignore_mandatory = True
	s.save(ignore_permissions=True)
	clear_route_cache()


def after_migrate():
	if frappe.db.get_single_value("Biometric Settings", "match_employee_by") in (None, ""):
		frappe.db.set_single_value("Biometric Settings", "match_employee_by", "Device ID, Emp Code or Employee ID")
	clear_route_cache()


APP_ROLES = {"System Manager", "HR Manager", "HR User"}


def has_app_permission():
	"""Apps-screen tile: the roles that can read Biometric Device / Punch Log."""
	return bool(APP_ROLES.intersection(frappe.get_roles()))


def clear_route_cache():
	"""If a machine called /iclock/... before this app was installed, Frappe may
	have cached that URL as 404. Clear it so the machine connects right away."""
	try:
		frappe.cache.delete_value("website_404")
	except Exception:
		pass
