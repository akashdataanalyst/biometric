#!/usr/bin/env python3
"""
Biometric device simulator - behaves like a real punching machine over the network.

No extra packages needed (Python 3.8+ standard library only). Works on Windows,
Linux and macOS.

Pretend to be an eSSL / ZKTeco machine (ADMS push protocol), exactly like a K23 / K28:
    python device_simulator.py --server http://erp.yourcompany.com adms --sn SIM-K23-001 --users 101,102,103

    It connects (handshake), sends a heartbeat every 10 s, answers remote commands
    from ERPNext (Fetch Device Info, Re-upload Logs, Push User, Reboot ...) and
    lets you punch by typing:   101 in   /   102 out   /   103

Hikvision face terminal:
    python device_simulator.py --server http://erp.yourcompany.com hikvision --sn HIK-GATE-1 --punch 101:in

Any machine through the webhook (needs the API token from Biometric Settings):
    python device_simulator.py --server https://erp.yourcompany.com --token XXXX webhook --sn GATE-1 --punch 101:in

Useful options:
    --punch 101:in --punch 102:out   send these punches right away
    --auto 30                        random punch from --users every 30 seconds
    --once                           send handshake + punches + one heartbeat, then exit
    --time "2026-10-18 09:05:00"     time for --punch (default: now)
"""

import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

STATE_CODE = {"in": "0", "out": "1", "": "255", None: "255"}
UA = "iClock Proxy/1.09"  # what real ZK firmware sends


def now_str():
	return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg, kind="info"):
	colors = {"info": "", "ok": "\033[92m", "err": "\033[91m", "cmd": "\033[96m", "send": "\033[93m"}
	end = "\033[0m" if colors.get(kind) else ""
	if not sys.stdout.isatty():
		colors, end = {}, ""
	print(f"{colors.get(kind, '')}[{datetime.now().strftime('%H:%M:%S')}] {msg}{end}", flush=True)


def http(method, url, params=None, body=None, headers=None, timeout=20):
	if params:
		url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
	data = body.encode() if isinstance(body, str) else body
	req = urllib.request.Request(url, data=data, method=method)
	req.add_header("User-Agent", UA)
	for k, v in (headers or {}).items():
		req.add_header(k, v)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as r:
			return r.status, r.read().decode("utf-8", "ignore")
	except urllib.error.HTTPError as e:
		return e.code, e.read().decode("utf-8", "ignore")
	except Exception as e:  # network problem
		return 0, f"{e.__class__.__name__}: {e}"


def parse_punch(text, default_type=None):
	"""'101:in', '101 out', '101' -> ('101', 'in'|'out'|None)"""
	text = text.strip().replace(":", " ").replace(",", " ")
	parts = text.split()
	if not parts:
		return None, None
	ptype = parts[1].lower() if len(parts) > 1 else default_type
	if ptype not in (None, "in", "out"):
		ptype = None
	return parts[0], ptype


# --------------------------------------------------------------------------- ADMS


class AdmsDevice:
	def __init__(self, base, sn, users, heartbeat):
		self.base = base.rstrip("/")
		self.sn = sn
		self.users = {u: f"User {u}" for u in users}
		self.heartbeat = heartbeat
		self.logs = []  # everything "stored" on the machine, for re-upload commands
		self.stamp = 1000
		self.lock = threading.Lock()

	def url(self, path):
		return f"{self.base}/iclock/{path}"

	def handshake(self):
		log(f"Connecting as eSSL/ZKTeco machine SN={self.sn} -> {self.base}")
		code, text = http("GET", self.url("cdata"), {"SN": self.sn, "options": "all", "pushver": "2.4.1", "language": "69"})
		if code == 200 and "GET OPTION FROM" in text:
			log("Handshake OK - server accepted the machine", "ok")
			for line in text.strip().splitlines()[:4]:
				log(f"   {line}")
			return True
		log(f"Handshake FAILED (HTTP {code}): {text[:300]}", "err")
		self.explain(code, text)
		return False

	def explain(self, code, text):
		if code == 0:
			log("Cannot reach the server. Check the address/port, AWS Security Group and that nginx listens on this port.", "err")
		elif code in (301, 302, 307, 308):
			log("Server redirects (probably to HTTPS). Real machines can't follow that - see README > AWS / nginx.", "err")
		elif code == 404:
			log("404: the app is not installed on this site, or the domain points to another site.", "err")
		elif "not registered" in text:
			log("Server refused the machine: turn on 'Auto-register New Machines' or create the device with this serial no.", "err")
		elif "disabled" in text:
			log("Integration is disabled in Biometric Settings.", "err")

	def punch(self, user_id, ptype=None, when=None):
		when = when or now_str()
		line = f"{user_id}\t{when}\t{STATE_CODE.get(ptype, '255')}\t1\t0\t0\t0\t0\t0\t0"
		with self.lock:
			self.logs.append(line)
		self.upload([line])

	def upload(self, lines):
		self.stamp += 1
		code, text = http(
			"POST",
			self.url("cdata"),
			{"SN": self.sn, "table": "ATTLOG", "Stamp": str(self.stamp)},
			"\n".join(lines) + "\n",
			{"Content-Type": "text/plain"},
		)
		if code == 200 and text.startswith("OK"):
			log(f"Sent {len(lines)} punch(es): {lines[0].split(chr(9))[:2]}{' ...' if len(lines) > 1 else ''} -> {text.strip()}", "send")
		else:
			log(f"Upload failed (HTTP {code}): {text[:200]}", "err")
			self.explain(code, text)

	def info_payload(self):
		return (
			f"~DeviceName=K23 Simulator,FWVersion=Ver 6.60 Sim,IPAddress=192.168.1.201,MAC=00:17:61:SI:MU:01,"
			f"UserCount={len(self.users)},FPCount={len(self.users)},TransactionCount={len(self.logs)},FaceCount=0"
		)

	def poll(self):
		info = f"Ver 6.60 Sim,{len(self.users)},{len(self.users)},{len(self.logs)},192.168.1.201,10,7,0,0,111"
		code, text = http("GET", self.url("getrequest"), {"SN": self.sn, "INFO": info})
		if code != 200:
			log(f"Heartbeat failed (HTTP {code}): {text[:120]}", "err")
			return
		text = text.strip()
		if text and text != "OK":
			for line in text.splitlines():
				self.run_command(line.strip())

	def run_command(self, line):
		if not line.startswith("C:"):
			return
		_, cmd_id, cmd = line.split(":", 2)
		log(f"Command from ERPNext #{cmd_id}: {cmd[:90]}", "cmd")
		ret, extra = 0, ""
		if cmd == "INFO":
			http("POST", self.url("cdata"), {"SN": self.sn, "table": "options"}, self.info_payload())
		elif cmd in ("CHECK",) or cmd.startswith("DATA QUERY ATTLOG"):
			with self.lock:
				lines = list(self.logs)
			if cmd.startswith("DATA QUERY"):
				try:
					params = dict(p.split("=", 1) for p in cmd.split(" ", 3)[3].split("\t"))
					lines = [ln for ln in lines if params["StartTime"] <= ln.split("\t")[1] <= params["EndTime"]]
				except Exception:
					pass
			if lines:
				self.upload(lines)
			ret = len(lines)
		elif cmd.startswith("DATA UPDATE USERINFO"):
			fields = dict(p.split("=", 1) for p in cmd[len("DATA UPDATE USERINFO ") :].split("\t") if "=" in p)
			self.users[fields.get("PIN")] = fields.get("Name", "")
			log(f"   user {fields.get('PIN')} '{fields.get('Name')}' saved on machine (enroll finger/face next)", "cmd")
			extra = "&CMD=DATA"
		elif cmd.startswith("DATA DELETE USERINFO"):
			pin = cmd.split("PIN=")[-1].strip()
			self.users.pop(pin, None)
			extra = "&CMD=DATA"
		elif cmd == "CLEAR LOG":
			with self.lock:
				self.logs.clear()
		elif cmd == "REBOOT":
			log("   rebooting ... (simulated)", "cmd")
			time.sleep(2)
			self.handshake()
		code, text = http("POST", self.url("devicecmd"), {"SN": self.sn}, f"ID={cmd_id}&Return={ret}{extra or '&CMD=' + cmd.split(' ')[0]}\n")
		log(f"   reported result of #{cmd_id}: Return={ret} -> {text.strip()[:40]}", "cmd")

	def heartbeat_loop(self, stop):
		while not stop.is_set():
			self.poll()
			stop.wait(self.heartbeat)


# --------------------------------------------------------------------------- Hikvision / webhook


def hikvision_punch(base, sn, user_id, ptype, when=None):
	when = when or now_str()
	event = {
		"ipAddress": "192.168.1.64",
		"portNo": 80,
		"protocol": "HTTP",
		"macAddress": "SI:MU:HI:KV:00:01",
		"channelID": 1,
		"dateTime": when.replace(" ", "T"),
		"activePostCount": 1,
		"eventType": "AccessControllerEvent",
		"eventState": "active",
		"eventDescription": "Access Controller Event",
		"AccessControllerEvent": {
			"deviceName": "Hikvision Simulator",
			"majorEventType": 5,
			"subEventType": 75,
			"employeeNoString": user_id,
			"currentVerifyMode": "cardOrFaceOrFp",
			"attendanceStatus": {"in": "checkIn", "out": "checkOut"}.get(ptype, ""),
			"mask": "no",
		},
	}
	# real terminals send multipart/form-data with a JSON part named event_log
	boundary = "MIME_boundary"
	body = (
		f"--{boundary}\r\nContent-Disposition: form-data; name=\"event_log\"\r\nContent-Type: application/json\r\n\r\n"
		f"{json.dumps(event)}\r\n--{boundary}--\r\n"
	)
	code, text = http(
		"POST",
		f"{base.rstrip('/')}/biometric/hikvision",
		{"sn": sn},
		body,
		{"Content-Type": f"multipart/form-data; boundary={boundary}"},
	)
	log(f"Hikvision event user {user_id} {ptype or ''} -> HTTP {code} {text[:200]}", "ok" if code == 200 and '"ok": true' in text.replace('":true', '": true') else "err")


def webhook_punch(base, token, sn, user_id, ptype, when=None):
	body = json.dumps({"device_id": sn, "user_id": user_id, "timestamp": when or now_str(), "type": ptype or ""})
	code, text = http(
		"POST",
		f"{base.rstrip('/')}/biometric/push",
		body=body,
		headers={"Content-Type": "application/json", "X-Biometric-Token": token or ""},
	)
	try:
		data = json.loads(text)
		item = (data.get("items") or [{}])[0]
		status = item.get("status") or data.get("error")
		log(f"Webhook user {user_id} {ptype or ''} -> HTTP {code}: {status} {item.get('employee_name') or ''} {item.get('log_type') or ''}", "ok" if data.get("ok") else "err")
	except ValueError:
		log(f"Webhook -> HTTP {code}: {text[:200]}", "err")


# --------------------------------------------------------------------------- main


def main():
	ap = argparse.ArgumentParser(description="Simulate a biometric punching machine", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
	ap.add_argument("--server", required=True, help="e.g. http://erp.yourcompany.com or http://1.2.3.4:8081")
	ap.add_argument("--token", default="", help="API token (webhook mode)")
	ap.add_argument("mode", choices=["adms", "hikvision", "webhook"], help="which kind of machine to imitate")
	ap.add_argument("--sn", default="SIM-K23-001", help="serial number / device id")
	ap.add_argument("--users", default="101,102,103", help="user ids for --auto")
	ap.add_argument("--punch", action="append", default=[], help="e.g. 101:in (repeatable)")
	ap.add_argument("--time", default=None, help="time for --punch, 'YYYY-MM-DD HH:MM:SS'")
	ap.add_argument("--auto", type=int, default=0, help="random punch every N seconds")
	ap.add_argument("--heartbeat", type=int, default=10, help="ADMS heartbeat seconds")
	ap.add_argument("--once", action="store_true", help="send and exit (no interactive prompt)")
	a = ap.parse_args()
	users = [u.strip() for u in a.users.split(",") if u.strip()]

	if a.mode == "adms":
		dev = AdmsDevice(a.server, a.sn, users, a.heartbeat)
		if not dev.handshake():
			sys.exit(1)

		def send(uid, ptype, when=None):
			dev.punch(uid, ptype, when)

	elif a.mode == "hikvision":

		def send(uid, ptype, when=None):
			hikvision_punch(a.server, a.sn, uid, ptype, when)

	else:
		if not a.token:
			log("webhook mode needs --token (Biometric Settings > API Token > Show API Token)", "err")
			sys.exit(1)

		def send(uid, ptype, when=None):
			webhook_punch(a.server, a.token, a.sn, uid, ptype, when)

	for p in a.punch:
		uid, ptype = parse_punch(p)
		if uid:
			send(uid, ptype, a.time)

	if a.once:
		if a.mode == "adms":
			dev.poll()
		return

	stop = threading.Event()
	if a.mode == "adms":
		threading.Thread(target=dev.heartbeat_loop, args=(stop,), daemon=True).start()
		log(f"Machine online. Heartbeat every {a.heartbeat}s - remote commands from ERPNext will show here.", "ok")
	if a.auto:

		def auto_loop():
			while not stop.is_set():
				stop.wait(a.auto)
				if not stop.is_set():
					send(random.choice(users), None)

		threading.Thread(target=auto_loop, daemon=True).start()
		log(f"Auto punch every {a.auto}s from users {', '.join(users)}")

	print("\nType a user id to punch, e.g.  101 in   /   102 out   /   103      (Ctrl+C to stop)\n")
	try:
		while True:
			line = input("punch> ").strip()
			if line.lower() in ("q", "quit", "exit"):
				break
			uid, ptype = parse_punch(line)
			if uid:
				send(uid, ptype)
	except (KeyboardInterrupt, EOFError):
		pass
	stop.set()
	log("Simulator stopped")


if __name__ == "__main__":
	main()
