"""Gradio dashboard for Grabette — camera view, capture controls, session/episode management."""

from __future__ import annotations

import io
import logging
import math

import gradio as gr
from PIL import Image

from grabette.ui.api_client import GrabetteClient

logger = logging.getLogger(__name__)


MODAL_CSS = """
#hf-auth-modal {
    position: fixed !important;
    inset: 0 !important;
    background: rgba(0, 0, 0, 0.78) !important;
    z-index: 9999 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    margin: 0 !important;
    padding: 1rem !important;
    border-radius: 0 !important;
    border: none !important;
    gap: 0 !important;
}
#hf-auth-card {
    max-width: 460px !important;
    width: 100% !important;
    background: #1f2937 !important;
    border-radius: 12px !important;
    padding: 2rem !important;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6) !important;
    border: 1px solid #374151 !important;
}
"""

_HEAD_HTML = """
<style>
.nav-holder { background: #111827 !important; border-bottom: 2px solid #f97316 !important; padding: 0 1rem !important; }
nav a { color: #9ca3af !important; font-weight: 600 !important; font-size: 0.95rem !important; padding: 12px 20px !important; border-radius: 0 !important; border: none !important; border-bottom: 3px solid transparent !important; }
nav a.active { color: #ffffff !important; background-color: transparent !important; border-bottom: 3px solid #f97316 !important; }
nav a:hover { color: #e5e7eb !important; background-color: rgba(255,255,255,0.07) !important; }
#tasks-col { background: #1e293b !important; border-radius: 8px !important; padding: 8px !important; }
</style>
<script>
var _wSsid = null, _wAttempts = 0;
function wSt(msg, cls) { var el = document.getElementById('wifi-st'); if (!el) return; el.textContent = msg; el.className = cls || ''; }
function wShowErr(msg) { var el = document.getElementById('wifi-err'); if (!el) return; el.textContent = msg; el.style.display = 'block'; }
function wHideErr() { var el = document.getElementById('wifi-err'); if (el) el.style.display = 'none'; }
function wEsc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
async function wScan() {
    wSt('Scanning…'); wHideErr();
    var ul = document.getElementById('wifi-nets');
    if (!ul) return;
    ul.innerHTML = '';
    try {
        var nets = await (await fetch('/api/wifi/scan')).json();
        if (!nets.length) { wSt('No networks found.', 'err'); return; }
        wSt('Select a network:');
        nets.forEach(function(n) {
            var li = document.createElement('li');
            li.innerHTML = '<span>' + wEsc(n.ssid) + '</span><span class="wifi-sig">' + n.signal + '%</span>';
            li.onclick = function() { wSel(n.ssid, li); };
            ul.appendChild(li);
        });
    } catch(e) { wSt('Scan failed: ' + e, 'err'); }
}
function wSel(ssid, el) {
    document.querySelectorAll('#wifi-nets li').forEach(function(l) { l.classList.remove('wsel'); });
    el.classList.add('wsel');
    _wSsid = ssid;
    document.getElementById('wifi-net-name').textContent = ssid;
    document.getElementById('wifi-pw').value = '';
    wHideErr();
    document.getElementById('wifi-form').style.display = 'block';
    document.getElementById('wifi-pw').focus();
}
function wCancel() { document.getElementById('wifi-form').style.display = 'none'; _wSsid = null; wHideErr(); }
function wTogglePw(btn) { var pw = document.getElementById('wifi-pw'); if (pw.type === 'password') { pw.type = 'text'; btn.textContent = 'Hide'; } else { pw.type = 'password'; btn.textContent = 'Show'; } }
async function wConn() {
    if (!_wSsid) return;
    var pw = document.getElementById('wifi-pw').value;
    wHideErr();
    document.getElementById('wifi-form').style.display = 'none';
    document.getElementById('wifi-spin').style.display = 'block';
    wSt('Connecting to ' + wEsc(_wSsid) + '…');
    _wAttempts = 0;
    try {
        var r = await fetch('/api/wifi/connect', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ssid: _wSsid, password: pw}) });
        if (r.status === 202) { setTimeout(wPoll, 3000); }
        else { var d = await r.json(); wShowErr('HTTP ' + r.status + ': ' + (d.detail || '?')); document.getElementById('wifi-spin').style.display = 'none'; document.getElementById('wifi-form').style.display = 'block'; }
    } catch(e) { wShowErr('' + e); document.getElementById('wifi-spin').style.display = 'none'; document.getElementById('wifi-form').style.display = 'block'; }
}
async function wPoll() {
    _wAttempts++;
    try {
        var results = await Promise.all([fetch('/api/wifi/status'), fetch('/api/wifi/connect-result')]);
        var wifi = await results[0].json(), conn = await results[1].json();
        if (conn.status === 'error') { document.getElementById('wifi-spin').style.display = 'none'; wShowErr(conn.message); wSt('Connection failed.', 'err'); document.getElementById('wifi-form').style.display = 'block'; return; }
        if (wifi.mode === 'connected') { document.getElementById('wifi-spin').style.display = 'none'; wSt('✓ Connected to: ' + wifi.ssid, 'ok'); return; }
        if (_wAttempts >= 30) { document.getElementById('wifi-spin').style.display = 'none'; wShowErr('Timed out.'); wSt('Timed out.', 'err'); document.getElementById('wifi-form').style.display = 'block'; return; }
        wSt('Connecting… (' + _wAttempts + ')');
        setTimeout(wPoll, 3000);
    } catch(e) { document.getElementById('wifi-spin').style.display = 'none'; wSt('✓ Grabette switched to the new network.', 'ok'); }
}
new MutationObserver(function() {
    var s = document.getElementById('wifi-section');
    if (s && !s.dataset.inited) { s.dataset.inited = '1'; wScan(); }
}).observe(document.documentElement, {childList: true, subtree: true});
</script>
"""

_IMU_IFRAME_HTML = (
    '<iframe src="/charts/imu" '
    'style="width:100%;height:38vh;border:none;'
    'border-radius:8px;background:transparent;"></iframe>'
)
_ANGLE_IFRAME_HTML = (
    '<iframe src="/charts/angle" '
    'style="width:100%;height:18vh;border:none;'
    'border-radius:8px;background:transparent;"></iframe>'
)
# Replacement HTML used while teleop is active. gr.update(value="") doesn't
# seem to force a DOM swap (Gradio may treat empty as no-op), so we use an
# explicit non-empty placeholder. Same height as the real iframes to avoid
# layout shift; src=about:blank guarantees no /api/state/history polling.
_IMU_IFRAME_PAUSED = (
    '<iframe src="about:blank" '
    'style="width:100%;height:38vh;border:none;'
    'border-radius:8px;background:#1a1a1a;"></iframe>'
)
_ANGLE_IFRAME_PAUSED = (
    '<iframe src="about:blank" '
    'style="width:100%;height:18vh;border:none;'
    'border-radius:8px;background:#1a1a1a;"></iframe>'
)

_WIFI_SETTINGS_HTML = """
<div id="wifi-section">
<style>
#wifi-section { font-family: sans-serif; color: #f1f5f9; }
#wifi-st { font-size:.85rem; color:#94a3b8; margin-bottom:12px; min-height:1.2em; }
#wifi-st.ok  { color:#4ade80; }
#wifi-st.err { color:#f87171; }
#wifi-nets { list-style:none; margin-bottom:16px; padding:0; }
#wifi-nets li {
  display:flex; justify-content:space-between; align-items:center;
  padding:8px 12px; margin-bottom:4px; border-radius:6px;
  background:#1e293b; cursor:pointer; border:1px solid #334155;
}
#wifi-nets li:hover { background:#263548; border-color:#f97316; }
#wifi-nets li.wsel { background:#2d1f0e; border-color:#f97316; }
.wifi-sig { font-size:.75rem; color:#94a3b8; }
#wifi-form {
  display:none; background:#1e293b; border-radius:8px;
  padding:14px; margin-bottom:12px; border:1px solid #334155;
}
#wifi-form label { display:block; margin-bottom:6px; color:#f97316; font-size:.9rem; }
.wifi-pw { display:flex; gap:8px; margin-bottom:12px; }
.wifi-pw input {
  flex:1; padding:8px 10px; border-radius:4px;
  border:1px solid #475569; background:#0f172a; color:#f1f5f9; font-size:1rem;
}
.wifi-pw button {
  padding:8px 12px; background:#334155; border:1px solid #475569;
  border-radius:4px; color:#cbd5e1; font-size:.85rem; cursor:pointer;
}
.wifi-pw button:hover { background:#475569; }
#wifi-err {
  display:none; background:#2a0000; border:1px solid #f87171; border-radius:6px;
  padding:10px 14px; margin-bottom:12px; font-size:.85rem; color:#fca5a5;
}
.wbtn {
  padding:8px 18px; border:none; border-radius:6px;
  background:#f97316; color:#fff; font-size:.9rem; cursor:pointer; font-weight:600;
  transition: background .1s, transform .1s;
}
.wbtn:hover { background:#ea6c0a; }
.wbtn:active { background:#c2410c; transform:scale(0.97); }
.wbtn.sec { background:#334155; font-weight:400; margin-left:8px; }
.wbtn.sec:hover { background:#475569; }
.wbtn.sec:active { background:#1e293b; transform:scale(0.97); }
#wifi-spin { display:none; color:#f97316; margin-top:10px; font-size:.85rem; }
</style>
<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
     onload="setTimeout(()=>window.wScan&&window.wScan(),100)" style="display:none" alt="">
<div id="wifi-st">—</div>
<div id="wifi-err"></div>
<ul id="wifi-nets"></ul>
<div id="wifi-form">
  <label>Password for: <strong id="wifi-net-name"></strong></label>
  <div class="wifi-pw">
    <input type="password" id="wifi-pw" placeholder="WiFi password" autocomplete="off"
           onkeydown="if(event.key==='Enter') wConn()">
    <button onclick="wTogglePw(this)">Show</button>
  </div>
  <button class="wbtn" onclick="wConn()">Connect</button>
  <button class="wbtn sec" onclick="wCancel()">Cancel</button>
</div>
<div id="wifi-spin">Connecting, please wait…</div>
<button class="wbtn sec" onclick="wScan()" style="margin-top:8px">↺ Refresh networks</button>
</div>
"""


def create_ui(api_url: str | None = None) -> gr.Blocks:
    client = GrabetteClient(base_url=api_url)

    # ── Camera ────────────────────────────────────────────────────────

    def get_camera_frame():
        data = client.get_snapshot()
        if data is None:
            return None
        try:
            return Image.open(io.BytesIO(data))
        except Exception:
            return None

    def get_depth_frame():
        data = client.get_depth_snapshot()
        if data is None:
            return None
        try:
            return Image.open(io.BytesIO(data))
        except Exception:
            return None

    # ── Sensor state (Live Streaming page) ────────────────────────────

    def get_sensor_state():
        state = client.get_state()
        if state is None:
            return "## IMU Live\n*Disconnected*", "## Angle Sensors\n*Disconnected*"

        imu = state.get("imu")
        if imu:
            a = imu["accel"]
            g = imu["gyro"]
            imu_text = (
                f"`Accel: [{a[0]:+8.3f}, {a[1]:+8.3f}, {a[2]:+8.3f}] m/s²`\n\n"
                f"`Gyro:  [{g[0]:+8.4f}, {g[1]:+8.4f}, {g[2]:+8.4f}] rad/s`"
            )
        else:
            imu_text = "*No IMU data*"

        angle = state.get("angle")
        if angle:
            p_deg = math.degrees(angle["proximal"])
            d_deg = math.degrees(angle["distal"])
            angle_text = (
                f"`Proximal: {p_deg:+7.2f}°  ({angle['proximal']:+.4f} rad)`\n\n"
                f"`Distal:   {d_deg:+7.2f}°  ({angle['distal']:+.4f} rad)`"
            )
        else:
            angle_text = "## Angle Sensors\n*No angle data*"

        return imu_text, angle_text

    # ── Capture (Datasets page) ───────────────────────────────────────

    def get_capture_status():
        state = client.get_state()
        if state is None:
            return "○ Idle"
        cap = state.get("capture", {})
        if cap.get("is_capturing", False):
            parts = [
                f"● RECORDING  {cap.get('session_id', '')}",
                f"Duration: {cap.get('duration_seconds', 0):.1f}s",
                f"Frames: {cap.get('frame_count', 0)}  |  IMU: {cap.get('imu_sample_count', 0)}",
            ]
            if cap.get("angle_sample_count", 0):
                parts[-1] += f"  |  Angle: {cap['angle_sample_count']}"
            return "\n".join(parts)
        return "○ Idle"

    def on_toggle_capture(session_id):
        state = client.get_state()
        capturing = state.get("capture", {}).get("is_capturing", False) if state else False
        if capturing:
            client.stop_capture()
            rows, move_dd, *_ = _refresh_episode_table(session_id)
            return gr.update(value="Start Capture", variant="primary"), rows, move_dd
        else:
            client.start_capture(session_id=session_id or None)
            return gr.update(value="Stop Capture", variant="stop"), gr.update(), gr.update()

    def get_teleop_display():
        """Polled on a slow (~1 Hz) timer, separately from get_sensor_state.

        Returns the teleop_msg text. When teleop is off, the textbox is
        cleared so it doesn't visually compete with the capture box.
        Doing this on the main state_timer caused HTTP backpressure that
        made the IMU / Angle markdown flicker and bursted the WS stream.
        """
        tstatus = client.get_teleop_status() or {}
        if not tstatus.get("active"):
            return ""
        sending = "YES" if tstatus.get("sending") else "no"
        stats = tstatus.get("stats", {}) or {}
        hz = stats.get("mean_hz", 0)
        n = stats.get("n_poses", 0)
        return f"● TELEOP ON   sending: {sending}   VIO: {hz:.1f} Hz   {n} poses"

    def _oakd_button_update():
        """Compute the OAK-D toggle button's appearance from current state."""
        s = client.get_oakd_status() or {}
        if not s.get("supported"):
            return gr.update(
                value="OAK-D not available",
                variant="secondary",
                interactive=False,
            )
        enabled = bool(s.get("enabled"))
        # Greyed out while capture or teleop holds the OAK — toggling is
        # refused server-side anyway, but the visual cue prevents user
        # confusion.
        state = client.get_state() or {}
        capturing = bool(state.get("capture", {}).get("is_capturing"))
        tstatus = client.get_teleop_status() or {}
        teleop = bool(tstatus.get("active"))
        busy = capturing or teleop
        if enabled:
            label = "OAK-D: ON" + ("  (busy)" if busy else "  — click to disable")
            variant = "primary"
        else:
            label = "OAK-D: OFF" + ("  (busy)" if busy else "  — click to enable")
            variant = "secondary"
        return gr.update(value=label, variant=variant, interactive=not busy)

    def on_toggle_oakd():
        s = client.get_oakd_status() or {}
        enabled = bool(s.get("enabled"))
        result = client.set_oakd(not enabled)
        if "error" in result:
            logger.warning("OAK-D toggle failed: %s", result["error"])
        return _oakd_button_update()

    def poll_oakd():
        return _oakd_button_update()

    def on_toggle_teleop():
        """Single-button toggle: enter teleop mode if off, exit if on.

        Entering teleop pauses ALL UI live-view sources so uvicorn's event
        loop is free for /api/teleop/stream:
          - Gradio Timers (camera, depth, sensor, teleop) → interval set to
            a huge value (Gradio's active=False propagation is unreliable for
            gr.Timer at runtime; bumping the interval is a deterministic kill)
          - IMU/angle chart iframes → swapped to about:blank placeholders
            so their JS stops polling /api/state/history

        Returns: (teleop_msg, teleop_btn, camera_timer, depth_timer,
        sensor_timer, teleop_timer, imu_iframe, angle_iframe).
        """
        status = client.get_teleop_status() or {}
        active = bool(status.get("active"))
        daemon = client.get_daemon_status() or {}
        if daemon.get("backend") != "RpiBackend":
            return ("Teleop not available (mock backend)",
                    gr.update(value="Enter Teleop Mode", variant="secondary", interactive=False),
                    gr.update(), gr.update(), gr.update(), gr.update(),
                    gr.update(), gr.update())
        if active:
            result = client.stop_teleop()
            if "error" in result:
                return (f"Stop error: {result['error']}",
                        gr.update(value="Exit Teleop Mode", variant="stop"),
                        gr.update(), gr.update(), gr.update(), gr.update(),
                        gr.update(), gr.update())
            # Exiting teleop — resume live-view timers and restore iframes.
            return ("Teleop OFF",
                    gr.update(value="Enter Teleop Mode", variant="secondary"),
                    gr.update(value=0.2),    # camera_timer
                    gr.update(value=0.2),    # depth_timer
                    gr.update(value=0.5),    # sensor_timer
                    gr.update(value=1.0),    # teleop_timer
                    gr.update(value=_IMU_IFRAME_HTML),
                    gr.update(value=_ANGLE_IFRAME_HTML))
        else:
            result = client.start_teleop()
            if "error" in result:
                return (f"Start error: {result['error']}",
                        gr.update(value="Enter Teleop Mode", variant="secondary"),
                        gr.update(), gr.update(), gr.update(), gr.update(),
                        gr.update(), gr.update())
            # Entering teleop — disable ALL live-view timers via huge intervals.
            return ("Teleop ON (press button to send deltas)",
                    gr.update(value="Exit Teleop Mode", variant="stop"),
                    gr.update(value=86400),  # camera_timer
                    gr.update(value=86400),  # depth_timer
                    gr.update(value=86400),  # sensor_timer
                    gr.update(value=86400),  # teleop_timer
                    gr.update(value=_IMU_IFRAME_PAUSED),
                    gr.update(value=_ANGLE_IFRAME_PAUSED))

    # ── Task (Session) helpers ────────────────────────────────────────

    def _get_sessions():
        return client.list_sessions()

    def _task_choices(sessions):
        return [(s["name"], s["id"]) for s in sessions]

    def _refresh_episode_table(session_id, sessions=None):
        if sessions is None:
            sessions = _get_sessions()
        rows = []
        task_name = ""
        task_description = ""
        for s in sessions:
            if s["id"] == session_id:
                task_name = s.get("name", "")
                task_description = s.get("description", "")
                for ep in s.get("episodes", []):
                    rows.append([
                        False,
                        ep["episode_id"],
                        f"{ep['duration_seconds']:.1f}s",
                        ep["frame_count"],
                        ep["imu_sample_count"],
                        ep.get("angle_sample_count", 0),
                    ])
                break
        rows.reverse()
        move_choices = _task_choices(sessions)
        move_dd = gr.update(
            choices=move_choices,
            value=move_choices[0][1] if move_choices else None,
        )
        task_header = f"## Task: {task_name}" if task_name else ""
        desc = f"**Description:** {task_description}" if task_description else ""
        cap_title = f"### Capture" if not task_name else f"### Capture a new episode for *{task_name}*"
        ep_title = f"## Episodes for *{task_name}*" if task_name else "## Episodes"
        return rows, move_dd, task_header, desc, cap_title, ep_title

    def refresh_tasks():
        sessions = _get_sessions()
        choices = _task_choices(sessions)
        value = choices[0][1] if choices else None
        rows, move_dd, task_header, desc, cap_title, ep_title = _refresh_episode_table(value, sessions)
        return gr.update(choices=choices, value=value), task_header, cap_title, desc, ep_title, rows, move_dd

    def on_task_select(session_id):
        rows, move_dd, task_header, desc, cap_title, ep_title = _refresh_episode_table(session_id)
        return task_header, cap_title, desc, ep_title, rows, move_dd

    def on_create_task(name, description):
        if not name:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(visible=True)
        result = client.create_session(name, description or "")
        if "error" in result:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(visible=True)
        sessions = _get_sessions()
        choices = _task_choices(sessions)
        new_id = result["id"]
        rows, move_dd, task_header, desc, cap_title, ep_title = _refresh_episode_table(new_id, sessions)
        return gr.update(choices=choices, value=new_id), task_header, cap_title, desc, ep_title, rows, move_dd, gr.update(visible=False)

    # ── Edit Task helpers ─────────────────────────────────────────────

    def on_open_edit_form(session_id):
        if not session_id:
            return gr.update(visible=False), "", ""
        sessions = _get_sessions()
        for s in sessions:
            if s["id"] == session_id:
                return gr.update(visible=True), s.get("name", ""), s.get("description", "")
        return gr.update(visible=True), "", ""

    def on_save_task(session_id, new_name, new_desc):
        if not session_id or not new_name.strip():
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(visible=True)
        client.update_session(session_id, name=new_name.strip(), description=new_desc)
        sessions = _get_sessions()
        choices = _task_choices(sessions)
        _, _, task_header, desc, cap_title, ep_title = _refresh_episode_table(session_id, sessions)
        return (
            gr.update(choices=choices, value=session_id),
            task_header, cap_title, desc, ep_title,
            gr.update(visible=False),
        )

    def on_delete_task(session_id):
        if not session_id:
            return (gr.update(),) * 9
        client.delete_session(session_id)
        sessions = _get_sessions()
        choices = _task_choices(sessions)
        value = choices[0][1] if choices else None
        rows, move_dd, task_header, desc, cap_title, ep_title = _refresh_episode_table(value, sessions)
        return (
            gr.update(choices=choices, value=value),
            task_header, cap_title, desc, ep_title, rows, move_dd,
            gr.update(visible=False),
            gr.update(visible=False),
        )

    def _get_selected_ids(table_data) -> list[str]:
        if table_data is None:
            return []
        try:
            if table_data.empty:
                return []
            selected = table_data[table_data.iloc[:, 0] == True]
            return selected.iloc[:, 1].tolist()
        except Exception:
            return []

    # ── Episode actions ───────────────────────────────────────────────

    def on_download_episodes(table_data):
        episode_ids = _get_selected_ids(table_data)
        if not episode_ids:
            return None
        return client.download_episodes(episode_ids)

    def on_delete_episode(table_data, session_id):
        episode_ids = _get_selected_ids(table_data)
        if not episode_ids:
            return "No episode selected", gr.update(), gr.update()
        errors = []
        for eid in episode_ids:
            result = client.delete_episode(eid)
            if "error" in result:
                errors.append(f"{eid}: {result['error']}")
        rows, move_dd, *_ = _refresh_episode_table(session_id)
        if errors:
            return "Errors: " + "; ".join(errors), rows, move_dd
        return f"Deleted {len(episode_ids)} episode(s)", rows, move_dd

    def on_move_episodes(table_data, target_session_id, current_session_id):
        episode_ids = _get_selected_ids(table_data)
        if not episode_ids:
            return "No episode selected", gr.update(), gr.update()
        if not target_session_id:
            return "No target task", gr.update(), gr.update()
        result = client.move_episodes(episode_ids, target_session_id)
        if "error" in result:
            return f"Error: {result['error']}", gr.update(), gr.update()
        rows, move_dd, *_ = _refresh_episode_table(current_session_id)
        return f"Moved {len(episode_ids)} episode(s)", rows, move_dd

    # ── SLAM ──────────────────────────────────────────────────────────

    def on_slam_run(table_data, repo_id: str):
        episode_ids = _get_selected_ids(table_data)
        episode_id = episode_ids[0] if episode_ids else None
        if not episode_id:
            return "Select an episode first"
        if not repo_id:
            return "Enter a HuggingFace repo ID first"
        result = client.slam_run(episode_id, repo_id)
        if "error" in result:
            return f"Error: {result['error']}"
        return f"SLAM started (job: {result.get('job_id', '?')})"

    def get_slam_status():
        jobs = client.hf_list_jobs()
        slam_jobs = [j for j in jobs if j.get("name", "").startswith("slam:")]
        if not slam_jobs:
            return "No SLAM jobs"
        latest = slam_jobs[-1]
        status = latest["status"]
        if status == "completed":
            return f"Complete: {latest.get('result', '')}"
        if status == "failed":
            return f"Failed: {latest.get('error', '')}"
        if status == "running":
            return f"Running ({latest.get('progress', 0):.0f}%): {latest.get('message', '')}"
        return f"Pending: {latest.get('message', '')}"

    # ── Replay ────────────────────────────────────────────────────────

    def _video_iframe(episode_id: str) -> str:
        return (
            f'<iframe src="/api/replay/video?episode_id={episode_id}" '
            'style="width:100%;height:320px;border:none;'
            'border-radius:8px;background:#000;"></iframe>'
        )

    def on_replay_start(table_data):
        episode_id = (_get_selected_ids(table_data) or [None])[0]
        if not episode_id:
            return "No episode selected", gr.update(visible=False), gr.update(), gr.update(), gr.update()
        result = client.replay_start(episode_id)
        if "error" in result:
            return f"Error: {result['error']}", gr.update(visible=False), gr.update(), gr.update(), gr.update()
        dur = result.get("duration_ms", 0)
        return (
            f"Replaying {episode_id}",
            gr.update(visible=True),
            gr.update(maximum=dur, value=0),
            gr.update(active=True),
            gr.update(value=_video_iframe(episode_id)),
        )

    def on_replay_stop():
        client.replay_stop()
        return "Replay stopped", gr.update(visible=False), gr.update(active=False), gr.update(value="")

    def on_replay_pause_play():
        st = client.replay_status()
        if st.get("playing"):
            client.replay_pause()
            return "Play"
        else:
            client.replay_resume()
            return "Pause"

    def on_replay_seek(time_ms):
        if time_ms is not None:
            client.replay_seek(float(time_ms))

    def poll_replay_status():
        st = client.replay_status()
        if not st.get("active"):
            return (
                gr.update(), gr.update(), gr.update(),
                gr.update(active=False),
                gr.update(visible=False),
                gr.update(value=""),
            )
        t = st.get("time_ms", 0)
        dur = st.get("duration_ms", 0)
        playing = st.get("playing", False)
        label = f"{t / 1000:.1f}s / {dur / 1000:.1f}s" + (" (paused)" if not playing else "")
        return (
            gr.update(value=t),
            label,
            "Pause" if playing else "Play",
            gr.update(),
            gr.update(),
            gr.update(),
        )

    # ── System bar ────────────────────────────────────────────────────

    def get_system_bar():
        info = client.get_system_info()
        if info is None:
            return "<p style='color:#64748b;font-size:0.85rem;margin:0.75rem 0;'>System disconnected</p>"
        cards = [
            ("Host", info.get("hostname", "?")),
            ("CPU temp", f"{info['cpu_temp_c']} °C" if "cpu_temp_c" in info else None),
            ("Disk free", f"{info['disk_free_gb']} GB" if "disk_free_gb" in info else None),
            ("IP", info.get("ip")),
        ]
        parts = []
        for label, value in cards:
            if not value:
                continue
            parts.append(
                f"<div style='background:#1e293b;border-radius:8px;padding:0.6rem 0.9rem;"
                f"border:1px solid #334155;'>"
                f"<div style='font-size:0.7rem;text-transform:uppercase;letter-spacing:0.08em;"
                f"color:#94a3b8;margin-bottom:0.25rem;'>{label}</div>"
                f"<div style='font-size:0.95rem;font-weight:600;color:#f1f5f9;'>{value}</div>"
                f"</div>"
            )
        return (
            "<div style='display:flex;flex-direction:column;gap:0.5rem;height:100%;'>"
            + "".join(parts)
            + "</div>"
        )

    # ── HuggingFace ───────────────────────────────────────────────────

    def _hf_status_text(result):
        if result.get("authenticated"):
            return f"Authenticated as {result.get('user', {}).get('username', '?')}"
        return "Not authenticated"

    def check_hf_auth_on_load():
        result = client.hf_check_auth()
        return gr.update(visible=not result.get("authenticated", False))

    def load_datasets_page():
        sessions = _get_sessions()
        task_choices = [(s["name"], s["id"]) for s in sessions]
        namespaces = client.hf_get_namespaces()
        ns_choices = [f"{ns}/" for ns in namespaces]
        ns_update = gr.update(
            choices=ns_choices,
            value=ns_choices[0] if ns_choices else None,
        )
        return gr.update(choices=task_choices, value=[]), ns_update

    def on_ds_upload(task_ids, namespace, repo_name):
        if not task_ids:
            return "Select at least one task"
        if not namespace or not repo_name.strip():
            return "Enter a namespace and a repository name"
        repo_id = f"{namespace}{repo_name.strip()}"
        sessions = _get_sessions()
        session_map = {s["id"]: s for s in sessions}
        jobs, errors = [], []
        for tid in task_ids:
            s = session_map.get(tid)
            if not s:
                continue
            for ep in s.get("episodes", []):
                result = client.hf_upload_episode(ep["episode_id"], repo_id)
                if "error" in result:
                    errors.append(f"{ep['episode_id']}: {result['error']}")
                else:
                    jobs.append(result.get("job_id", "?"))
        if errors:
            return f"Errors: {'; '.join(errors)}"
        if not jobs:
            return "No episodes found in selected tasks"
        return f"Started {len(jobs)} upload job(s)"

    def on_modal_auth(token):
        if not token:
            return gr.update(visible=True, value="Please enter a token"), gr.update()
        result = client.hf_set_auth(token)
        if result.get("authenticated"):
            return gr.update(visible=False), gr.update(visible=False)
        return (
            gr.update(visible=True, value=f"Auth failed: {result.get('error', 'unknown')}"),
            gr.update(),
        )

    def on_hf_upload(table_data, repo_id):
        episode_ids = _get_selected_ids(table_data)
        episode_id = episode_ids[0] if episode_ids else None
        if not episode_id:
            return "Select an episode first"
        if not repo_id:
            return "Enter a repo ID (e.g. username/grabette-data)"
        result = client.hf_upload_episode(episode_id, repo_id)
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Upload started (job: {result.get('job_id', '?')})"

    def check_hf_account():
        return _hf_status_text(client.hf_check_auth())

    def on_hf_update_token(token):
        if not token:
            return "No token provided", gr.update()
        result = client.hf_set_auth(token)
        return _hf_status_text(result), gr.update(value="")

    def on_hf_remove_token():
        client.hf_set_auth("")
        return "Not authenticated"

    # ══════════════════════════════════════════════════════════════════
    # Page 1 — Episodes
    # ══════════════════════════════════════════════════════════════════

    with gr.Blocks(title="Grabette", css=MODAL_CSS, head=_HEAD_HTML) as demo:
        gr.Navbar(main_page_name="Episodes")
        gr.Markdown("# GRABETTE")

        # ── Main layout ───────────────────────────────────────────────
        with gr.Row():

            # ── LEFT: Tasks ──────────────────────────────────────────
            with gr.Column(scale=1, min_width=200, elem_id="tasks-col"):
                gr.Markdown("## Tasks")
                task_list = gr.Radio(choices=[], label=None, container=False)
                new_task_btn = gr.Button("+ New Task", size="sm", variant="primary")
                with gr.Group(visible=False) as new_task_form:
                    new_task_name = gr.Textbox(label="Name", placeholder="e.g. Kitchen Pick & Place")
                    new_task_desc = gr.Textbox(label="Description", placeholder="Optional")
                    with gr.Row():
                        create_task_btn = gr.Button("Create", variant="primary", size="sm")
                        cancel_task_btn = gr.Button("Cancel", size="sm")

            # ── RIGHT: Episodes ──────────────────────────────────────
            with gr.Column(scale=3):

                # Task header: "## Task: X" + edit button
                with gr.Row():
                    with gr.Column(scale=5):
                        task_header_md = gr.Markdown("")
                    edit_task_btn = gr.Button("✏ Edit", size="sm", scale=1)
                task_desc_md = gr.Markdown("")

                # Edit Task panel (appears below description)
                with gr.Group(visible=False) as edit_task_form:
                    gr.Markdown("#### Edit Task")
                    rename_input = gr.Textbox(label="Name", placeholder="Task name…")
                    desc_edit_input = gr.Textbox(label="Description", placeholder="Description…")
                    with gr.Row():
                        delete_task_btn = gr.Button("Delete Task", variant="stop", size="sm")
                        cancel_edit_btn = gr.Button("Cancel", size="sm")
                        save_task_btn = gr.Button("Save changes", variant="primary", size="sm")
                    with gr.Group(visible=False) as delete_confirm:
                        gr.Markdown(
                            "⚠ **This will permanently delete the task and ALL its episodes. "
                            "This action cannot be undone.**"
                        )
                        with gr.Row():
                            confirm_delete_btn = gr.Button(
                                "Yes, delete everything", variant="stop", size="sm",
                            )
                            cancel_delete_btn = gr.Button("Cancel", size="sm")

                # Capture
                capture_title = gr.Markdown("### Capture")
                with gr.Row():
                    capture_box = gr.Textbox(
                        label="Status", lines=2, interactive=False, scale=3,
                    )
                    toggle_btn = gr.Button("Start Capture", variant="primary", scale=1)

                episodes_title = gr.Markdown("## Episodes")

                episodes_table = gr.Dataframe(
                    headers=["✓", "Episode ID", "Duration", "Frames", "IMU", "Angle"],
                    datatype=["bool", "str", "str", "number", "number", "number"],
                    interactive=True,
                    col_count=(6, "fixed"),
                    show_search="filter",
                )
                with gr.Row():
                    replay_btn = gr.Button("▶ Replay", size="md", scale=1)
                    with gr.Accordion("Download", open=False):
                        dl_btn = gr.Button("Download selected", size="sm")
                        dl_file = gr.File(label="Download")
                    with gr.Accordion("Move to Task", open=False):
                        move_target_dd = gr.Dropdown(label="Move to task", interactive=True)
                        move_btn = gr.Button("Move", size="sm")
                    with gr.Accordion("Delete", open=False):
                        del_episode_btn = gr.Button("Delete selected", variant="stop", size="sm")

                episode_msg = gr.Textbox(show_label=False, interactive=False, max_lines=1)

                # Replay panel (hidden until replay starts)
                with gr.Group(visible=False) as replay_panel:
                    gr.Markdown("#### Replay")
                    replay_video = gr.HTML(value="")
                    gr.HTML(
                        '<iframe src="/charts/imu" '
                        'style="width:100%;height:160px;border:none;'
                        'border-radius:8px;background:transparent;"></iframe>'
                    )
                    gr.HTML(
                        '<iframe src="/charts/angle" '
                        'style="width:100%;height:100px;border:none;'
                        'border-radius:8px;background:transparent;"></iframe>'
                    )
                    replay_slider = gr.Slider(
                        minimum=0, maximum=1, step=1, value=0,
                        label="Timeline (ms)", interactive=True,
                    )
                    replay_time_label = gr.Textbox(
                        value="0.0s / 0.0s", show_label=False,
                        interactive=False, max_lines=1,
                    )
                    with gr.Row():
                        replay_pause_btn = gr.Button("Pause", size="sm")
                        replay_stop_btn = gr.Button("Stop Replay", variant="stop", size="sm")
                replay_timer = gr.Timer(0.5, active=False)

        gr.HTML("""
            <div style="margin-top:2rem;padding:1.25rem 1.5rem;
                        background:#1e3a5f;border-radius:10px;
                        border:1px solid #2563eb;display:flex;
                        align-items:center;justify-content:space-between;gap:1rem;">
                <span style="color:#e2e8f0;font-size:0.95rem;">
                    Ready to push your episodes to HuggingFace?
                </span>
                <a href="/datasets" style="background:#2563eb;color:#fff;
                           padding:8px 18px;border-radius:6px;font-weight:600;
                           font-size:0.9rem;text-decoration:none;white-space:nowrap;">
                    Create a dataset &#8594;
                </a>
            </div>
        """)

        # ── Wire events ───────────────────────────────────────────────

        new_task_btn.click(fn=lambda: gr.update(visible=True), outputs=new_task_form)
        cancel_task_btn.click(fn=lambda: gr.update(visible=False), outputs=new_task_form)
        create_task_btn.click(
            fn=on_create_task,
            inputs=[new_task_name, new_task_desc],
            outputs=[task_list, task_header_md, capture_title, task_desc_md, episodes_title, episodes_table, move_target_dd, new_task_form],
        )
        task_list.change(
            fn=on_task_select, inputs=task_list,
            outputs=[task_header_md, capture_title, task_desc_md, episodes_title, episodes_table, move_target_dd],
        )

        # Edit Task
        edit_task_btn.click(
            fn=on_open_edit_form, inputs=task_list,
            outputs=[edit_task_form, rename_input, desc_edit_input],
        )
        cancel_edit_btn.click(
            fn=lambda: gr.update(visible=False), outputs=edit_task_form,
        )
        save_task_btn.click(
            fn=on_save_task,
            inputs=[task_list, rename_input, desc_edit_input],
            outputs=[task_list, task_header_md, capture_title, task_desc_md, episodes_title, edit_task_form],
        )
        delete_task_btn.click(
            fn=lambda: gr.update(visible=True), outputs=delete_confirm,
        )
        cancel_delete_btn.click(
            fn=lambda: gr.update(visible=False), outputs=delete_confirm,
        )
        confirm_delete_btn.click(
            fn=on_delete_task, inputs=task_list,
            outputs=[task_list, task_header_md, capture_title, task_desc_md, episodes_title,
                     episodes_table, move_target_dd, edit_task_form, delete_confirm],
        )

        toggle_btn.click(
            fn=on_toggle_capture,
            inputs=[task_list],
            outputs=[toggle_btn, episodes_table, move_target_dd],
        )

        dl_btn.click(fn=on_download_episodes, inputs=episodes_table, outputs=dl_file)
        del_episode_btn.click(
            fn=on_delete_episode, inputs=[episodes_table, task_list],
            outputs=[episode_msg, episodes_table, move_target_dd],
        )
        move_btn.click(
            fn=on_move_episodes, inputs=[episodes_table, move_target_dd, task_list],
            outputs=[episode_msg, episodes_table, move_target_dd],
        )

        replay_btn.click(
            fn=on_replay_start, inputs=episodes_table,
            outputs=[episode_msg, replay_panel, replay_slider, replay_timer, replay_video],
        )
        replay_stop_btn.click(
            fn=on_replay_stop,
            outputs=[episode_msg, replay_panel, replay_timer, replay_video],
        )
        replay_pause_btn.click(fn=on_replay_pause_play, outputs=replay_pause_btn)
        replay_slider.release(fn=on_replay_seek, inputs=replay_slider)
        replay_timer.tick(
            fn=poll_replay_status,
            outputs=[replay_slider, replay_time_label, replay_pause_btn,
                     replay_timer, replay_panel, replay_video],
        )

        capture_timer = gr.Timer(0.5)
        capture_timer.tick(fn=get_capture_status, outputs=capture_box)

        demo.load(fn=refresh_tasks, outputs=[task_list, task_header_md, capture_title, task_desc_md, episodes_title, episodes_table, move_target_dd])

    # ══════════════════════════════════════════════════════════════════
    # Page 2 — Datasets (HF auth popup + upload)
    # ══════════════════════════════════════════════════════════════════

    with demo.route("Datasets") as datasets_demo:
        gr.Navbar(main_page_name="Episodes")

        # HF Auth popup
        with gr.Group(visible=False, elem_id="hf-auth-modal") as ds_auth_modal:
            with gr.Group(elem_id="hf-auth-card"):
                gr.HTML(
                    "<h2 style='margin:0 0 0.4rem;'>HuggingFace Authentication</h2>"
                    "<p style='color:#9ca3af;margin:0 0 1.2rem;font-size:0.9rem;'>"
                    "A HuggingFace token is required to push datasets.</p>"
                )
                ds_modal_token = gr.Textbox(label="HF Token", type="password", placeholder="hf_...")
                ds_modal_msg = gr.Textbox(show_label=False, interactive=False, max_lines=1, visible=False)
                ds_modal_auth_btn = gr.Button("Authenticate", variant="primary", size="sm")

        # ── Page header ───────────────────────────────────────────────
        gr.HTML("""
        <div style="padding:2rem 0 1.5rem;">
          <h1 style="margin:0 0 0.4rem;font-size:1.8rem;">Create a Dataset</h1>
          <p style="margin:0;color:#94a3b8;font-size:0.95rem;">
            Package your recorded tasks and push them to HuggingFace Hub.
          </p>
        </div>
        """)

        # ── Step 1 ────────────────────────────────────────────────────
        gr.HTML("""
        <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;">
          <span style="background:#f97316;color:#fff;font-weight:700;
                       border-radius:50%;width:28px;height:28px;display:flex;
                       align-items:center;justify-content:center;flex-shrink:0;">1</span>
          <div>
            <div style="font-weight:600;font-size:1rem;">Select tasks to include</div>
            <div style="color:#94a3b8;font-size:0.85rem;">
              All episodes within each selected task will be uploaded.
            </div>
          </div>
        </div>
        """)
        ds_task_cbg = gr.CheckboxGroup(choices=[], label=None, container=False)

        # ── Step 2 ────────────────────────────────────────────────────
        gr.HTML("""
        <div style="display:flex;align-items:center;gap:0.75rem;
                    margin-top:1.5rem;margin-bottom:0.5rem;">
          <span style="background:#f97316;color:#fff;font-weight:700;
                       border-radius:50%;width:28px;height:28px;display:flex;
                       align-items:center;justify-content:center;flex-shrink:0;">2</span>
          <div>
            <div style="font-weight:600;font-size:1rem;">Name your destination repository</div>
            <div style="color:#94a3b8;font-size:0.85rem;">
              Choose a namespace and give a name to the dataset.
            </div>
          </div>
        </div>
        """)
        with gr.Row():
            ds_namespace = gr.Dropdown(
                label="Namespace", choices=[], interactive=True, scale=1,
            )
            ds_repo_name = gr.Textbox(
                label="Repository name", placeholder="grabette-data",
                scale=2,
            )

        # ── Upload ────────────────────────────────────────────────────
        gr.HTML("<div style='margin-top:1.5rem;max-width:260px;'>")
        ds_upload_btn = gr.Button(
            "Push to HuggingFace Hub",
            variant="huggingface",
        )
        gr.HTML("</div>")
        ds_upload_msg = gr.Textbox(
            show_label=False, interactive=False, max_lines=2, container=False,
        )
        gr.HTML("</div>")

        ds_modal_auth_btn.click(
            fn=on_modal_auth, inputs=ds_modal_token,
            outputs=[ds_modal_msg, ds_auth_modal],
        )
        ds_upload_btn.click(
            fn=on_ds_upload,
            inputs=[ds_task_cbg, ds_namespace, ds_repo_name],
            outputs=ds_upload_msg,
        )
        datasets_demo.load(fn=load_datasets_page, outputs=[ds_task_cbg, ds_namespace])
        datasets_demo.load(fn=check_hf_auth_on_load, outputs=ds_auth_modal)

    # ══════════════════════════════════════════════════════════════════
    # Page 3 — Live View
    # ══════════════════════════════════════════════════════════════════

    with demo.route("Live View") as live_demo:
        gr.Navbar(main_page_name="Episodes")
        gr.Markdown("# GRABETTE")

        # ── Camera | 3D viewer | System cards ────────────────────────
        with gr.Row(equal_height=True):
            with gr.Column(scale=2):
                gr.HTML("<div style='font-size:0.72rem;text-transform:uppercase;"
                        "letter-spacing:0.09em;color:#94a3b8;margin-bottom:0.3rem;'>"
                        "Camera</div>")
                camera_img = gr.Image(
                    label=None, show_label=False, height="30vh", container=False,
                )
                gr.HTML("<div style='font-size:0.72rem;text-transform:uppercase;"
                        "letter-spacing:0.09em;color:#94a3b8;margin:0.5rem 0 0.3rem;'>"
                        "Depth (OAK-D)</div>")
                depth_img = gr.Image(
                    label=None, show_label=False, height="25vh", container=False,
                )
                oakd_btn = gr.Button("OAK-D: OFF  — click to enable", size="sm")
            with gr.Column(scale=2):
                gr.HTML("<div style='font-size:0.72rem;text-transform:uppercase;"
                        "letter-spacing:0.09em;color:#94a3b8;margin-bottom:0.3rem;'>"
                        "3D Model</div>")
                gr.HTML(
                    '<iframe id="urdf-viewer" src="/viewer" '
                    'style="width:100%;height:30vh;border:none;'
                    'border-radius:8px;background:#1a1a2e;"></iframe>'
                )
            with gr.Column(scale=1):
                gr.HTML("<div style='font-size:0.72rem;text-transform:uppercase;"
                        "letter-spacing:0.09em;color:#94a3b8;margin-bottom:0.3rem;'>"
                        "System</div>")
                dv_system_bar = gr.HTML()
                teleop_btn = gr.Button("Enter Teleop Mode", variant="secondary")
                teleop_msg = gr.Textbox(
                    show_label=False, interactive=False, max_lines=1,
                )

        # ── Sensor charts ─────────────────────────────────────────────
        gr.HTML("<hr style='margin:1.25rem 0;border:none;border-top:1px solid #1e293b;'>")
        with gr.Row():
            with gr.Column(scale=3):
                imu_box = gr.Markdown("## IMU Live")
                imu_iframe = gr.HTML(value=_IMU_IFRAME_HTML)
            with gr.Column(scale=2):
                angle_box = gr.Markdown("## Angle Sensors")
                angle_iframe = gr.HTML(value=_ANGLE_IFRAME_HTML)

        camera_timer = gr.Timer(0.2)
        camera_timer.tick(fn=get_camera_frame, outputs=camera_img)

        depth_timer = gr.Timer(0.2)
        depth_timer.tick(fn=get_depth_frame, outputs=depth_img)

        sensor_timer = gr.Timer(0.5)
        sensor_timer.tick(fn=get_sensor_state, outputs=[imu_box, angle_box])

        # Teleop status polled at 1 Hz on its own timer — kept off the
        # main sensor_timer to avoid HTTP backpressure that caused Markdown
        # flicker and WS-stream bursting in earlier revisions.
        teleop_timer = gr.Timer(1.0)
        teleop_timer.tick(fn=get_teleop_display, outputs=teleop_msg)

        # OAK-D toggle — slow poll (3 s) since the user is the only thing
        # that flips it, except for the auto-on-at-record path which also
        # only needs O(seconds) responsiveness.
        oakd_timer = gr.Timer(3.0)
        oakd_timer.tick(fn=poll_oakd, outputs=oakd_btn)
        oakd_btn.click(fn=on_toggle_oakd, outputs=oakd_btn)
        live_demo.load(fn=poll_oakd, outputs=oakd_btn)

        # Teleop mode toggle (wired here so the timer references resolve).
        # When teleop is ON: the live-view timers are paused so uvicorn
        # has headroom for the WS stream.
        teleop_btn.click(
            fn=on_toggle_teleop,
            outputs=[teleop_msg, teleop_btn,
                     camera_timer, depth_timer, sensor_timer, teleop_timer,
                     imu_iframe, angle_iframe],
        )

        dv_system_timer = gr.Timer(10)
        dv_system_timer.tick(fn=get_system_bar, outputs=dv_system_bar)

    # ══════════════════════════════════════════════════════════════════
    # Page 4 — Settings
    # ══════════════════════════════════════════════════════════════════

    with demo.route("Settings") as settings_demo:
        gr.Navbar(main_page_name="Episodes")
        gr.Markdown("# GRABETTE")

        with gr.Row(equal_height=False):

            # ── HuggingFace Account ───────────────────────────────────
            with gr.Column(scale=1):
                gr.Markdown("## HuggingFace Account")
                gr.Markdown("### Current status")
                hf_account_status = gr.Textbox(
                    label=None, container=False, interactive=False,
                )
                gr.Markdown("### Update Token")
                new_token_input = gr.Textbox(
                    label=None, container=False,
                    type="password", placeholder="hf_...",
                )
                with gr.Row():
                    update_token_btn = gr.Button("Save token", variant="primary", size="sm")
                    remove_token_btn = gr.Button("Remove current token", variant="stop", size="sm")
                account_msg = gr.Textbox(show_label=False, interactive=False, max_lines=1)

            # ── WiFi ─────────────────────────────────────────────────
            with gr.Column(scale=1):
                gr.Markdown("## WiFi")
                gr.HTML(_WIFI_SETTINGS_HTML)

        update_token_btn.click(
            fn=on_hf_update_token, inputs=new_token_input,
            outputs=[hf_account_status, new_token_input],
        )
        remove_token_btn.click(fn=on_hf_remove_token, outputs=hf_account_status)

        settings_demo.load(fn=check_hf_account, outputs=hf_account_status)

    return demo
