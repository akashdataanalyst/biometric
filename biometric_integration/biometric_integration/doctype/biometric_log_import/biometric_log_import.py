import frappe
from frappe import _
from frappe.model.document import Document

from biometric_integration.adapters.file_import import parse_file
from biometric_integration.engine import ingest, server_tz


class BiometricLogImport(Document):
	@frappe.whitelist()
	def start_import(self):
		frappe.only_for(("System Manager", "HR Manager"))
		if not self.import_file:
			frappe.throw(_("Attach a file first"))
		file_doc = frappe.get_doc("File", {"file_url": self.import_file})
		content = file_doc.get_content()
		if isinstance(content, str):
			content = content.encode()
		rows, errors = parse_file(
			content,
			file_doc.file_name or self.import_file,
			server_tz(),
			user_col=self.user_column,
			time_col=self.time_column,
			date_col=self.date_column,
			type_col=self.type_column,
			date_format=self.date_format,
		)
		totals = {"new": 0, "already_received": 0, "processed": 0, "unmapped": 0}
		by_device = {}
		for dev, punch in rows:
			# a device column in the file wins only if that device exists
			target = dev if dev and frappe.db.exists("Biometric Device", dev) else self.device
			by_device.setdefault(target, []).append(punch)
		for dev, punches in by_device.items():
			summary = ingest(dev, punches, "File Import")
			totals["new"] += summary["new"]
			totals["already_received"] += summary["already_received"]
			totals["processed"] += summary["processed"]
			totals["unmapped"] += len([i for i in summary["items"] if i.get("status") == "Unmapped"])

		self.total_rows = len(rows) + len(errors)
		self.new_punches = totals["new"]
		self.already_received = totals["already_received"]
		self.processed = totals["processed"]
		self.unmapped = totals["unmapped"]
		self.errors_count = len(errors)
		self.import_log = "\n".join(errors[:500]) or _("No errors")
		if not rows:
			self.status = "Failed"
		elif errors or totals["unmapped"]:
			self.status = "Partial Success"
		else:
			self.status = "Success"
		self.save()
		return {"status": self.status, **totals, "errors": len(errors)}
