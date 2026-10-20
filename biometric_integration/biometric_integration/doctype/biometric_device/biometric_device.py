import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

# Model suggestions per Connection Type (the Model field is an Autocomplete that also accepts any
# other text, because machines that register themselves report their own model name).
MODELS_BY_PROTOCOL = {
	"ADMS (eSSL / ZKTeco Push)": [
		"eSSL K21 Pro",
		"eSSL K23",
		"eSSL K28",
		"eSSL K30 Pro",
		"eSSL K90 Pro",
		"eSSL X990",
		"eSSL MB160",
		"eSSL MB20",
		"eSSL uFace 202",
		"eSSL uFace 302",
		"eSSL uFace 800",
		"eSSL iFace 702",
		"ZKTeco SpeedFace",
		"ZKTeco ProFace X",
		"ZKTeco MB360",
		"ZKTeco uFace 302",
		"Identix K21",
		"Identix K30",
		"Other ADMS / Cloud Server machine",
	],
	"Hikvision ISAPI": [
		"Hikvision DS-K1T341 (face)",
		"Hikvision DS-K1T342 (face)",
		"Hikvision DS-K1T343 (face)",
		"Hikvision DS-K1T671 (face)",
		"Hikvision DS-K1T804 (fingerprint)",
		"Hikvision DS-K1A802 (fingerprint)",
		"Other Hikvision terminal",
	],
	"Generic Webhook": [
		"Matrix COSEC",
		"Suprema BioStar 2",
		"Realtime",
		"Mantra (middleware)",
		"Vendor cloud / middleware",
		"Own script / software",
	],
	"LAN Agent (ZK Pull)": [
		"ZKTeco K14",
		"ZKTeco K40",
		"ZKTeco iClock",
		"eSSL X990 (LAN only)",
		"eSSL K21 (LAN only)",
		"Identix K20",
		"Other ZK-protocol machine (port 4370)",
	],
	"File Import": [
		"eSSL USB download (1_attlog.dat)",
		"eTimeTrackLite CSV export",
		"Suprema BioStar CSV",
		"Matrix COSEC CSV",
		"Excel (.xlsx) export",
		"Other CSV / Excel",
	],
}


@frappe.whitelist()
def get_model_options() -> dict:
	return MODELS_BY_PROTOCOL


class BiometricDevice(Document):
	def validate(self):
		self.serial_no = (self.serial_no or "").strip()
		if not self.serial_no:
			frappe.throw(_("Serial No is required"))
		if self.mac_address:
			self.mac_address = self.mac_address.strip().upper()
		if not self.device_name:
			self.device_name = self.serial_no

	def on_update(self):
		before = self.get_doc_before_save()
		if before and before.status != "Active" and self.status == "Active":
			# punches that arrived while the device waited for approval or was disabled
			# (only "Device is disabled" ones: other Ignored punches are older than the cut-off date)
			names = frappe.get_all(
				"Biometric Punch Log",
				filters={"device": self.name, "status": "Device Not Approved"},
				pluck="name",
			) + frappe.get_all(
				"Biometric Punch Log",
				filters={"device": self.name, "status": "Ignored", "message": "Device is disabled"},
				pluck="name",
			)
			if names:
				frappe.enqueue(
					"biometric_integration.engine.reprocess_logs",
					names=names,
					statuses=("Device Not Approved", "Ignored"),
					queue="long",
					enqueue_after_commit=True,
					now=len(names) <= 300,
				)

	@frappe.whitelist()
	def send_command(self, command_type, **kwargs):
		if not self.protocol.startswith("ADMS"):
			frappe.throw(_("Remote commands work only for ADMS (eSSL / ZKTeco Push) machines"))
		doc = frappe.get_doc({"doctype": "Biometric Device Command", "device": self.name, "command_type": command_type, **kwargs})
		doc.insert()
		return doc.name

	@frappe.whitelist()
	def push_employees(self, only_branch=1):
		"""Queue a 'Push User to Device' command for every active employee with a device ID."""
		if not self.protocol.startswith("ADMS"):
			frappe.throw(_("Pushing users works only for ADMS (eSSL / ZKTeco Push) machines"))
		from biometric_integration.engine import _employee_map, clear_employee_cache

		clear_employee_cache()
		by_emp = {}
		for machine_id, r in _employee_map().items():
			if r.status == "Active" and r.name not in by_emp and (not self.company or r.company == self.company):
				by_emp[r.name] = machine_id
		branch_ok = None
		if cint(only_branch) and self.branch:
			branch_ok = set(frappe.get_all("Employee", filters={"branch": self.branch}, pluck="name"))
		emps = [
			frappe._dict(name=n, attendance_device_id=mid, employee_name=frappe.db.get_value("Employee", n, "employee_name"))
			for n, mid in by_emp.items()
			if branch_ok is None or n in branch_ok
		]
		for e in emps:
			frappe.get_doc(
				{
					"doctype": "Biometric Device Command",
					"device": self.name,
					"command_type": "Push User to Device",
					"employee": e.name,
					"user_id": e.attendance_device_id,
					"user_name": e.employee_name,
				}
			).insert()
		return len(emps)
