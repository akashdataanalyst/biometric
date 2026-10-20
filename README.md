# Biometric Integration for ERPNext HRMS

One app for **any punching machine**: eSSL / ZKTeco (K21, K23, K28, K30, K90, X990, MB160, uFace, iFace …), Identix, Hikvision face terminals, and any other machine or vendor software through a webhook, a LAN agent or a file import. Punches arrive live as **Employee Checkin**, and HRMS Auto Attendance turns them into **Attendance**.

Includes a **built-in simulator** so you can test the full flow **without a machine**.

Tested on Frappe 16.52 + ERPNext 16.51 + HRMS 16.51 (`version-16`). Version 1.0.0 was written for v15; the v16 port changed only the version pin, `/app/` → `/desk/` links, and added the desk sidebar and the apps-screen tile **Biometric**.

---

## How it works

```
Machine ──(ADMS / Hikvision / Webhook / LAN agent / File)──► Adapter ──► Biometric Punch Log
                                                                          │  de-duplicate
                                                                          ▼
                                                     Employee.attendance_device_id lookup
                                                                          │
                                                                          ▼
                                                     Employee Checkin (IN / OUT)  ──► Live Monitor
                                                                          │
                                                                          ▼
                                                     HRMS Shift Type Auto Attendance ──► Attendance
```

| Connection type | Machines | How they connect |
|---|---|---|
| **ADMS (eSSL / ZKTeco Push)** | eSSL K21/K23/K28/K30/K90/X990/MB160/uFace/iFace, ZKTeco, Identix and other ZK‑firmware machines with a *Cloud Server* / *ADMS* menu | The machine calls the server over the internet. No office PC, no port forwarding at the office. |
| **Hikvision ISAPI** | Hikvision DS‑K1T series face / finger terminals | Terminal's *HTTP Listening* posts every event. |
| **Generic Webhook** | Anything that can POST JSON: Matrix COSEC, Suprema BioStar, Realtime, Mantra middleware, vendor cloud, your own script | `POST /biometric/push` with header `X-Biometric-Token`. |
| **LAN Agent (ZK Pull)** | Older ZK‑protocol machines with no ADMS option (TCP 4370) | Small agent on an office PC reads the machine and pushes to the server. |
| **File Import** | Every machine | Upload the USB file (`1_attlog.dat`), CSV or Excel. |

---

## Install (once, by the person who manages the server)

On the AWS server, as the bench user:

```bash
cd ~/frappe-bench
bench get-app https://github.com/<your-org>/biometric_integration   # or copy this folder into apps/
bench --site <your-site> install-app biometric_integration
bench --site <your-site> migrate
bench build --app biometric_integration
sudo supervisorctl restart all          # or: bench restart
bench --site <your-site> enable-scheduler
```

Then open the port for the machines (next section).

### Docker (frappe_docker)

The app is a separate app in the image (`apps/biometric_integration`), next to erpnext, hrms and
grid_interview. After a new image is running:

```bash
# stop scheduler / queue workers during install (avoids MariaDB deadlocks on install)
bench --site <your-site> install-app biometric_integration
```

The frontend container (nginx) already passes `/iclock/*` and `/biometric/*` to Frappe, so the
machines can use the published HTTP port directly (for example `http://<server>:8080`). Check:
`curl "http://<server>:8080/iclock/cdata?SN=__PING__"` → `OK PING biometric_integration`. With
`FRAPPE_SITE_NAME_HEADER` set, every host name reaches the site, which is what machines calling
by IP need.

### AWS + nginx: let the machines in

Most eSSL / ZKTeco machines speak **plain HTTP only**. If your site redirects HTTP → HTTPS, the machine cannot follow the redirect. Pick one option:

**Option A – separate port for machines (recommended).** Copy `deploy/nginx-biometric.conf` to `/etc/nginx/conf.d/`, put your site name in it, then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

In the **AWS Security Group** add an inbound rule: *Custom TCP, port 8081, source 0.0.0.0/0* (or only your office public IPs).
In **Biometric Settings** set *Port for Machines* = `8081`.

**Option B – port 80.** Add the `location ~ ^/(iclock|biometric)/` block from the same file inside your existing `listen 80` server block, **above** the redirect to HTTPS.

Hikvision terminals and the LAN agent / webhook can use HTTPS on 443 normally.

Check it from your laptop:

```bash
curl "http://erp.yourcompany.com:8081/iclock/cdata?SN=__PING__"
# -> OK PING biometric_integration
```

---

## 10‑minute setup per machine (all from the ERPNext UI)

1. **Biometric Settings** → set *Public Server Address* (e.g. `erp.yourcompany.com`) and *Port for Machines*. Click **Run Self‑Test**.
2. **Employees** → the machine user ID (PIN) must match the employee. By default the app tries *Attendance Device ID*, then *Employee Number* (Emp Code), then *Employee ID*. So if the PIN on the machine is the same as the Emp Code, nothing extra is needed. Change this in *Biometric Settings → Match Employee By* (you can also point it to your own custom emp code field).
3. **On the machine** (eSSL / ZKTeco): *Menu → COMM. → Cloud Server Setting*
   - Server Address: your domain or IP, Server Port: 80 / 8081, Enable Domain Name: ON, Proxy: OFF
   - Check *COMM. → Ethernet* has a correct Gateway and DNS.
4. Within a minute the machine appears in **Biometric Device** (*Pending Approval*). Open it and click **Approve**. Punches that arrived before approval are processed automatically.
5. **Shift Type** → tick *Enable Auto Attendance* and set *Process Attendance After*. The app keeps *Last Sync of Checkin* up to date for you.
6. Punch on the machine and watch it in **Biometric Live Monitor**.

Every Biometric Device page shows its own step‑by‑step instructions with your server address already filled in.

Optional: on the device page, **Machine Commands → Push Employees to Machine** sends all employees' IDs and names to the machine, so staff only need to enroll finger / face.

---

## Test without a machine

### Inside ERPNext: Biometric Simulator page
*Biometric sidebar → Tools → Simulator* (`/desk/biometric-simulator`)

1. Click a machine type (eSSL, Hikvision, Webhook, LAN Agent). A **virtual device** is created that speaks the exact same protocol as the real machine.
2. **Auto‑assign test IDs** gives employees without an ID a test ID (101, 102 …).
3. **Send Punch** / **Send Unknown ID** / **Simulate a Full Day**. Each result shows the raw request the machine would send and the server's reply.
4. Tick **Send over the network** to send the punch through your public address, which tests DNS, the AWS port and nginx too.
5. **Delete All Test Data** removes test punches, their checkins and the virtual devices.

### From any PC: `device_simulator.py`
*Biometric Settings → Download → Device Simulator*. Needs only Python 3.

```bash
# behaves like an eSSL K23: handshake, heartbeat, punches, remote commands
python device_simulator.py --server http://erp.yourcompany.com:8081 adms --sn SIM-K23-001 --users 101,102
punch> 101 in
punch> 102 out
```

Run it from outside your office network to prove the internet → AWS → ERPNext path works before the real machine arrives.

---

## Settings that matter

| Setting | Default | Why |
|---|---|---|
| Match Employee By | Device ID, Emp Code or Employee ID | Which Employee field the machine PIN is matched to. Use *Employee Number (Emp Code)* to match only on Emp Code, or *Custom Employee Field* for your own field. |
| Decide IN / OUT | Alternate IN / OUT | Many machines send every punch as "Check‑In" unless staff press a key. Alternate is the safe default. Use *Device Punch State* if your staff do press IN/OUT keys. Per machine you can force *Always IN* (entry gate) / *Always OUT* (exit gate). |
| Ignore Repeat Punch Within | 60 s | Double punches become *Duplicate* instead of extra checkins. |
| Auto‑register / Auto‑approve | On / Off | New machines appear by themselves but wait for approval, so a random device can't create checkins. |
| Ignore Punches Before | blank | A newly connected machine may upload years of history; set a date to keep it out of attendance. |
| Treat 00101 and 101 as the same | On | Some machines pad IDs with zeros. |
| Update Last Sync of Checkin | On | HRMS Auto Attendance only processes checkins up to this time. Set to the moment the slowest live machine last reported (machines silent for 24 h+ are ignored). |

---

## Webhook format (for any machine / software)

```http
POST https://erp.yourcompany.com/biometric/push
X-Biometric-Token: <API Token from Biometric Settings>
Content-Type: application/json

{"device_id": "GATE-1", "user_id": "101", "timestamp": "2026-10-18 09:01:00", "type": "IN"}
```

Also accepted: a list of punches, `{"device_id": "...", "punches": [...]}`, ISO timestamps with time zone, epoch seconds/milliseconds, and common field names (`emp_code`, `pin`, `employee_no`, `punch_time`, `log_time`, `status`, `attendanceStatus` …). Unusual names can be mapped in *Biometric Settings → Webhook Field Mapping*.

Alternative URL: `/api/method/biometric_integration.api.push.punch`.

---

## What's in the app

| Item | Purpose |
|---|---|
| Biometric Settings | Server address, token, processing rules, self‑test |
| Biometric Device | One record per machine: status, online/offline, setup instructions, remote commands |
| Biometric Punch Log | Every raw punch with its result: Processed, Unmapped, Duplicate, Error … Re‑processable |
| Biometric Device Command | Queue for ADMS remote commands (device info, re‑upload logs, fetch date range, push/delete user, reboot) |
| Biometric Log Import | Import USB / CSV / Excel files |
| Biometric Live Monitor (page) | Live feed, machines online, unmapped IDs with one‑click mapping |
| Biometric Simulator (page) | Test everything without a machine |
| Biometric Daily Punch Report | First in, last out, hours, missing punches |
| `tools/device_simulator.py` | Stand‑alone machine simulator for any PC |
| `tools/lan_sync_agent.py` | Agent for LAN‑only ZK machines |

---

How each DocType works, its statuses, the code behind it and the test entries on the site:
[docs/biometric-doctypes.md](docs/biometric-doctypes.md) (Hinglish).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Machine never shows up | Self‑Test → "Machines can reach …". Check AWS Security Group port, DNS of the domain, nginx block, and machine Gateway/DNS. Try `device_simulator.py` from a laptop. |
| Machine shows *Pending Approval* | Open it and click **Approve**. |
| Punches are *Unmapped* | The machine user ID doesn't match any Employee's Device ID / Emp Code. Map it from the Live Monitor; waiting punches are processed at once. |
| Checkins but no Attendance | Enable *Auto Attendance* on the Shift Type, assign shifts, keep the scheduler on. |
| Wrong time on punches | Fix the machine's clock, or use *Clock Correction (minutes)* on the device. |
| "Latitude and longitude values are required" | HR Settings has geolocation tracking on. Fill Latitude/Longitude on the Biometric Device. |
| Old machine without ADMS | Use the LAN Agent or File Import. |

---

## Hinglish quick guide

- App ek baar server par install hoga (upar *Install* section).
- Uske baad har machine: Settings mein server address → machine par *Cloud Server Setting* mein wahi address/port → machine ERPNext mein khud aa jayegi → **Approve** → done.
- Punch Emp Code se match hota hai: machine par jo user ID (PIN) hai wahi Employee ka Emp Code (Employee Number) ho, bas. Settings → *Match Employee By* se badal sakte ho.
- Machine nahi hai? **Biometric Simulator** page kholo, virtual eSSL K23 banao, punch bhejo, Live Monitor mein dekho. Bahar se test karna ho to `device_simulator.py` chalao.
- Test data **Delete All Test Data** se hat jaata hai.
