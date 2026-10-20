import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime

from biometric_integration.adapters.adms import build_command
from biometric_integration.engine import machine_id_of


class BiometricDeviceCommand(Document):
	def validate(self):
		if self.command_type in ("Push User to Device", "Delete User from Device"):
			if self.employee and not self.user_id:
				self.user_id = machine_id_of(self.employee)
			if self.employee and not self.user_name:
				self.user_name = frappe.db.get_value("Employee", self.employee, "employee_name")
			if not self.user_id:
				frappe.throw(_("Device User ID is required (set Attendance Device ID or Emp Code on the Employee)"))
		if self.command_type == "Fetch Logs for Date Range" and get_datetime(self.from_time) > get_datetime(self.to_time):
			frappe.throw(_("From must be before To"))

	def before_insert(self):
		self.status = "Pending"

	def after_insert(self):
		self.db_set("cmd_id", cint(self.name.split("-")[-1]))
		self.db_set("command_text", self._text())

	def _text(self):
		fmt = "%Y-%m-%d %H:%M:%S"
		return build_command(
			self.cmd_id,
			self.command_type,
			from_time=get_datetime(self.from_time).strftime(fmt) if self.from_time else None,
			to_time=get_datetime(self.to_time).strftime(fmt) if self.to_time else None,
			user_id=self.user_id,
			user_name=self.user_name,
			card=self.card_no,
			custom=self.custom_command,
		)

	@frappe.whitelist()
	def cancel_command(self):
		if self.status in ("Pending", "Sent"):
			self.db_set("status", "Cancelled")

	@frappe.whitelist()
	def retry(self):
		self.db_set({"status": "Pending", "sent_on": None, "completed_on": None, "return_code": None, "response": None})
