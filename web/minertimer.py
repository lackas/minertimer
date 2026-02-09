#!/usr/bin/env python3
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
    Response,
    send_file,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("minertimer")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
)

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "db"
DB_DIR.mkdir(exist_ok=True)

PASSWORD_FILE = DB_DIR / "password"
DEFAULT_LIMIT_SECONDS = 30 * 60
INCREMENTS = [5, 15, 30, 60]
CLIENT_VERSION = "2"
API_TOKEN = os.environ.get("API_TOKEN", "")
NOTIFICATION_URL = os.environ.get("NOTIFICATION_URL", "https://minertimer.lackas.net/update")
ASSETS_DIR = Path("/app/assets")
PLIST_PATH = ASSETS_DIR / "com.soferio.minertimer_daily_timer.plist"
MINERTIMER_PATH = ASSETS_DIR / "minertimer.sh"
MINERTIMER_WIN_PATH = ASSETS_DIR / "minertimer.ps1"
TZ_NAME = os.environ.get("TIMEZONE", "Europe/Berlin")
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:
    TZ = ZoneInfo("UTC")

PLAYERS_TEMPLATE = """
{% for user, info in players.items() %}
    {% set last = info.last_minutes %}
    {% set style = 'active' if last is not none and last < 5 else 'inactive' %}
    {% set over = info.played >= info.max_time %}
    <div class="line-compact">
        <h3 class="{{ style }}{% if over %} over-limit{% endif %}"><a class="user-link" href="{{ url_for('user_stats', user=user) }}">{{ user }}</a></h3>
        <h4 class="{{ style }}{% if over %} over-limit{% endif %}">
            {{ (info.played // 60) }}/{{ (info.max_time // 60) }}m
            {% if last is not none %}({{ last }}m ago){% endif %}
            {% if over %}<span class="over-limit">Time used up</span>{% endif %}
        </h4>
    </div>
    {% if increments %}
    {% set offset = info.max_time %}
    <div class="buttons">
    {% for t in increments %}
        <button class="button" onclick="postIncrease('{{ user }}', {{ t * 60 + offset }})">+{{ t }}</button>
    {% endfor %}
    <button class="button stop" onclick="postIncrease('{{ user }}', {{ info.played if info.played > 0 else 1 }}, 1)">Stop</button>
    </div>
    {% endif %}
    <hr/>
{% endfor %}
"""


def _valid_user(user: str) -> bool:
    return bool(re.match(r"^\w+$", user))


def _valid_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _write_state(path: Path, played: int, max_time: int) -> None:
    with path.open("w") as fh:
        fh.write(f"{played}\n{max_time}\n")


def _read_state(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("r") as fh:
            played = int(fh.readline().strip())
            max_time = int(fh.readline().strip())
            return played, max_time
    except (FileNotFoundError, ValueError, OSError):
        return None


def _load_users() -> dict[str, dict]:
    users: dict[str, dict] = {}
    if not PASSWORD_FILE.exists():
        return users
    try:
        with PASSWORD_FILE.open("r") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) < 3:
                    continue
                name, password, role = parts[:3]
                default_time = parts[3] if len(parts) > 3 else ""
                if not _valid_user(name):
                    continue
                try:
                    default_limit = int(default_time) * 60 if default_time else DEFAULT_LIMIT_SECONDS
                except ValueError:
                    default_limit = DEFAULT_LIMIT_SECONDS
                users[name] = {
                    "password": password,
                    "role": role,
                    "default_limit": default_limit,
                }
    except OSError:
        return users
    return users


def _session_context(user_meta: dict[str, dict]) -> tuple[str | None, dict | None, bool]:
    current_user = session.get("user")
    current_meta = user_meta.get(current_user)
    if not current_meta:
        session.clear()
        return None, None, False
    role = current_meta.get("role")
    return current_user, current_meta, role == "admin"


def _consume_increase(path: Path) -> int | None:
    inc_path = Path(f"{path}.increase")
    try:
        with inc_path.open("r") as fh:
            value = int(fh.readline().strip())
        inc_path.unlink(missing_ok=True)
        return value
    except (FileNotFoundError, ValueError, OSError):
        inc_path.unlink(missing_ok=True)
        return None


def _now_local() -> datetime:
    return datetime.now(tz=TZ)


def _players_for_today(user_meta: dict, viewer_user: str | None, admin: bool) -> tuple[str, dict]:
    now = _now_local()
    today = now.strftime("%Y-%m-%d")
    players: dict[str, dict] = {}
    for name, meta in user_meta.items():
        if meta.get("role") == "admin":
            continue
        if not admin and viewer_user and name != viewer_user:
            continue
        default_limit = meta.get("default_limit", DEFAULT_LIMIT_SECONDS)
        players[name] = {
            "played": 0,
            "max_time": default_limit,
            "path": DB_DIR / f"{name}-{today}",
            "last_minutes": None,
        }

    for entry in DB_DIR.glob(f"*-{today}"):
        state = _read_state(entry)
        if not state:
            continue
        user = entry.name[: -(len(today) + 1)]
        meta = user_meta.get(user)
        if not meta or meta.get("role") == "admin":
            continue
        if not admin and viewer_user and user != viewer_user:
            continue
        played, max_time = state
        last_minutes = int((now.timestamp() - entry.stat().st_mtime) / 60)
        players[user] = {
            "played": played,
            "max_time": max_time,
            "path": entry,
            "last_minutes": last_minutes,
        }

    return today, players, list(user_meta.keys())


def _daily_stats(user: str, days: int = 30) -> list[dict]:
    end_date = _now_local().date()
    start_date = end_date - timedelta(days=days - 1)
    results: list[dict] = []
    for i in range(days):
        day = start_date + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        path = DB_DIR / f"{user}-{date_str}"
        state = _read_state(path)
        played = state[0] if state else 0
        results.append(
            {
                "date": date_str,
                "label": day.strftime("%b %d"),
                "seconds": played,
                "minutes": played // 60,
            }
        )
    return results


@app.get("/update/<user>/<date>/<int:played>/<int:client_max>")
def update(user: str, date: str, played: int, client_max: int):
    user = user.lower()
    # if API_TOKEN and request.headers.get("X-API-Token") != API_TOKEN:
    #     abort(401)
    if not (_valid_user(user) and _valid_date(date)):
        abort(400)
    if played < 0 or client_max <= 0:
        abort(400)

    path = DB_DIR / f"{user}-{date}"
    user_meta = _load_users()
    default_limit = user_meta.get(user, {}).get("default_limit", DEFAULT_LIMIT_SECONDS)
    current_state = _read_state(path)
    current_played = current_state[0] if current_state else 0
    current_max = current_state[1] if current_state else default_limit

    # don't allow decrease of played time
    played = max(played, current_played)

    # Ignore the client max; web UI is authoritative.
    _write_state(path, played, current_max)
    log.info("update: %s played=%dm/%dm", user, played // 60, current_max // 60)

    return str(current_max), 200, {"Content-Type": "text/plain"}


@app.get("/version")
def version():
    return CLIENT_VERSION, 200, {"Content-Type": "text/plain"}


def _render_dashboard(message: str | None = None):
    user_meta = _load_users()
    user_names = list(user_meta.keys())
    current_user, current_meta, is_admin = _session_context(user_meta)
    current_role = current_meta.get("role") if current_meta else None

    if current_user:
        today, players, _ = _players_for_today(
            user_meta=user_meta, viewer_user=current_user, admin=is_admin
        )
    else:
        today, players, _ = "", {}, []
    players_html = render_template_string(
        PLAYERS_TEMPLATE,
        players=players,
        increments=INCREMENTS if is_admin else [],
    )
    html = """
<!DOCTYPE html>
<html>
<head>
    <title>MinerTimer</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/skeleton/2.0.4/skeleton.min.css" />
    <style>
        .button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            margin: 5px;
            min-height: 42px;
            min-width: 60px;
            padding: 10px 16px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            text-decoration: none;
            outline: none;
            color: #fff;
            background-color: #6C7A89;
            border: none;
            border-radius: 999px;
            box-shadow: 0 9px #999;
        }
        .button:hover {background-color: #3E5060; color: #f5a623;}
        .button.stop {
            background-color: #c0392b;
            box-shadow: 0 9px #922b21;
        }
        .button.stop:hover { background-color: #a93226; color: #fff; }
        .button.login { background-color: #2980b9; box-shadow: 0 9px #1f618d; }
        .button.logout { background-color: #7f8c8d; box-shadow: 0 9px #707b7c; }
        .active { color: green; }
        .inactive { color: gray; }
        .over-limit { color: #c0392b; }
        .container {
            width: 90%;
            max-width: 600px;
            margin: 0 auto;
            padding-top: 20px;
        }
        .buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin: 8px 0;
        }
        .login-box {
            margin-top: 12px;
            padding: 8px 0;
        }
        select, input[type="password"] {
            padding: 8px 10px;
            font-size: 14px;
            border-radius: 6px;
            border: 1px solid #ccc;
            box-sizing: border-box;
        }
        .login-status {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
            margin: 6px 0;
        }
        .row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .user-line {
            display: flex;
            flex-direction: column;
            flex: 1;
        }
        .line-compact {
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
            font-size: 16px;
            margin: 6px 0;
        }
        .line-compact h3, .line-compact h4 {
            margin: 0;
        }
        .user-link {
            color: inherit;
            text-decoration: none;
        }
        .date-badge {
            position: fixed;
            right: 12px;
            bottom: 12px;
            padding: 6px 10px;
            background: rgba(255, 255, 255, 0.85);
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
            font-weight: 600;
            color: #555;
        }
    </style>
</head>
<body>
<div class="container">
<h1>MinerTimer</h1>
{% if message %}<h3>{{ message }}</h3><hr/>{% endif %}
<div id="players">{{ players_html|safe }}</div>
<div class="login-box">
    <div class="login-status">
        {% if current_user %}
            <div>Logged in as <strong>{{ current_user }}</strong> ({{ current_role }})</div>
            {% if current_role == 'admin' %}<a class="button" href="{{ url_for('setup_guide') }}" style="background-color:#27ae60; box-shadow:0 9px #1e8449;">Setup</a>{% endif %}
            <a class="button logout" href="{{ url_for('logout') }}">Logout</a>
        {% else %}
            <form method="post" action="{{ url_for('login') }}" style="width: 100%; display: flex; gap: 6px; flex-wrap: wrap; align-items: center;">
                <select id="username" name="username" style="flex: 1 1 40%; min-width: 120px;">
                    {% for name in user_names %}
                    <option value="{{ name }}">{{ name }}</option>
                    {% endfor %}
                </select>
                <input id="password" name="password" type="password" inputmode="text" autocomplete="current-password" placeholder="Password" style="flex: 1 1 40%; min-width: 140px;" />
                <button class="button login" type="submit" style="flex: 0 0 auto; padding: 10px 12px;">Login</button>
            </form>
        {% endif %}
    </div>
</div>
</div>
<script>
const playersDiv = document.getElementById('players');
async function refreshPlayers() {
    try {
        const res = await fetch('/players', {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        if (!res.ok) return;
        const html = await res.text();
        playersDiv.innerHTML = html;
    } catch (e) {
        // ignore transient errors
    }
}
setInterval(refreshPlayers, 10000);

function postIncrease(user, time, stop=0) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/increase';

    const userField = document.createElement('input');
    userField.type = 'hidden';
    userField.name = 'user';
    userField.value = user;
    form.appendChild(userField);

    const timeField = document.createElement('input');
    timeField.type = 'hidden';
    timeField.name = 'time';
    timeField.value = time;
    form.appendChild(timeField);

    if (stop) {
        const stopField = document.createElement('input');
        stopField.type = 'hidden';
        stopField.name = 'stop';
        stopField.value = stop;
        form.appendChild(stopField);
    }

    document.body.appendChild(form);
    form.submit();
}
</script>
<div class="date-badge">{{ current_date }}</div>
</body>
</html>
"""
    return render_template_string(
        html,
        players_html=players_html,
        message=message,
        increments=INCREMENTS if is_admin else [],
        user_names=user_names,
        current_user=current_user,
        current_role=current_role,
        notification_url=NOTIFICATION_URL,
        current_date=_now_local().strftime("%Y-%m-%d"),
    )


@app.get("/")
def home():
    return _render_dashboard()


@app.route("/increase", methods=["POST"])
def increase():
    user = request.form.get("user")
    time_param = request.form.get("time")
    stop_flag = request.form.get("stop")

    if user and time_param:
        user_meta = _load_users()
        _, _, is_admin = _session_context(user_meta)
        if not is_admin:
            abort(403)
        if user not in user_meta:
            abort(400)
        if not _valid_user(user):
            abort(400)
        try:
            new_total = int(time_param)
        except (TypeError, ValueError):
            abort(400)
        if new_total <= 0 and not stop_flag:
            abort(400)

        date_str = _now_local().strftime("%Y-%m-%d")
        path = DB_DIR / f"{user}-{date_str}"
        current_state = _read_state(path)
        current_played = current_state[0] if current_state else 0

        if stop_flag:
            new_max = max(current_played, 0)
            message = f"Removed extra time for {user}"
        else:
            new_max = new_total
            message = f"Increased time for {user} to {new_max // 60}min"

        _write_state(path, current_played, new_max)
        log.info("increase: %s max=%dm by %s%s", user, new_max // 60, session.get("user"), " (stop)" if stop_flag else "")

        return _render_dashboard(message)

    return _render_dashboard()


@app.post("/login")
def login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    users = _load_users()
    meta = users.get(username)
    if not meta or password != meta.get("password"):
        log.info("login failed: %s", username)
        return _render_dashboard("Login failed")
    session.clear()
    session.permanent = True
    session["user"] = username
    log.info("login: %s (%s)", username, meta.get("role"))
    return redirect(url_for("home"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


def _require_admin_or_401() -> dict:
    user_meta = _load_users()
    header_token = request.headers.get("X-API-Token")
    if API_TOKEN and header_token == API_TOKEN:
        return user_meta
    _, meta, is_admin = _session_context(user_meta)
    if not is_admin:
        abort(403)
    return user_meta


@app.get("/install/minertimer.sh")
def download_minertimer():
    if not MINERTIMER_PATH.exists():
        abort(500)
    return send_file(
        MINERTIMER_PATH,
        mimetype="text/plain",
        as_attachment=True,
        download_name="minertimer.sh",
    )


@app.get("/install")
def install_script():
    _require_admin_or_401()
    api_token = API_TOKEN
    notif_url = NOTIFICATION_URL
    try:
        plist_content = PLIST_PATH.read_text()
    except OSError:
        plist_content = ""

    try:
        minertimer_content = MINERTIMER_PATH.read_text()
    except OSError:
        abort(500)

    try:
        template = (ASSETS_DIR / "setup-template.sh").read_text()
    except OSError:
        abort(500)

    script = (
        template.replace("__MINERTIMER_CONTENT__", minertimer_content)
        .replace("__API_TOKEN__", api_token)
        .replace("__NOTIFICATION_URL__", notif_url)
        .replace("__PLIST_CONTENT__", plist_content)
    )

    return Response(
        script,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=setup.txt"},
    )


@app.get("/install/minertimer.ps1")
def download_minertimer_win():
    if not MINERTIMER_WIN_PATH.exists():
        abort(500)
    return send_file(
        MINERTIMER_WIN_PATH,
        mimetype="text/plain",
        as_attachment=True,
        download_name="minertimer.ps1",
    )


@app.get("/install/win")
def install_script_win():
    _require_admin_or_401()
    api_token = API_TOKEN
    notif_url = NOTIFICATION_URL

    try:
        minertimer_content = MINERTIMER_WIN_PATH.read_text()
    except OSError:
        abort(500)

    try:
        template = (ASSETS_DIR / "setup-template-win.ps1").read_text()
    except OSError:
        abort(500)

    script = (
        template.replace("__MINERTIMER_CONTENT__", minertimer_content)
        .replace("__API_TOKEN__", api_token)
        .replace("__NOTIFICATION_URL__", notif_url)
    )

    return Response(
        script,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=setup-win.ps1"},
    )


@app.get("/setup")
def setup_guide():
    user_meta = _load_users()
    _, _, is_admin = _session_context(user_meta)
    if not is_admin:
        abort(403)

    notif_url = NOTIFICATION_URL
    base_url = notif_url.rsplit("/update", 1)[0] if "/update" in notif_url else notif_url

    html = """
<!DOCTYPE html>
<html>
<head>
    <title>MinerTimer Setup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/skeleton/2.0.4/skeleton.min.css" />
    <style>
        body { padding: 18px; }
        .container { width: 90%; max-width: 700px; margin: 0 auto; }
        .button {
            display: inline-flex; align-items: center; justify-content: center;
            margin: 5px; min-height: 42px; padding: 10px 16px;
            font-size: 16px; font-weight: 600; cursor: pointer;
            text-decoration: none; color: #fff; background-color: #6C7A89;
            border: none; border-radius: 999px; box-shadow: 0 9px #999;
        }
        .button:hover { background-color: #3E5060; color: #f5a623; }
        .button.dl { background-color: #2980b9; box-shadow: 0 9px #1f618d; }
        .button.dl:hover { background-color: #2471a3; color: #fff; }
        .button.ext { background-color: #8e44ad; box-shadow: 0 9px #6c3483; }
        .button.ext:hover { background-color: #7d3c98; color: #fff; }
        .link-back { text-decoration: none; }
        .section { margin: 24px 0; padding: 16px; background: #f8f9fa; border-radius: 10px; }
        .section h4 { margin-top: 0; }
        code { background: #eee; padding: 2px 6px; border-radius: 4px; font-size: 14px; }
        pre { background: #2c3e50; color: #ecf0f1; padding: 14px; border-radius: 8px;
              overflow-x: auto; font-size: 13px; line-height: 1.5; }
        .step { margin: 10px 0; padding-left: 8px; }
        .step-num { display: inline-block; width: 24px; height: 24px; line-height: 24px;
                    text-align: center; background: #2980b9; color: #fff; border-radius: 50%;
                    font-size: 13px; font-weight: 700; margin-right: 6px; }
        .downloads { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
        .note { background: #fef9e7; border-left: 4px solid #f39c12; padding: 10px 14px;
                border-radius: 4px; margin: 10px 0; font-size: 14px; }
        hr { margin: 20px 0; }
    </style>
</head>
<body>
<div class="container">
    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
        <h2 style="margin: 0;">MinerTimer Setup</h2>
        <a class="button link-back" href="{{ url_for('home') }}">Back</a>
    </div>
    <hr/>

    <!-- ===== macOS ===== -->
    <div class="section">
        <h3>macOS</h3>
        <h4>Quick Install (recommended)</h4>
        <div class="downloads">
            <a class="button dl" href="{{ url_for('install_script') }}">Download macOS Installer</a>
        </div>
        <p>Run the downloaded file in Terminal:</p>
        <pre>sudo bash ~/Downloads/setup.txt</pre>
        <p>This installs the daemon, configures the API token, and starts monitoring automatically.</p>

        <h4>Manual Install</h4>
        <div class="step"><span class="step-num">1</span> Download the client script:</div>
        <div class="downloads">
            <a class="button dl" href="{{ url_for('download_minertimer') }}">Download minertimer.sh</a>
        </div>
        <div class="step"><span class="step-num">2</span> Copy to <code>/Users/Shared/minertimer/</code> and create <code>.env</code>:</div>
        <pre>sudo mkdir -p /Users/Shared/minertimer
sudo cp minertimer.sh /Users/Shared/minertimer/
sudo chmod +x /Users/Shared/minertimer/minertimer.sh

sudo tee /Users/Shared/minertimer/.env > /dev/null &lt;&lt;EOF
API_TOKEN={{ api_token }}
NOTIFICATION_URL={{ notification_url }}
EOF
sudo chmod 600 /Users/Shared/minertimer/.env</pre>
        <div class="step"><span class="step-num">3</span> Register as LaunchDaemon (starts on boot).</div>

        <h4>Verify</h4>
        <pre>sudo launchctl list | grep com.soferio.minertimer_daily_timer</pre>
    </div>

    <!-- ===== Windows ===== -->
    <div class="section">
        <h3>Windows</h3>
        <h4>Quick Install (recommended)</h4>
        <div class="downloads">
            <a class="button dl" href="{{ url_for('install_script_win') }}">Download Windows Installer</a>
        </div>
        <p>Run the downloaded file in an <strong>Administrator PowerShell</strong>:</p>
        <pre>Set-ExecutionPolicy Bypass -Scope Process -Force
&amp; "$env:USERPROFILE\\Downloads\\setup-win.ps1"</pre>
        <p>This installs the script to <code>C:\\ProgramData\\minertimer</code> and registers a Scheduled Task that runs at startup.</p>

        <h4>Manual Install with NSSM (Windows Service)</h4>
        <div class="note">
            NSSM (Non-Sucking Service Manager) creates a proper Windows Service with
            auto-restart and log rotation. It needs to be installed separately.
        </div>
        <div class="downloads">
            <a class="button ext" href="https://nssm.cc/release/nssm-2.24.zip" target="_blank" rel="noopener">Download NSSM (nssm.cc)</a>
            <a class="button dl" href="{{ url_for('download_minertimer_win') }}">Download minertimer.ps1</a>
        </div>
        <div class="step"><span class="step-num">1</span> Download and extract <strong>nssm.exe</strong> from the ZIP (use the <code>win64</code> folder).</div>
        <div class="step"><span class="step-num">2</span> Place <code>nssm.exe</code> somewhere permanent (e.g. <code>C:\\tools\\nssm.exe</code>).</div>
        <div class="step"><span class="step-num">3</span> Copy <code>minertimer.ps1</code> to <code>C:\\ProgramData\\minertimer\\</code>:</div>
        <pre>New-Item -ItemType Directory -Path "C:\\ProgramData\\minertimer" -Force
Copy-Item minertimer.ps1 "C:\\ProgramData\\minertimer\\"</pre>
        <div class="step"><span class="step-num">4</span> Create the config file <code>C:\\ProgramData\\minertimer\\.env</code>:</div>
        <pre>@"
API_TOKEN={{ api_token }}
NOTIFICATION_URL={{ notification_url }}
TIME_LIMIT_DEFAULT=1800
"@ | Set-Content "C:\\ProgramData\\minertimer\\.env"</pre>
        <div class="step"><span class="step-num">5</span> Install as a Windows Service using NSSM:</div>
        <pre>C:\\tools\\nssm.exe install MinerTimer powershell.exe "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\\ProgramData\\minertimer\\minertimer.ps1"
C:\\tools\\nssm.exe set MinerTimer DisplayName "MinerTimer"
C:\\tools\\nssm.exe set MinerTimer Start SERVICE_AUTO_START
C:\\tools\\nssm.exe set MinerTimer AppExit Default Restart
C:\\tools\\nssm.exe start MinerTimer</pre>

        <h4>Verify</h4>
        <pre># Scheduled Task:
Get-ScheduledTask -TaskName MinerTimer

# NSSM Service:
Get-Service MinerTimer</pre>
    </div>

    <!-- ===== Uninstall ===== -->
    <div class="section">
        <h3>Uninstall</h3>
        <h4>macOS</h4>
        <pre>sudo launchctl bootout system/com.soferio.minertimer_daily_timer
sudo rm -rf /Users/Shared/minertimer
sudo rm /Library/LaunchDaemons/com.soferio.minertimer_daily_timer.plist</pre>
        <h4>Windows (Scheduled Task)</h4>
        <pre>Unregister-ScheduledTask -TaskName MinerTimer -Confirm:$false
Remove-Item "C:\\ProgramData\\minertimer" -Recurse -Force</pre>
        <h4>Windows (NSSM Service)</h4>
        <pre>nssm stop MinerTimer
nssm remove MinerTimer confirm
Remove-Item "C:\\ProgramData\\minertimer" -Recurse -Force</pre>
    </div>
</div>
</body>
</html>
"""
    return render_template_string(
        html,
        api_token=API_TOKEN,
        notification_url=NOTIFICATION_URL,
    )


@app.get("/user/<user>")
def user_stats(user: str):
    if not _valid_user(user):
        abort(404)
    user_meta = _load_users()
    current_user, _, is_admin = _session_context(user_meta)
    if not current_user:
        abort(403)
    meta = user_meta.get(user)
    if not meta or meta.get("role") == "admin":
        abort(404)
    if not is_admin and current_user != user:
        abort(403)

    stats = _daily_stats(user, days=30)
    avg_minutes = (sum(d["minutes"] for d in stats) / len(stats)) if stats else 0
    max_minutes = max((d["minutes"] for d in stats), default=0)
    scale = max_minutes if max_minutes > 0 else 1
    html = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ user }} stats</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/skeleton/2.0.4/skeleton.min.css" />
    <style>
        body { padding: 18px; }
        .chart {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(48px, 1fr));
            gap: 10px;
            align-items: end;
            margin-top: 20px;
        }
        .bar-wrap {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
        }
        .bar {
            width: 100%;
            max-width: 46px;
            background: linear-gradient(180deg, #5dade2 0%, #2e86c1 100%);
            border-radius: 6px 6px 4px 4px;
            display: flex;
            align-items: flex-end;
            justify-content: center;
            color: #fff;
            font-size: 12px;
            font-weight: 700;
            padding: 4px 0;
            box-shadow: 0 2px 6px rgba(0,0,0,0.15);
        }
        .bar-value {
            padding: 2px 4px;
        }
        .bar-label {
            font-size: 11px;
            text-align: center;
            color: #555;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        .meta {
            color: #555;
            font-size: 14px;
        }
        .link-back {
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h3 style="margin: 0;">{{ user }} last 30 days</h3>
            <div class="meta">Avg: {{ "%.1f"|format(avg_minutes) }} minutes/day</div>
        </div>
        <a class="button link-back" href="{{ url_for('home') }}">Back</a>
    </div>
    <div class="chart">
        {% for d in stats %}
        <div class="bar-wrap">
            <div class="bar" style="height: {{ 12 + (d.minutes / scale) * 180 }}px;" title="{{ d.date }}: {{ d.minutes }}m">
                <span class="bar-value">{{ d.minutes }}</span>
            </div>
            <div class="bar-label">{{ d.label }}</div>
        </div>
        {% endfor %}
    </div>
</body>
</html>
    """
    return render_template_string(
        html,
        user=user,
        stats=stats,
        avg_minutes=avg_minutes,
        scale=scale,
    )


@app.get("/players")
def players_partial():
    user_meta = _load_users()
    current_user, current_meta, is_admin = _session_context(user_meta)
    if not current_user:
        abort(403)
    today, players, _ = _players_for_today(
        user_meta=user_meta, viewer_user=current_user, admin=is_admin
    )
    html = render_template_string(
        PLAYERS_TEMPLATE,
        players=players,
        increments=INCREMENTS if is_admin else [],
    )
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
