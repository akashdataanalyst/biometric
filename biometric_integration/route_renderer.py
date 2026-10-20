"""Answers the fixed URLs that punching machines call.

Machines cannot call /api/method/... - an eSSL K23 always calls /iclock/cdata,
a Hikvision terminal calls whatever short path you type into it. This renderer is
registered via the `page_renderer` hook so these paths work on a normal ERPNext
site without any nginx rewrite.

    /iclock/cdata, /iclock/getrequest, /iclock/devicecmd   ADMS (eSSL / ZKTeco ...)
    /biometric/hikvision                                     Hikvision HTTP listening
    /biometric/push                                          Generic JSON webhook
"""

import frappe
from werkzeug.wrappers import Response

ADMS_ENDPOINTS = {"cdata", "getrequest", "devicecmd", "fdata", "registry", "push", "ping", "querydata"}


class BiometricRouteRenderer:
	def __init__(self, path=None, http_status_code=None):
		self.path = (path or frappe.local.request.path or "").strip("/ ")
		self.http_status_code = http_status_code or 200

	def _parts(self):
		p = self.path.lower()
		if p.endswith(".aspx"):
			p = p[:-5]
		return p.split("/")

	def can_render(self):
		parts = self._parts()
		if len(parts) == 2 and parts[0] == "iclock" and parts[1] in ADMS_ENDPOINTS:
			return True
		return len(parts) == 2 and parts[0] == "biometric" and parts[1] in ("hikvision", "push")

	def render(self):
		parts = self._parts()
		request = frappe.local.request
		args = dict(request.args or {})
		body = request.get_data() or b""
		try:
			if parts[0] == "iclock":
				from biometric_integration.api.adms import handle

				text = handle(parts[1], request.method, args, body)
				frappe.db.commit()
				return Response(text, status=200, mimetype="text/plain")

			from biometric_integration.api import push

			if parts[1] == "hikvision":
				result = push.handle_hikvision(args, body, request.headers.get("Content-Type"))
			else:
				result = push.handle_generic(args, body, request.headers)
			frappe.db.commit()
			status = result.pop("_http_status", 200)
			return Response(frappe.as_json(result), status=status, mimetype="application/json")
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Biometric endpoint /{self.path}", reference_doctype="Biometric Device")
			frappe.db.commit()
			if parts[0] == "iclock":
				# non-OK answer -> device keeps the data and retries later
				return Response("ERROR: server error", status=200, mimetype="text/plain")
			return Response('{"ok": false, "error": "server error"}', status=500, mimetype="application/json")
