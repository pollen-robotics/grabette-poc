"""WiFi status, configuration, and setup page endpoints.

GET  /api/wifi/status      → mode + SSID courant
GET  /api/wifi/credentials → SSID + password du réseau home (subnet hotspot uniquement)
GET  /api/wifi/scan        → liste des réseaux visibles
POST /api/wifi/connect     → connecte grabette au réseau choisi (async, retourne 202)
GET  /api/wifi/setup       → page HTML de configuration (navigateur sur hotspot)
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from grabette.config import settings
from grabette.wifi import (
    deactivate_hotspot,
    get_current_ssid,
    get_local_ip,
    get_network_mode,
    load_home_credentials,
    save_home_credentials,
    scan_networks,
    wifi_connect,
)

router = APIRouter(prefix="/api/wifi", tags=["wifi"])

_HOTSPOT_SUBNET = "192.168.42."


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class WifiStatus(BaseModel):
    mode: str  # "hotspot" | "connected" | "offline"
    ssid: str | None
    ip: str | None = None


class WifiCredentials(BaseModel):
    ssid: str
    password: str


class ConnectRequest(BaseModel):
    ssid: str
    password: str


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=WifiStatus)
def wifi_status() -> WifiStatus:
    return WifiStatus(mode=get_network_mode(), ssid=get_current_ssid(), ip=get_local_ip())


@router.get("/credentials", response_model=WifiCredentials)
def wifi_credentials(request: Request) -> WifiCredentials:
    client_ip = request.client.host if request.client else ""
    if not client_ip.startswith(_HOTSPOT_SUBNET):
        raise HTTPException(
            status_code=403,
            detail="Credentials endpoint is only accessible from the grabette hotspot network",
        )
    creds = load_home_credentials(settings.hotspot_credentials_file)
    if creds is None:
        raise HTTPException(status_code=404, detail="No home network configured yet")
    return WifiCredentials(**creds)


@router.get("/scan")
def wifi_scan() -> list[dict]:
    """Scan and return visible networks sorted by signal strength."""
    return scan_networks()


# Résultat de la dernière tentative de connexion — lu par /api/wifi/connect-result
_last_connect: dict = {"status": "idle", "message": ""}


def _do_connect(ssid: str, password: str) -> None:
    import time
    global _last_connect
    _last_connect = {"status": "connecting", "message": f"Connecting to {ssid}…"}

    # 1. Save credentials BEFORE connecting so grabette-screen can fetch them
    #    while the hotspot is still active.
    save_home_credentials(ssid, password, settings.hotspot_credentials_file)

    # 2. Give grabette-screen time to fetch the credentials (it polls every 1.5 s).
    time.sleep(3)

    # 3. Explicitly deactivate the hotspot before connecting.
    #    Letting nmcli handle the AP→STA transition implicitly is slow and
    #    can exceed the connection timeout.  Deactivating first is faster.
    deactivate_hotspot()
    time.sleep(1)  # let wlan0 settle in managed mode before connecting

    result = wifi_connect(ssid, password, settings.hotspot_credentials_file)
    if result.startswith("OK:"):
        _last_connect = {"status": "ok", "message": result}
    else:
        # Connection failed — remove the pre-saved credentials
        try:
            settings.hotspot_credentials_file.unlink(missing_ok=True)
        except Exception:
            pass
        _last_connect = {"status": "error", "message": result}


@router.post("/connect", status_code=202)
def wifi_connect_endpoint(req: ConnectRequest, background_tasks: BackgroundTasks):
    """Connect grabette to the given network. Returns 202 immediately; connection runs in background."""
    global _last_connect
    _last_connect = {"status": "connecting", "message": f"Connecting to {req.ssid}…"}
    background_tasks.add_task(_do_connect, req.ssid, req.password)
    return {"status": "connecting", "ssid": req.ssid}


@router.get("/connect-result")
def wifi_connect_result() -> dict:
    """Return the result of the last connection attempt."""
    return _last_connect


# ---------------------------------------------------------------------------
# Web setup page
# ---------------------------------------------------------------------------

@router.get("/setup", response_class=HTMLResponse)
def wifi_setup_page() -> str:
    return _WIFI_SETUP_HTML


_WIFI_SETUP_HTML = """\
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grabette — WiFi Setup</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #111; color: #eee; font-family: sans-serif; padding: 20px; max-width: 480px; margin: auto; }
  h1 { color: #0ff; font-size: 1.3rem; margin-bottom: 16px; }
  #status { font-size: .85rem; color: #aaa; margin-bottom: 12px; min-height: 1.2em; }
  #status.ok  { color: #0f0; }
  #status.err { color: #f44; }
  #networks { list-style: none; margin-bottom: 16px; }
  #networks li {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 12px; margin-bottom: 4px; border-radius: 6px;
    background: #1e1e1e; cursor: pointer; border: 1px solid #333;
  }
  #networks li:hover { background: #2a2a2a; border-color: #0ff; }
  #networks li.selected { background: #003333; border-color: #0ff; }
  .signal { font-size: .75rem; color: #888; }
  #form { display: none; background: #1e1e1e; border-radius: 8px; padding: 16px; margin-bottom: 12px; }
  #form label { display: block; margin-bottom: 6px; color: #0ff; font-size: .9rem; }
  .pw-row { display: flex; gap: 8px; margin-bottom: 12px; }
  .pw-row input {
    flex: 1; padding: 8px 10px; border-radius: 4px;
    border: 1px solid #444; background: #111; color: #eee; font-size: 1rem;
  }
  .pw-row .toggle {
    padding: 8px 14px; background: #333; border: 1px solid #444;
    border-radius: 4px; color: #ccc; font-size: .85rem; cursor: pointer; white-space: nowrap;
  }
  .pw-row .toggle:hover { background: #444; }
  #error-box {
    display: none; background: #2a0000; border: 1px solid #f44; border-radius: 6px;
    padding: 10px 14px; margin-bottom: 12px; font-size: .85rem; color: #f88;
    word-break: break-word;
  }
  button {
    padding: 10px 20px; border: none; border-radius: 6px;
    background: #006666; color: #fff; font-size: 1rem; cursor: pointer;
  }
  button:hover { background: #008888; }
  button.secondary { background: #333; margin-left: 8px; }
  #spinner { display: none; color: #0ff; margin-top: 10px; }
</style>
</head>
<body>
<h1>Grabette — WiFi Setup</h1>
<div id="status">Scanning networks…</div>
<div id="error-box"></div>
<ul id="networks"></ul>
<div id="form">
  <label id="net-label">Password for: <strong id="net-name"></strong></label>
  <div class="pw-row">
    <input type="password" id="password" placeholder="WiFi password" autocomplete="off"
           onkeydown="if(event.key==='Enter') connect()">
    <button type="button" class="toggle" id="pw-toggle" onclick="togglePw()">Show</button>
  </div>
  <button onclick="connect()">Connect</button>
  <button class="secondary" onclick="cancelForm()">Cancel</button>
</div>
<div id="spinner">Connecting, please wait…</div>
<button onclick="scan()" style="margin-top:8px">Refresh networks</button>

<script>
let selectedSsid = null;
let checkAttempts = 0;
const MAX_CHECKS = 30; // 30 × 3 s = 90 s max

async function scan() {
  setStatus('Scanning…');
  hideError();
  document.getElementById('networks').innerHTML = '';
  try {
    const r = await fetch('/api/wifi/scan');
    const nets = await r.json();
    if (!nets.length) { setStatus('No networks found.', 'err'); return; }
    setStatus('Select a network:');
    const ul = document.getElementById('networks');
    nets.forEach(n => {
      const li = document.createElement('li');
      li.innerHTML = '<span>' + escHtml(n.ssid) + '</span><span class="signal">' + n.signal + '%</span>';
      li.onclick = () => selectNet(n.ssid, li);
      ul.appendChild(li);
    });
  } catch(e) { setStatus('Scan failed: ' + e, 'err'); }
}

function selectNet(ssid, el) {
  document.querySelectorAll('#networks li').forEach(l => l.classList.remove('selected'));
  el.classList.add('selected');
  selectedSsid = ssid;
  document.getElementById('net-name').textContent = ssid;
  document.getElementById('password').value = '';
  document.getElementById('pw-toggle').textContent = 'Show';
  document.getElementById('password').type = 'password';
  hideError();
  document.getElementById('form').style.display = 'block';
  document.getElementById('password').focus();
}

function cancelForm() {
  document.getElementById('form').style.display = 'none';
  selectedSsid = null;
  hideError();
}

function togglePw() {
  const pw = document.getElementById('password');
  const btn = document.getElementById('pw-toggle');
  if (pw.type === 'password') { pw.type = 'text';     btn.textContent = 'Hide'; }
  else                        { pw.type = 'password'; btn.textContent = 'Show'; }
}

async function connect() {
  if (!selectedSsid) return;
  const pw = document.getElementById('password').value;
  hideError();
  document.getElementById('form').style.display = 'none';
  document.getElementById('spinner').style.display = 'block';
  setStatus('Connecting to ' + escHtml(selectedSsid) + '…');
  checkAttempts = 0;
  try {
    const r = await fetch('/api/wifi/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ssid: selectedSsid, password: pw})
    });
    if (r.status === 202) {
      setTimeout(checkStatus, 3000);
    } else {
      const d = await r.json();
      showError('HTTP ' + r.status + ': ' + (d.detail || 'Unknown error'));
      document.getElementById('spinner').style.display = 'none';
      document.getElementById('form').style.display = 'block';
    }
  } catch(e) {
    showError('Request failed: ' + e);
    document.getElementById('spinner').style.display = 'none';
    document.getElementById('form').style.display = 'block';
  }
}

async function checkStatus() {
  checkAttempts++;
  try {
    const [wifiRes, connRes] = await Promise.all([
      fetch('/api/wifi/status'),
      fetch('/api/wifi/connect-result')
    ]);
    const wifi = await wifiRes.json();
    const conn = await connRes.json();

    // Connection failed → show error and re-display form
    if (conn.status === 'error') {
      document.getElementById('spinner').style.display = 'none';
      showError(conn.message);
      setStatus('Connection failed.', 'err');
      document.getElementById('form').style.display = 'block';
      return;
    }

    // Connected!
    if (wifi.mode === 'connected') {
      document.getElementById('spinner').style.display = 'none';
      setStatus('✓ Connected to: ' + wifi.ssid, 'ok');
      return;
    }

    // Timeout
    if (checkAttempts >= MAX_CHECKS) {
      document.getElementById('spinner').style.display = 'none';
      showError('Connection timed out. Check the password and try again.');
      setStatus('Connection timed out.', 'err');
      document.getElementById('form').style.display = 'block';
      return;
    }

    setStatus('Connecting… (' + checkAttempts + ')');
    setTimeout(checkStatus, 3000);
  } catch(e) {
    // Grabette unreachable = it switched networks = success
    document.getElementById('spinner').style.display = 'none';
    setStatus('✓ Grabette switched to the new network.', 'ok');
  }
}

function setStatus(msg, cls) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = cls || '';
}

function showError(msg) {
  const el = document.getElementById('error-box');
  el.textContent = msg;
  el.style.display = 'block';
}

function hideError() {
  document.getElementById('error-box').style.display = 'none';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

scan();
</script>
</body>
</html>
"""
