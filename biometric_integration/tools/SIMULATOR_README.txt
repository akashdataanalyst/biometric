BIOMETRIC DEVICE SIMULATOR  -  test the whole setup without a machine
=====================================================================

Needs only Python 3 (no packages). Run it from ANY computer, ideally one outside
your office network, to prove the internet -> AWS -> ERPNext path works.

1) Pretend to be an eSSL / ZKTeco machine (K23, K28 ...):

   python device_simulator.py --server {{MACHINE_BASE}} adms --sn SIM-K23-001 --users 101,102

   - connects exactly like the real machine (handshake + heartbeat every 10 s)
   - type "101 in" or "102 out" and press Enter to punch
   - commands you send from ERPNext (Biometric Device > Machine Commands) show up here

2) One-shot test (good for checking quickly):

   python device_simulator.py --server {{MACHINE_BASE}} adms --sn SIM-K23-001 --punch 101:in --once

3) Hikvision face terminal:

   python device_simulator.py --server {{MACHINE_BASE}} hikvision --sn SIM-HIK-1 --punch 101:in --once

4) Any other machine / software through the webhook (token from Biometric Settings):

   python device_simulator.py --server {{HTTPS_BASE}} --token YOUR_TOKEN webhook --sn GATE-1 --punch 101:in --once

5) Busy office: a random punch every 20 seconds

   python device_simulator.py --server {{MACHINE_BASE}} adms --sn SIM-K23-001 --users 101,102,103,104 --auto 20

Then open ERPNext > Biometric Live Monitor and watch the punches arrive.

Note: a new serial number appears in ERPNext as "Pending Approval" (unless
auto-approve is on). Approve it once and its punches are processed.
Delete test data from Biometric Simulator > Delete All Test Data (only for
devices marked "Virtual / Test Device") or delete the device's punch logs.

Windows: use "py" instead of "python" if "python" is not found.
