# Biometric Integration: DocTypes kaise kaam karte hain

App ke **5 DocTypes** hain. Ye file batati hai ki har ek kya hai, kaun / kab banata hai, uske
status ka matlab, kaunsa code use karta hai, aur ek punch machine se **Employee Checkin** tak kaise
pahunchta hai. Aakhir mein `griderp.in` par bani **test entries** ki list hai, jinhe khol kar dekh
sakte hain.

## Ek nazar mein

| DocType | Type | Ek line mein | Kaun banata hai |
|---|---|---|---|
| **Biometric Settings** | single | poori app ke niyam: matching, IN/OUT, duplicate, token, port | install par default; HR / System Manager badalta hai |
| **Biometric Device** | normal | ek machine = ek record (serial no se) | machine pehli baar connect hote hi khud (auto-register), ya HR haath se |
| **Biometric Punch Log** | normal | machine se aaya **har** punch, kachcha (raw) + uska nateeja | engine, har punch par apne aap |
| **Biometric Device Command** | normal | ERPNext se machine ko bheja gaya hukm (info, logs, user push, reboot …) | HR, device form ke *Machine Commands* button se |
| **Biometric Log Import** | normal | USB / CSV / Excel file se punches upload | HR, haath se |

Asli attendance HRMS ke DocTypes mein banti hai: **Employee Checkin** (har sahi punch ka ek) aur
**Attendance** (Shift Type ki Auto Attendance se). Ye app sirf Checkin tak le jaati hai.

## Poora safar: machine → Checkin

```
 Machine (eSSL/ZKTeco, Hikvision, webhook, LAN agent)       USB / CSV / Excel file
        │ /iclock/cdata, /biometric/hikvision, /biometric/push        │
        ▼                                                             ▼
 route_renderer.py → api/adms.py | api/push.py            Biometric Log Import → start_import
        │                                                             │
        └──────────────► engine.resolve_device ◄──────────────────────┘
                          │  Biometric Device mila?  nahi → auto-register (Pending Approval)
                          ▼
                     engine.ingest  ── har punch ──► Biometric Punch Log (naam = hash: device+user+time,
                          │                          isliye wahi punch dobara aaye to "already received")
                          ▼
                     engine.process_log
                          ├─ device Pending Approval?  → "Device Not Approved" (approve par apne aap dobara)
                          ├─ device Disabled / bahut purana / future time → "Ignored" / "Error"
                          ├─ machine ID kisi Employee se nahi mila → "Unmapped"
                          │     (Employee par ID set karte hi apne aap dobara: engine.on_employee_update)
                          ├─ 60 s ke andar dobara punch → "Duplicate"
                          ├─ IN / OUT tay karo (Settings + device ki Punch Direction)
                          └─ Employee Checkin banao → "Processed"  ──► Live Monitor (realtime)
                                                                         │
                          HRMS Shift Type (Auto Attendance) ◄────────────┘ → Attendance

 Har 5 min (tasks.py): jo machine 10 min se chup hai → Offline; Shift Type ka
 "Last Sync of Checkin" aage badhao (taaki HRMS Attendance bana sake).
 Roz (tasks.py): Settings › Keep Logs Days se purane Processed / Duplicate logs hatao.
```

Commands ulti disha mein chalte hain:

```
 HR: Device form → Machine Commands → Biometric Device Command (Pending)
        ▼  machine har 10 s /iclock/getrequest poochti hai (heartbeat)
 api/adms.pending_commands → command machine ko diya (Sent)
        ▼  machine command chala kar /iclock/devicecmd par jawab bhejti hai
 api/adms._complete_command → Success / Failed (+ return code, response)
```

Isliye command sirf **asli ya chalti hui machine** (ya `device_simulator.py`) par poora hota hai,
aur machine band ho to *Pending* rehta hai; chalu hote hi chala jaata hai.
Simulator page ki virtual machine khud poll nahi karti, to uske commands *Pending* reh jaate hain.

## 1. Biometric Settings (single)

**Kya:** poori app ke niyam. Install par default values aur ek API token ban jaate hain
(`install.py:after_install`).

| Group | Field | Matlab |
|---|---|---|
| Server & Machines | Enable Biometric Integration, Public Server Address, Port for Machines, Device Time Zone (hours) | machine kis address / port par aaye; *Port* sirf instructions mein dikhta hai (abhi 80; is local Docker setup par **8080** karein) |
| Naye machines | Auto-register New Machines, Auto-approve New Machines | anjaan machine khud aa jaaye (default haan) par approval maange (default haan) |
| API Token | API Token | webhook / LAN agent ke liye `X-Biometric-Token` |
| Punch Processing | Match Employee By, Custom Employee Field, Treat 00101 and 101 as the Same ID | machine ka user ID / PIN kis Employee field se milaayein (default: Attendance Device ID → Emp Code → Employee ID) |
| | Decide IN / OUT, Alternate Window (hours) | default *Alternate*: pichhla IN tha to ab OUT |
| | Ignore Repeat Punch Within (seconds), Ignore Punches Before, Reject Punches More Than N Minutes in the Future | duplicate, purana itihaas, galat ghadi |
| | Update 'Last Sync of Checkin' on Shift Types | HRMS Auto Attendance ke liye |
| Monitoring & Cleanup | Mark Machine Offline After (minutes), Delete Processed Raw Logs After (days) | Offline status, purane logs |
| Webhook Field Mapping | User ID / Time / IN/OUT / Device Field | ajeeb field naam wale JSON ke liye mapping |

**Code:** `engine.get_settings` se har jagah padhi jaati hai; form par *Run Self-Test*
(`api/simulator.run_self_test`) aur setup instructions (`api/setup_info.py`).

## 2. Biometric Device

**Kya:** ek machine. Naam = **Serial No** (eSSL ka SN, Hikvision ka MAC / sn, webhook ka `device_id`).

| Field | Matlab |
|---|---|
| Protocol | ADMS (eSSL / ZKTeco Push), Hikvision ISAPI, Generic Webhook, LAN Agent (ZK Pull), File Import |
| Status | **Pending Approval** (nayi machine, punches rukte hain) → **Active** (punches chalte hain) / **Disabled** |
| Online Status | Never Connected / Online / Offline (10 min chup = Offline) |
| Is Virtual | Simulator wali machine; iske punches **test** maane jaate hain (`is_test = 1`) |
| Punch Direction | As per Settings / Always IN (entry gate) / Always OUT (exit gate) |
| Clock Correction (min) | machine ki ghadi galat ho to |
| Latitude / Longitude | HR Settings mein geolocation chalu ho to Checkin ke liye zaroori |
| Device Token | sirf is machine ka webhook token (optional) |
| Firmware, User / FP / Face / Att count, IP, MAC | machine khud batati hai (`touch_device`) |
| Total Punches, Last Punch On, Last Heartbeat | engine bharta hai |

**Kaun banata hai:** machine ka pehla request → `engine.resolve_device` (auto-register). Ya HR haath
se, ya Simulator page (`create_virtual_device`).

**Approve karne par:** `biometric_device.py:on_update` us machine ke ruke hue punches
("Device Not Approved") ko dobara process karta hai.

**Form buttons:** Approve, Machine Commands (`send_command`, *Push Employees to Machine* =
`push_employees`), View → Punch Logs / Live Monitor / Command History, aur machine par kya
set karna hai uski step-by-step instructions.

## 3. Biometric Punch Log

**Kya:** har aaya hua punch, jaisa aaya waisa (`raw_data`), aur uska nateeja. Naam
`BPL-<hash>` = device + user ID + time, isliye ek punch do baar save nahi hota.

| Status | Kab | Aage kya |
|---|---|---|
| **Processed** | Employee mila, Checkin bana | `employee_checkin` link dekhein |
| **Unmapped** | machine ID kisi Employee se nahi mila | Employee par *Attendance Device ID* / Emp Code set karein → apne aap Processed. Ya Live Monitor se ek click mapping |
| **Duplicate** | 60 s ke andar wahi employee dobara (ya Checkin pehle se hai) | kuch nahi; pehle wale Checkin ka link rakha jaata hai |
| **Device Not Approved** | machine abhi Pending Approval | machine Approve karein → apne aap Processed |
| **Ignored** | machine Disabled, ya *Ignore Punches Before* se purana | — |
| **Error** | future time, employee Inactive, HRMS ne Checkin mana kiya | `message` padhein, theek karke *Reprocess* |
| **Pending** | abhi process ho raha hai | — |
| **Dry Run** | self-test; Checkin nahi bana | — |

Baaki fields: `device`, `user_id`, `punch_time`, `log_type` (IN / OUT), `employee`,
`employee_name`, `shift`, `source` (ADMS / Hikvision / Webhook / LAN Agent / File Import /
Simulator), `is_test`, `device_punch_type`, `status_code`, `verify_mode`, `received_on`.

**Code:** `engine.ingest` banata hai, `engine.process_log` faisla karta hai, `engine.reprocess`
list ke *Reprocess* / *Reprocess All Unmapped / Errors* buttons, `tasks.daily` purane hataata hai, Live Monitor aur Daily Punch Report
yahin se padhte hain.

## 4. Biometric Device Command

**Kya:** ADMS (eSSL / ZKTeco) machine ko ERPNext se bheja gaya ek hukm. Sirf ADMS machines par.

| Command Type | Machine kya karti hai |
|---|---|
| Fetch Device Info | firmware, users, fingerprints, logs ki ginti bhejti hai |
| Re-upload All Logs | saare stored punches dobara bhejti hai (jo pehle aa chuke wo "already received") |
| Fetch Logs for Date Range | sirf From–To ke punches |
| Push User to Device | Employee ka machine ID + naam machine mein save (phir finger / face enroll) |
| Delete User from Device | user hatao |
| Reboot, Clear Device Logs, Custom | jaisa naam |

Status: **Pending** (bana) → **Sent** (machine ke heartbeat par diya) → **Success** / **Failed**
(machine ka jawab, `return_code`, `response`) ya **Cancelled**. `cmd_id` naam ka number hai;
`command_text` wahi line jo machine ko jaati hai.

**Code:** `biometric_device_command.py` (text banana, Cancel / Retry), `api/adms.pending_commands`
(machine ko dena), `api/adms._complete_command` (jawab save).

## 5. Biometric Log Import

**Kya:** machine internet se na jude to file se punches: eSSL USB `1_attlog.dat`, eTimeTrack /
BioStar / COSEC ka CSV, ya Excel. Columns apne aap pehchaane jaate hain (Emp Code, User ID, PIN,
Punch Time, Date + Time …); na pehchaane to *Column Mapping* mein naam likhein.

| Field | Matlab |
|---|---|
| Device | kis machine ke naam se punches daalein (File Import protocol ka device bana lein) |
| File | file (attach pehle, phir save) |
| Status | Not Started → **Success** / **Partial Success** (kuch rows galat ya unmapped) / **Failed** |
| Rows Read, New Punches, Already Received, Checkins Created, Unmapped, Rows with Errors | *Start Import* ka hisaab |
| Column Mapping | User ID / Date-Time / Date / IN/OUT column, Date Format (khaali = auto) |
| Import Log | galat rows aur wajah |

**Code:** `biometric_log_import.py:start_import` → `adapters/file_import.parse_file` →
`engine.ingest` (baaki sab punch jaisa hi). Wahi file dobara import karein to punches
"already received" hote hain, double nahi.

## Test entries (`griderp.in`, 2026-10-18)

Sab **TEST** hain: machine IDs 9101, 9102, 9999 aur virtual devices. Desk → **Biometric** sidebar
se kholein.

| DocType | Entry | Kya dikhata hai |
|---|---|---|
| Employee | HR-EMP-00001 *TEST Bio Asha* (ID 9101), HR-EMP-00002 *TEST Bio Ravi* (ID 9102) | Attendance Device ID se matching |
| Biometric Device | **SIM-ESSL-K23** (aapka banaya, virtual eSSL K23) | `device_simulator.py` ne isi naam se nginx :8080 ke raaste asli eSSL ki tarah handshake, heartbeat, punches kiye; Online, firmware "Ver 6.60 Sim", 4 punches |
| Biometric Device | **TEST-USB-FILE** (virtual, File Import) | file import wali machine |
| Biometric Punch Log | 9101 09:02 IN → **Processed** | EMP-CKIN-10-2026-000001 |
| | 9102 09:02 IN → **Processed** | EMP-CKIN-10-2026-000002 |
| | 9101 09:02:20 → **Duplicate** | 20 s baad dobara punch, naya Checkin nahi |
| | 9999 09:05 → **Unmapped** | koi Employee nahi; kisi Employee ko ID 9999 dein to apne aap Processed |
| | 9101 13:30 OUT, 9102 13:32 OUT (file se) → **Processed** | EMP-CKIN-10-2026-000003 / 000004, source File Import |
| Biometric Device Command | **BDC-00001** Fetch Device Info → **Success** | aapka pending command; machine ke heartbeat par chala |
| | **BDC-00002** Push User to Device (9101, TEST Bio Asha) → **Success** | machine ne user save kiya |
| Biometric Log Import | **BLI-00001** → **Partial Success** | 3 rows: 2 processed, 1 galat ("not-a-date") Import Log mein |
| Employee Checkin | 4 entries (2 IN, 2 OUT) | HRMS mein asli attendance input |

Attendance nahi bani, kyunki abhi koi Shift Type *Auto Attendance* ke saath nahi hai.

**Hatane ke liye:** Biometric Simulator page → **Delete All Test Data** (test punches, unke
Checkins aur virtual devices hata deta hai). Phir dono TEST employees aur BDC-00001 / 00002,
BLI-00001 haath se delete karein.

## Khud test karna

- **Simulator page** (`/desk/biometric-simulator`): machine type chunein, *Send Punch*, *Simulate a
  Full Day*, nateeja aur raw request dekhein.
- **device_simulator.py** (Settings → Download): kisi bhi PC se asli machine ki tarah:

```bash
python device_simulator.py --server http://<server>:8080 adms --sn SIM-ESSL-K23
punch> 9101 in
punch> 9102 out
```

Machine chalu rehte hue Device form se *Machine Commands* bhejein; 10 s mein simulator ki screen
par command aur ERPNext mein Success dikhega.
