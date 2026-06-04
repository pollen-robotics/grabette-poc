"""WiFi management helpers using nmcli.

Used by:
- hotspot_manager.py (runs as root) to create/activate the hotspot profile
- bluetooth_service.py (runs as root) to save home credentials after WIFI command
- app/routers/wifi.py (runs as rasp) to serve status and credentials to grabette-screen
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

HOTSPOT_CONN_NAME = "grabette-hotspot"
HOTSPOT_IFACE = "wlan0"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_active_wifi_connection() -> str | None:
    """Return the NetworkManager connection name active on wlan0, or None."""
    result = _run(["nmcli", "-t", "-f", "device,connection", "dev", "status"])
    for line in result.stdout.splitlines():
        if line.startswith(f"{HOTSPOT_IFACE}:"):
            conn = line[len(HOTSPOT_IFACE) + 1:]
            return conn if conn else None
    return None


def get_network_mode() -> str:
    """Return 'hotspot', 'connected', or 'offline'."""
    conn = get_active_wifi_connection()
    if conn is None:
        return "offline"
    if conn == HOTSPOT_CONN_NAME:
        return "hotspot"
    return "connected"


def get_current_ssid() -> str | None:
    """Return the SSID of the current WiFi connection, or None."""
    result = _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
    for line in result.stdout.splitlines():
        if line.startswith("yes:"):
            return line[4:] or None
    return None


def get_local_ip() -> str | None:
    """Return the WiFi interface's current IPv4 address, or None."""
    result = _run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", HOTSPOT_IFACE])
    for line in result.stdout.strip().splitlines():
        if "/" in line:
            return line.split("/")[0]
    return None


# ---------------------------------------------------------------------------
# Hotspot profile management
# ---------------------------------------------------------------------------

def ensure_hotspot_profile(ssid: str) -> bool:
    """Ensure the NM hotspot profile exists with the correct SSID. Returns True on success.

    Only deletes and recreates the profile if:
    - it doesn't exist yet, OR
    - the SSID has changed (e.g. robot_id was updated).
    Leaving an existing correct profile untouched avoids disrupting an active hotspot
    and prevents NM from auto-connecting to a remembered home network on profile deletion.
    """
    existing = {line.strip() for line in _run(["nmcli", "-t", "-f", "name", "con", "show"]).stdout.splitlines()}

    if HOTSPOT_CONN_NAME in existing:
        current_ssid = _run(
            ["nmcli", "-g", "802-11-wireless.ssid", "con", "show", HOTSPOT_CONN_NAME]
        ).stdout.strip()
        if current_ssid == ssid:
            logger.info("Hotspot profile '%s' already correct (SSID: %s)", HOTSPOT_CONN_NAME, ssid)
            return True
        # SSID mismatch (e.g. robot_id changed) → recreate
        logger.info("Hotspot SSID mismatch ('%s' != '%s') — recreating profile", current_ssid, ssid)
        _run(["nmcli", "connection", "delete", HOTSPOT_CONN_NAME])

    result = _run([
        "nmcli", "connection", "add",
        "type", "wifi",
        "ifname", HOTSPOT_IFACE,
        "con-name", HOTSPOT_CONN_NAME,
        "ssid", ssid,
        "802-11-wireless.mode", "ap",
        "ipv4.method", "shared",
        "ipv4.addresses", "192.168.42.1/24",
        "connection.autoconnect", "no",
    ])
    if result.returncode == 0:
        logger.info("Hotspot profile '%s' created (SSID: %s, open network)", HOTSPOT_CONN_NAME, ssid)
        return True
    logger.error("Failed to create hotspot profile: %s", result.stderr.strip())
    return False


def activate_hotspot() -> bool:
    """Bring up the grabette hotspot connection."""
    result = _run(["nmcli", "con", "up", HOTSPOT_CONN_NAME])
    if result.returncode == 0:
        logger.info("Hotspot activated")
        return True
    logger.error("Failed to activate hotspot: %s", result.stderr.strip())
    return False


def deactivate_hotspot() -> bool:
    """Bring down the grabette hotspot connection."""
    result = _run(["nmcli", "con", "down", HOTSPOT_CONN_NAME])
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Credential persistence
# ---------------------------------------------------------------------------

SCAN_CACHE_FILE = Path("/tmp/grabette_wifi_scan.json")


def prescan_and_cache() -> int:
    """Scan WiFi networks while wlan0 is in STA/offline mode and cache the results.

    Must be called by hotspot_manager (root) just *before* activating the hotspot,
    while the interface can still scan.  The API process (rasp user) cannot trigger
    scans (requires NET_ADMIN), so it reads this cache file in AP mode.

    Returns the number of networks found.
    """
    import os

    result = _run(
        ["nmcli", "--escape", "no", "-t", "-f", "SSID,SIGNAL",
         "dev", "wifi", "list", "--rescan", "yes"],
        timeout=15,
    )
    networks: list[dict] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        idx = line.rfind(":")
        if idx < 0:
            continue
        ssid = line[:idx].strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            signal = int(line[idx + 1:].strip())
        except ValueError:
            continue
        networks.append({"ssid": ssid, "signal": signal})
    networks.sort(key=lambda n: n["signal"], reverse=True)
    try:
        SCAN_CACHE_FILE.write_text(json.dumps(networks))
        os.chmod(SCAN_CACHE_FILE, 0o644)  # lisible par rasp
    except Exception as exc:
        logger.warning("Could not write scan cache: %s", exc)
    logger.info("Pre-scan: %d networks cached", len(networks))
    return len(networks)


def scan_networks() -> list[dict]:
    """Return visible WiFi networks sorted by signal, excluding the current connection.

    In hotspot (AP) mode wlan0 cannot scan (requires NET_ADMIN, held by NM/root).
    Reads the cache written by prescan_and_cache() — called by hotspot_manager as
    root just before activating the hotspot.  In STA mode, triggers a live scan.
    """
    own_ssid = get_current_ssid() or ""

    if get_network_mode() == "hotspot":
        try:
            data = json.loads(SCAN_CACHE_FILE.read_text())
            return [n for n in data if n.get("ssid") != own_ssid]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    result = _run(
        ["nmcli", "--escape", "no", "-t", "-f", "SSID,SIGNAL",
         "dev", "wifi", "list", "--rescan", "yes"],
        timeout=15,
    )
    networks: list[dict] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        idx = line.rfind(":")
        if idx < 0:
            continue
        ssid = line[:idx].strip()
        if not ssid or ssid == own_ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            signal = int(line[idx + 1:].strip())
        except ValueError:
            continue
        networks.append({"ssid": ssid, "signal": signal})
    return sorted(networks, key=lambda n: n["signal"], reverse=True)


def wifi_connect(ssid: str, password: str, credentials_file: Path) -> str:
    """Connect to a WiFi network, save credentials, return a status string."""
    try:
        result = _run(
            ["nmcli", "device", "wifi", "connect", ssid, "password", password],
            timeout=60,
        )
        if result.returncode == 0:
            save_home_credentials(ssid, password, credentials_file)
            return f"OK: Connecting to {ssid}"
        error = result.stderr.strip() or result.stdout.strip()
        return f"ERROR: {error}"
    except subprocess.TimeoutExpired:
        return "ERROR: Connection timed out"
    except Exception as exc:
        return f"ERROR: {exc}"


def save_home_credentials(ssid: str, password: str, credentials_file: Path) -> None:
    """Write home WiFi credentials to a JSON file readable by the API service."""
    credentials_file.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"ssid": ssid, "password": password})
    credentials_file.write_text(data)
    os.chmod(credentials_file, 0o644)
    logger.info("Home credentials saved for SSID '%s'", ssid)


def load_home_credentials(credentials_file: Path) -> dict | None:
    """Read saved home credentials. Returns None if file is missing or invalid."""
    try:
        return json.loads(credentials_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
