#!/usr/bin/env python3
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
API_TOKEN = os.environ.get("API_TOKEN", "")
NOTIFICATION_URL = os.environ.get("NOTIFICATION_URL", "http://minecraft.lackas.net/update")
ASSETS_DIR = Path("/app/assets")
PLIST_PATH = ASSETS_DIR / "com.soferio.minertimer_daily_timer.plist"
MINERTIMER_PATH = ASSETS_DIR / "minertimer.sh"
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
        <h3 class="{{ style }}{% if over %} over-limit{% endif %}">{{ user }}</h3>
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
        <a class="button" href="/increase?user={{ user }}&time={{ t * 60 + offset }}">+{{ t }}</a>
    {% endfor %}
    <a class="button stop" href="/increase?user={{ user }}&time={{ info.played if info.played > 0 else 1 }}&stop=1">Stop</a>
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


@app.get("/update/<user>/<date>/<int:played>/<int:client_max>")
def update(user: str, date: str, played: int, client_max: int):
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

    return str(current_max), 200, {"Content-Type": "text/plain"}


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


@app.get("/increase")
def increase():
    user = request.args.get("user")
    time_param = request.args.get("time")
    stop_flag = request.args.get("stop")

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

        return _render_dashboard(message)

    return _render_dashboard()


@app.post("/login")
def login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    users = _load_users()
    meta = users.get(username)
    if not meta or password != meta.get("password"):
        return _render_dashboard("Login failed")
    session.clear()
    session.permanent = True
    session["user"] = username
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
