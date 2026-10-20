"""Protocol adapters.

Every punching machine speaks some protocol. An adapter turns that protocol's
raw payload into a list of `RawPunch` objects. The engine never cares which
machine a punch came from - it only sees RawPunch.

Supported out of the box:
    adms        eSSL / ZKTeco / Identix and other "ADMS / Push / iclock" devices
    hikvision   Hikvision face / finger terminals (ISAPI "HTTP Listening")
    generic     Any device, vendor software or middleware that can send JSON
    file        USB export (.dat attlog), CSV or Excel files from any machine
    (LAN agent) ZK protocol devices on a local network, pulled by tools/lan_sync_agent.py
"""

from biometric_integration.adapters.base import RawPunch  # noqa: F401
