LAN SYNC AGENT  -  for machines on the office network (TCP port 4370)
=====================================================================

Use this only for machines that do NOT have an ADMS / Cloud Server menu.
(eSSL K23 / K28 / X990 etc. have ADMS - use that instead, no agent needed.)

1. Copy this folder to an office PC that stays on (C:\biometric on Windows).
2. Install Python 3 (python.org, tick "Add to PATH"), then in a terminal:
       pip install pyzk requests
3. Open config.json and check each machine's "ip" (machine Menu > COMM > Ethernet)
   and "serial" (must match the Biometric Device in ERPNext; a new serial is
   registered automatically). "password" is the machine's COMM key (usually 0).
4. Test:      python lan_sync_agent.py --test
5. Run:       python lan_sync_agent.py
   Windows auto-start: Task Scheduler > Create Task > Trigger "At startup" >
   Action: pythonw.exe  Arguments: C:\biometric\lan_sync_agent.py
   Linux: add a systemd service or cron @reboot.

The agent remembers the last punch it sent (agent_state.json) and the server
ignores repeats, so restarting or re-running is always safe. Log: agent.log
