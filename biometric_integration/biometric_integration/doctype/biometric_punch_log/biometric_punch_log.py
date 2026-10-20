from frappe.model.document import Document


class BiometricPunchLog(Document):
	def autoname(self):
		# same device + user + second -> same name, so a re-sent punch can never be stored twice
		# (with "hash" naming Frappe silently retried a clashing insert under a new random name)
		from biometric_integration.engine import log_name

		self.name = log_name(self.device, self.user_id, self.punch_time)
