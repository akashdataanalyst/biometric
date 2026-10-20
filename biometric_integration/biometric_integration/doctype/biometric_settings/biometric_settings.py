import frappe
from frappe.model.document import Document


class BiometricSettings(Document):
	def validate(self):
		if self.server_url:
			self.server_url = self.server_url.strip().rstrip("/")
		for field, low in (("duplicate_window_seconds", 0), ("alternate_window_hours", 1), ("offline_after_minutes", 2)):
			if self.get(field) is not None and self.get(field) < low:
				self.set(field, low)

	def on_update(self):
		frappe.clear_cache(doctype="Biometric Settings")
