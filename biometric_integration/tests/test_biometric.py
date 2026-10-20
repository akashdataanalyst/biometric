"""End-to-end tests: the exact requests a machine sends, through to Employee Checkin.

    bench --site test.localhost run-tests --app biometric_integration
"""

import json
from datetime import timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, nowdate

from biometric_integration import tasks
from biometric_integration.adapters.adms import adms_timezone, handshake_response, parse_attlog
from biometric_integration.adapters.base import parse_datetime
from biometric_integration.api import adms as adms_api
from biometric_integration.api import push as push_api
from biometric_integration.engine import clear_employee_cache, ingest, log_name

LOG = "Biometric Punch Log"
DEVICE = "Biometric Device"
SN = "UT-ADMS-1"
UID = "770001"


def _ts(dt):
	return dt.strftime("%Y-%m-%d %H:%M:%S")


def adms(endpoint, method="GET", body="", **args):
	return adms_api.handle(endpoint, method, {"SN": SN, **args}, body.encode())


class TestAdapters(IntegrationTestCase):
	def test_timezone_sent_as_zk_expects(self):
		self.assertEqual(adms_timezone("5.5"), "330")
		self.assertEqual(adms_timezone(5.5), "330")
		self.assertEqual(adms_timezone("+5:30"), "330")
		self.assertEqual(adms_timezone("330"), "330")
		self.assertEqual(adms_timezone("8"), "8")
		self.assertEqual(adms_timezone("-3.5"), "-210")
		self.assertIsNone(adms_timezone(""))
		self.assertIsNone(adms_timezone("abc"))
		self.assertIn("TimeZone=330\n", handshake_response("X", 5.5))
		self.assertNotIn("TimeZone", handshake_response("X", None))

	def test_attlog_lines(self):
		punches, bad = parse_attlog("101\t2026-10-19 09:01:02\t0\t1\t0\t0\n  \n102 2026-10-19 18:00 1 15\njunk\n")
		self.assertEqual([p.user_id for p in punches], ["101", "102"])
		self.assertEqual([p.punch_type for p in punches], ["IN", "OUT"])
		self.assertEqual(punches[1].verify_mode, "Face")
		self.assertEqual(bad, ["junk"])

	def test_iso_offset_converted_to_site_time(self):
		from zoneinfo import ZoneInfo

		dt = parse_datetime("2026-10-19T03:31:00Z", ZoneInfo("Asia/Kolkata"))
		self.assertEqual(_ts(dt), "2026-10-19 09:01:00")


class TestMachineFlow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.local.request_ip = "10.1.2.3"
		s = frappe.get_doc("Biometric Settings")
		s.update(
			{
				"enabled": 1,
				"auto_register_devices": 1,
				"auto_approve_devices": 0,
				"device_timezone": "",
				"match_employee_by": "Device ID, Emp Code or Employee ID",
				"log_type_mode": "Alternate IN / OUT",
				"duplicate_window_seconds": 60,
				"ignore_punches_before": None,
			}
		)
		if not s.get_password("api_token", raise_exception=False):
			s.api_token = frappe.generate_hash(length=32)
		s.save()
		frappe.db.set_default("time_zone", "Asia/Kolkata")
		company = frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.emp = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": "UT Biometric",
				"gender": frappe.get_all("Gender", pluck="name", limit=1)[0],
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"company": company,
				"status": "Active",
			}
		).insert()

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()
		super().tearDownClass()

	def setUp(self):
		clear_employee_cache()
		frappe.clear_document_cache("Biometric Settings", "Biometric Settings")

	def _log(self, uid, dt):
		return frappe.get_doc(LOG, log_name(SN, uid, dt))

	def test_full_adms_flow(self):
		# 1. first contact: device registers itself, waits for approval; handshake carries the site time zone
		hello = adms("cdata", options="all", pushver="2.4.1")
		self.assertIn(f"GET OPTION FROM: {SN}", hello)
		self.assertIn("TimeZone=330", hello)
		dev = frappe.get_doc(DEVICE, SN)
		self.assertEqual(dev.status, "Pending Approval")
		self.assertEqual(dev.device_ip, "10.1.2.3")

		# 2. heartbeat with device info
		self.assertEqual(adms("getrequest", INFO="Ver 6.60,3,2,15,192.168.1.201"), "OK")
		self.assertEqual(frappe.db.get_value(DEVICE, SN, "firmware"), "Ver 6.60")

		# 3. punches before approval are kept
		t_in = (now_datetime() - timedelta(hours=9)).replace(microsecond=0)
		t_out = t_in + timedelta(hours=8, minutes=30)
		body = f"{UID}\t{_ts(t_in)}\t0\t1\t0\t0\n{UID}\t{_ts(t_out)}\t0\t1\t0\t0\n"
		self.assertEqual(adms("cdata", "POST", body, table="ATTLOG", Stamp="1"), "OK: 2")
		self.assertEqual(self._log(UID, t_in).status, "Device Not Approved")

		# 4. machine re-sends the same batch: nothing doubled
		self.assertEqual(adms("cdata", "POST", body, table="ATTLOG", Stamp="1"), "OK: 2")
		self.assertEqual(frappe.db.count(LOG, {"device": SN}), 2)

		# 5. approve -> punches without an employee become Unmapped
		dev.reload()
		dev.status = "Active"
		dev.save()
		self.assertEqual(self._log(UID, t_in).status, "Unmapped")

		# 6. HR sets the machine ID on the employee -> waiting punches turn into checkins IN, OUT
		self.emp.reload()
		self.emp.attendance_device_id = UID
		self.emp.save()
		first, second = self._log(UID, t_in), self._log(UID, t_out)
		self.assertEqual((first.status, first.log_type), ("Processed", "IN"))
		self.assertEqual((second.status, second.log_type), ("Processed", "OUT"))
		checkin = frappe.get_doc("Employee Checkin", first.employee_checkin)
		self.assertEqual((checkin.employee, checkin.device_id, checkin.log_type), (self.emp.name, SN, "IN"))

		# 7. double punch within the duplicate window
		body = f"{UID}\t{_ts(t_out + timedelta(seconds=20))}\t0\t1\n"
		adms("cdata", "POST", body, table="ATTLOG")
		self.assertEqual(self._log(UID, t_out + timedelta(seconds=20)).status, "Duplicate")

		# 8. disabled machine: ignored; enabled again: processed
		dev.reload()
		dev.status = "Disabled"
		dev.save()
		t3 = t_out + timedelta(minutes=10)
		adms("cdata", "POST", f"{UID}\t{_ts(t3)}\t0\t1\n", table="ATTLOG")
		self.assertEqual(self._log(UID, t3).status, "Ignored")
		dev.reload()
		dev.status = "Active"
		dev.save()
		self.assertEqual(self._log(UID, t3).status, "Processed")

		# 9. future time (machine clock wrong) is an Error, not a checkin
		t_future = now_datetime() + timedelta(hours=3)
		adms("cdata", "POST", f"{UID}\t{_ts(t_future)}\t0\t1\n", table="ATTLOG")
		self.assertEqual(self._log(UID, t_future).status, "Error")

		# 10. remote command: queued -> sent on heartbeat -> result reported
		cmd = dev.send_command("Fetch Device Info")
		cmd_id = frappe.db.get_value("Biometric Device Command", cmd, "cmd_id")
		self.assertEqual(adms("getrequest").strip(), f"C:{cmd_id}:INFO")
		self.assertEqual(frappe.db.get_value("Biometric Device Command", cmd, "status"), "Sent")
		self.assertEqual(adms("getrequest"), "OK")
		adms("devicecmd", "POST", f"ID={cmd_id}&Return=0&CMD=INFO\n")
		self.assertEqual(frappe.db.get_value("Biometric Device Command", cmd, "status"), "Success")

	def test_ingest_survives_concurrent_duplicate(self):
		frappe.get_doc({"doctype": DEVICE, "serial_no": "UT-RACE", "device_name": "UT-RACE", "status": "Active"}).insert()
		from biometric_integration.adapters.base import RawPunch

		t = (now_datetime() - timedelta(hours=1)).replace(microsecond=0)
		punch = RawPunch(user_id="UT-NOBODY", punch_time=t)
		ingest("UT-RACE", [punch], "ADMS")
		# simulate the other request having inserted the row between exists() and insert()
		orig = frappe.db.exists
		frappe.db.exists = lambda dt, *a, **k: False if dt == LOG else orig(dt, *a, **k)
		try:
			summary = ingest("UT-RACE", [punch], "ADMS")
		finally:
			frappe.db.exists = orig
		self.assertEqual((summary["new"], summary["already_received"]), (0, 1))

	def test_webhook_needs_token(self):
		body = json.dumps({"device_id": "UT-HOOK", "user_id": "UT-X", "timestamp": _ts(now_datetime() - timedelta(minutes=5))})
		res = push_api.handle_generic({}, body.encode(), {})
		self.assertEqual(res["_http_status"], 401)
		token = frappe.get_doc("Biometric Settings").get_password("api_token")
		res = push_api.handle_generic({}, body.encode(), {"X-Biometric-Token": token})
		self.assertTrue(res["ok"])
		self.assertEqual(res["new"], 1)
		self.assertEqual(frappe.db.get_value(DEVICE, "UT-HOOK", "protocol"), "Generic Webhook")

	def test_hikvision_event(self):
		event = {
			"macAddress": "aa:bb:cc:dd:ee:01",
			"ipAddress": "192.168.1.64",
			"dateTime": (now_datetime() - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S+05:30"),
			"eventType": "AccessControllerEvent",
			"AccessControllerEvent": {"employeeNoString": "UT-H1", "attendanceStatus": "checkOut"},
		}
		res = push_api.handle_hikvision({"sn": "UT-HIK"}, json.dumps(event).encode(), "application/json")
		self.assertEqual(res, {"ok": True, "new": 1, "processed": 0})
		log = frappe.get_all(LOG, filters={"device": "UT-HIK"}, fields=["user_id", "device_punch_type"])[0]
		self.assertEqual((log.user_id, log.device_punch_type), ("UT-H1", "OUT"))

	def test_shift_sync_waits_for_a_real_machine(self):
		frappe.db.set_value(DEVICE, {"is_virtual": 0}, "last_heartbeat", add_days(nowdate(), -5))
		self.assertIsNone(tasks.sync_upto())
		frappe.get_doc(
			{"doctype": DEVICE, "serial_no": "UT-LIVE", "device_name": "UT-LIVE", "status": "Active"}
		).insert()
		beat = (now_datetime() - timedelta(minutes=2)).replace(microsecond=0)
		frappe.db.set_value(DEVICE, "UT-LIVE", "last_heartbeat", beat)
		self.assertEqual(tasks.sync_upto(), beat)
