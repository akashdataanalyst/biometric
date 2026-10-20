frappe.ui.form.on("Biometric Log Import", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.import_file) {
			frm.add_custom_button(frm.doc.status === "Not Started" ? __("Start Import") : __("Import Again"), () =>
				frm.call({ method: "start_import", doc: frm.doc, freeze: true, freeze_message: __("Importing punches...") })
					.then((r) => {
						frm.reload_doc();
						const m = r.message;
						frappe.msgprint({
							title: __("Import {0}", [m.status]),
							message: __("New punches: {0}<br>Already received: {1}<br>Checkins created: {2}<br>Unmapped: {3}<br>Rows with errors: {4}",
								[m.new, m.already_received, m.processed, m.unmapped, m.errors]),
						});
					})).addClass("btn-primary");
		}
		if (frm.is_new()) {
			frm.set_intro(__("Attach the file and save, then click Start Import. Re-importing the same file is safe: already received punches are skipped."));
		}
	},
});
