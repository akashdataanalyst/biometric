#!/usr/bin/env python3
"""
LAN Sync Agent - for punching machines on the office network that have no
ADMS / cloud option (ZKTeco / eSSL protocol on TCP port 4370).

Runs on any office PC that stays on (Windows / Linux). Every minute it reads new
punches from each machine and pushes them to ERPNext over HTTPS.

    pip install pyzk requests
    python lan_sync_agent.py               # uses config.json next to this file
    python lan_sync_agent.py --test        # check machines + server, then exit

Windows: create a Task Scheduler task "At startup" running
    pythonw.exe C:\\biometric\\lan_sync_agent.py
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
STATE = os.path.join(HERE, "agent_state.json")
ENDPOINT = "/api/method/biometric_integration.api.push.punch"
BATCH = 500


def log(msg):
	line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
	print(line, flush=True)
	with open(os.path.join(HERE, "agent.log"), "a", encoding="utf-8") as f:
		f.write(line + "\n")


def load(path, default):
	try:
		with open(path, encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError):
		return default


def save_state(state):
	tmp = STATE + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(state, f, indent=2)
	os.replace(tmp, STATE)


class Server:
	def __init__(self, cfg):
		self.url = cfg["server_url"].rstrip("/") + ENDPOINT
		self.headers = {"X-Biometric-Token": cfg["token"], "Content-Type": "application/json"}

	def post(self, payload):
		r = requests.post(self.url, data=json.dumps(payload, default=str), headers=self.headers, timeout=60)
		try:
			data = r.json()
		except ValueError:
			raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
		data = data.get("message", data)
		if r.status_code != 200 or not data.get("ok"):
			raise RuntimeError(f"HTTP {r.status_code}: {data.get('error') or data}")
		return data

	def heartbeat(self, serial, info):
		return self.post({"device_id": serial, "heartbeat": True, "source": "LAN Agent", "info": info})

	def push(self, serial, punches):
		return self.post({"device_id": serial, "source": "LAN Agent", "punches": punches})


def read_machine(dev):
	from zk import ZK  # pyzk

	zk = ZK(dev["ip"], port=int(dev.get("port", 4370)), timeout=15, password=int(dev.get("password", 0) or 0), force_udp=bool(dev.get("udp")), ommit_ping=True)
	conn = zk.connect()
	try:
		info = {"device_ip": dev["ip"]}
		for key, fn in (("firmware", conn.get_firmware_version), ("model", conn.get_device_name), ("mac_address", conn.get_mac)):
			try:
				info[key] = fn()
			except Exception:
				pass
		try:
			conn.read_sizes()
			info.update({"user_count": conn.users, "fp_count": conn.fingers, "att_count": conn.records})
		except Exception:
			pass
		records = conn.get_attendance() or []
	finally:
		conn.disconnect()
	return info, records


def sync_device(server, dev, state):
	serial = dev["serial"]
	last = state.get(serial)
	info, records = read_machine(dev)
	server.heartbeat(serial, info)
	new = [r for r in records if not last or r.timestamp.strftime("%Y-%m-%d %H:%M:%S") > last]
	new.sort(key=lambda r: r.timestamp)
	sent = 0
	for i in range(0, len(new), BATCH):
		chunk = new[i : i + BATCH]
		punches = [
			{
				"user_id": str(r.user_id),
				"timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
				"status": r.punch,  # 0 check-in, 1 check-out, 2 break-out ...
				"verify": r.status,
			}
			for r in chunk
		]
		result = server.push(serial, punches)
		state[serial] = chunk[-1].timestamp.strftime("%Y-%m-%d %H:%M:%S")
		save_state(state)
		sent += len(chunk)
		log(f"{serial}: pushed {len(chunk)} punches (new {result.get('new')}, checkins {result.get('processed')})")
	if not new:
		log(f"{serial}: no new punches ({len(records)} on machine)")
	return sent


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--config", default=CONFIG)
	ap.add_argument("--test", action="store_true", help="check machines and server once, then exit")
	ap.add_argument("--once", action="store_true", help="sync once and exit")
	a = ap.parse_args()
	cfg = load(a.config, None)
	if not cfg:
		print(f"config.json not found or invalid: {a.config}")
		sys.exit(1)
	server = Server(cfg)
	state = load(STATE, {})
	interval = int(cfg.get("interval_seconds", 60))

	if a.test:
		ok = True
		for dev in cfg["devices"]:
			try:
				info, records = read_machine(dev)
				log(f"Machine {dev['serial']} @ {dev['ip']}: OK, {len(records)} punches, info {info}")
				server.heartbeat(dev["serial"], info)
				log(f"Server accepted heartbeat for {dev['serial']}")
			except Exception as e:
				ok = False
				log(f"Machine {dev.get('serial')} @ {dev.get('ip')}: FAILED - {e}")
		sys.exit(0 if ok else 1)

	log(f"LAN agent started: {len(cfg['devices'])} machine(s), every {interval}s -> {cfg['server_url']}")
	while True:
		for dev in cfg["devices"]:
			try:
				sync_device(server, dev, state)
			except Exception as e:
				log(f"{dev.get('serial')}: ERROR {e}")
		if a.once:
			break
		time.sleep(interval)


if __name__ == "__main__":
	main()
