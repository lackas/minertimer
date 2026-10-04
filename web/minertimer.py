#!/usr/bin/env python3
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
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
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
)

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "db"
DB_DIR.mkdir(exist_ok=True)
USER_CONFIG_DIR = DB_DIR / "users"
USER_CONFIG_DIR.mkdir(exist_ok=True)
CLIENT_INFO_DIR = DB_DIR / "clients"
CLIENT_INFO_DIR.mkdir(exist_ok=True)

PASSWORD_FILE = DB_DIR / "password"
DEFAULT_LIMIT_SECONDS = 30 * 60
INCREMENTS = [5, 15, 30, 60]
CLIENT_VERSION = "5"
# A machine that has not reported for this long is shown with its date instead
# of an "update pending" flag: it is idle, not broken.
CLIENT_RECENT_HOURS = 48
CLIENT_FORGET_DAYS = 30
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
    {% for machine in info.clients %}
    <div class="client-info">
        Client {{ machine.version }}{% if machine.platform %} ({{ machine.platform }}{% if machine.host %} &middot; {{ machine.host }}{% endif %}){% endif %}
        {%- if not machine.recent %}
        <span class="client-old">last seen {{ machine.last_seen_label }}</span>
        {%- elif client_version and machine.version != client_version %}
        <span class="client-stale">&ne; {{ client_version }} on server, update pending</span>
        {%- endif %}
    </div>
    {% endfor %}
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


def _safe_header(value: str | None, pattern: str = r"^[\w.:+-]{1,40}$") -> str | None:
    """Whitelist a client-supplied header value, or drop it."""
    if value and re.match(pattern, value):
        return value
    return None


def _client_machine_key(platform: str | None, host: str | None) -> str:
    base = platform or "unknown"
    return f"{base}:{host}" if host else base


def _parse_last_seen(machine: dict) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(machine.get("last_seen", "")))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)


def _load_machines(path: Path) -> dict[str, dict]:
    """Read the stored machines, accepting the single-slot format of client 4."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    machines = data.get("machines")
    if isinstance(machines, dict):
        return {k: v for k, v in machines.items() if isinstance(v, dict)}
    if data.get("version"):
        return {_client_machine_key(data.get("platform"), None): data}
    return {}


def _record_client_info(
    user: str, version: str | None, platform: str | None, host: str | None = None
) -> None:
    """Remember which client version each of a user's machines reported.

    /update has no authentication (the token check is commented out), so every
    header here is attacker-controlled: each value is whitelisted to a short,
    harmless character set before it is stored or ever rendered.

    One record per machine, not one per user: David plays mostly on the gaming
    PC and now and then on the Mac, and a single slot would simply show
    whichever machine reported last. The other machine's version would be
    invisible — including a Mac that quietly stopped updating itself.
    """
    version = _safe_header(version, r"^[\w.+-]{1,20}$")
    if not version:
        return
    platform = _safe_header(platform, r"^[a-z]{1,16}$")
    host = _safe_header(host, r"^[\w.-]{1,32}$")
    now = _now_local()
    path = CLIENT_INFO_DIR / f"{user}.json"
    machines = _load_machines(path)
    if host:
        # Client 4 and older sent no host, so their record is keyed by platform
        # alone. The first report from the same platform *with* a host is that
        # very machine after its update — drop the hostless record instead of
        # listing the Mac twice, once as a phantom still "update pending".
        machines.pop(_client_machine_key(platform, None), None)
    machines[_client_machine_key(platform, host)] = {
        "version": version,
        "platform": platform or "",
        "host": host or "",
        "last_seen": now.isoformat(timespec="seconds"),
    }
    # Forget a machine nobody has played on for a month, so a reinstalled or
    # retired box does not sit in the dashboard forever.
    cutoff = now - timedelta(days=CLIENT_FORGET_DAYS)
    machines = {
        key: entry
        for key, entry in machines.items()
        if (_parse_last_seen(entry) or now) >= cutoff
    }
    try:
        path.write_text(json.dumps({"machines": machines}), encoding="utf-8")
    except OSError as exc:
        log.warning("could not store client info for %s: %s", user, exc)


def _read_client_info(user: str) -> list[dict]:
    """All machines known for a user, most recently seen first."""
    now = _now_local()
    machines = []
    for entry in _load_machines(CLIENT_INFO_DIR / f"{user}.json").values():
        last_seen = _parse_last_seen(entry)
        machines.append(
            {
                "version": str(entry.get("version", "")),
                "platform": str(entry.get("platform", "")),
                "host": str(entry.get("host", "")),
                "last_seen": last_seen,
                "last_seen_label": last_seen.strftime("%d.%m. %H:%M") if last_seen else "?",
                "recent": bool(
                    last_seen and now - last_seen <= timedelta(hours=CLIENT_RECENT_HOURS)
                ),
            }
        )
    machines.sort(
        key=lambda machine: machine["last_seen"] or datetime.min.replace(tzinfo=TZ),
        reverse=True,
    )
    return machines


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
                user_config = _load_user_config(name)
                users[name] = {
                    "password": password,
                    "role": role,
                    "base_default_limit": default_limit,
                    "default_limit": default_limit,
                    "config": user_config,
                }
    except OSError:
        return users
    return users


def _load_user_config(user: str) -> dict:
    path = USER_CONFIG_DIR / f"{user}.json"
    if not path.exists():
        return {}
    try:
        with path.open("r") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        log.warning("invalid user config: %s", path)
        return {}


def _save_user_config(user: str, config: dict) -> None:
    path = USER_CONFIG_DIR / f"{user}.json"
    with path.open("w") as fh:
        json.dump(config, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _date_override(meta: dict, day: date) -> dict:
    overrides = meta.get("config", {}).get("overrides", {})
    dates = overrides.get("dates", {})
    override = dates.get(day.isoformat(), {})
    return override if isinstance(override, dict) else {}


def _default_limit_for_day(meta: dict, day: date) -> int:
    base_default = meta.get("base_default_limit", meta.get("default_limit", DEFAULT_LIMIT_SECONDS))
    config = meta.get("config", {})
    rules = config.get("rules", {})
    flags = config.get("flags", {})
    schedule = rules.get("schedule", {})
    exam_mode = rules.get("exam_mode", {})
    override = _date_override(meta, day)

    weekday = day.weekday()
    if weekday <= 3:
        scheduled_minutes = schedule.get("mon_thu_minutes")
    elif weekday <= 6:
        scheduled_minutes = schedule.get("fri_sun_minutes")
    else:
        scheduled_minutes = None
    if "limit_minutes" in override:
        scheduled_minutes = override.get("limit_minutes")

    try:
        limit_seconds = int(scheduled_minutes) * 60 if scheduled_minutes is not None else int(base_default)
    except (TypeError, ValueError):
        limit_seconds = int(base_default)

    if exam_mode.get("active") or flags.get("learning_time"):
        try:
            reduction_seconds = int(exam_mode.get("reduction_minutes", 0)) * 60
        except (TypeError, ValueError):
            reduction_seconds = 0
        limit_seconds = max(0, limit_seconds - reduction_seconds)

    return limit_seconds


def _curfew_deadline_for_day(meta: dict, day: date) -> datetime | None:
    rules = meta.get("config", {}).get("rules", {})
    curfew = rules.get("curfew", {})
    override = _date_override(meta, day)
    if override.get("disable_curfew"):
        return None
    if "curfew" in override:
        curfew_value = override.get("curfew")
    else:
        weekday = day.weekday()
        if weekday in (4, 5):
            curfew_value = curfew.get("fri_sat", curfew.get("weekend"))
        else:
            curfew_value = curfew.get("school_nights", curfew.get("weekdays"))
    if not curfew_value:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(curfew_value))
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if minute > 59 or hour > 24 or (hour == 24 and minute != 0):
        return None
    deadline_day = day + timedelta(days=1) if hour == 24 else day
    deadline_hour = 0 if hour == 24 else hour
    return datetime(
        deadline_day.year,
        deadline_day.month,
        deadline_day.day,
        deadline_hour,
        minute,
        tzinfo=TZ,
    )


def _apply_curfew(meta: dict, day: date, played: int, max_time: int, now: datetime | None = None) -> int:
    deadline = _curfew_deadline_for_day(meta, day)
    if not deadline:
        return max_time
    current_time = now or _now_local()
    if current_time < deadline:
        return max_time
    return min(max_time, played)


def _valid_curfew_value(value: str) -> bool:
    match = re.match(r"^(\d{1,2}):(\d{2})$", value)
    if not match:
        return False
    hour = int(match.group(1))
    minute = int(match.group(2))
    return minute <= 59 and hour <= 24 and not (hour == 24 and minute != 0)


def _session_context(user_meta: dict[str, dict]) -> tuple[str | None, dict | None, bool]:
    session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    has_session_cookie = bool(request.cookies.get(session_cookie_name))
    current_user = session.get("user")
    if has_session_cookie and not current_user:
        log.info(
            "session missing user: host=%s remote=%s ua=%s",
            request.host,
            request.remote_addr,
            request.user_agent.string,
        )
        return None, None, False
    current_meta = user_meta.get(current_user)
    if not current_meta:
        log.info(
            "session invalidated for missing user: user=%s host=%s remote=%s",
            current_user,
            request.host,
            request.remote_addr,
        )
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
    today_date = now.date()
    today = now.strftime("%Y-%m-%d")
    players: dict[str, dict] = {}
    for name, meta in user_meta.items():
        if meta.get("role") == "admin":
            continue
        if not admin and viewer_user and name != viewer_user:
            continue
        default_limit = _default_limit_for_day(meta, today_date)
        default_limit = _apply_curfew(meta, today_date, 0, default_limit, now)
        players[name] = {
            "played": 0,
            "max_time": default_limit,
            "path": DB_DIR / f"{name}-{today}",
            "last_minutes": None,
            "clients": _read_client_info(name),
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
        max_time = _apply_curfew(meta, today_date, played, max_time, now)
        last_minutes = int((now.timestamp() - entry.stat().st_mtime) / 60)
        players[user] = {
            "played": played,
            "max_time": max_time,
            "path": entry,
            "last_minutes": last_minutes,
            "clients": _read_client_info(user),
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
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        abort(400)
    default_limit = _default_limit_for_day(user_meta.get(user, {}), day)
    current_state = _read_state(path)
    current_played = current_state[0] if current_state else 0
    current_max = current_state[1] if current_state else default_limit
    effective_max = _apply_curfew(user_meta.get(user, {}), day, current_played, current_max)
    if effective_max != current_max:
        log.info("curfew applied: %s date=%s played=%dm max=%dm -> %dm", user, date, current_played // 60, current_max // 60, effective_max // 60)
    current_max = effective_max

    # don't allow decrease of played time
    played = max(played, current_played)

    # Ignore the client max; web UI is authoritative.
    _write_state(path, played, current_max)
    _record_client_info(
        user,
        request.headers.get("X-Client-Version"),
        request.headers.get("X-Client-Platform"),
        request.headers.get("X-Client-Host"),
    )
    log.info("update: %s played=%dm/%dm", user, played // 60, current_max // 60)

    return str(current_max), 200, {"Content-Type": "text/plain"}


@app.get("/version")
def version():
    # Logged because this is the only sign of life from a machine nobody is
    # playing on: the dashboard can only show what /update reports, and since
    # client 5 the update check runs hourly on every awake machine. Grep the
    # log to see who checked in and with which version.
    log.info(
        "version check: client=%s platform=%s host=%s",
        _safe_header(request.headers.get("X-Client-Version"), r"^[\w.+-]{1,20}$"),
        _safe_header(request.headers.get("X-Client-Platform"), r"^[a-z]{1,16}$"),
        _safe_header(request.headers.get("X-Client-Host"), r"^[\w.-]{1,32}$"),
    )
    return CLIENT_VERSION, 200, {"Content-Type": "text/plain"}


def _render_dashboard(message: str | None = None):
    if message is None:
        message = session.pop("flash_message", None)
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
        client_version=CLIENT_VERSION,
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
        .client-info { font-size: 13px; color: #7f8c8d; margin: -8px 0 6px; }
        .client-stale { color: #c0392b; }
        .client-old { color: #95a5a6; }
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


@app.get("/increase")
def increase_get():
    return redirect(url_for("home"), code=303)


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

        session["flash_message"] = message

    return redirect(url_for("home"), code=303)


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
    log.info(
        "login: %s (%s) host=%s remote=%s ua=%s",
        username,
        meta.get("role"),
        request.host,
        request.remote_addr,
        request.user_agent.string,
    )
    return redirect(url_for("home"))


@app.get("/logout")
def logout():
    log.info(
        "logout: user=%s host=%s remote=%s",
        session.get("user"),
        request.host,
        request.remote_addr,
    )
    session.clear()
    return redirect(url_for("home"))


def _require_admin_or_401() -> dict:
    user_meta = _load_users()
    header_token = request.headers.get("X-API-Token")
    if API_TOKEN and header_token == API_TOKEN:
        return user_meta
    _, meta, is_admin = _session_context(user_meta)
    if not is_admin:
        session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        log.info(
            "admin auth failed: host=%s remote=%s cookie_present=%s user=%s",
            request.host,
            request.remote_addr,
            bool(request.cookies.get(session_cookie_name)),
            session.get("user"),
        )
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


@app.post("/user/<user>/config")
def update_user_config(user: str):
    if not _valid_user(user):
        abort(404)
    user_meta = _load_users()
    _, _, is_admin = _session_context(user_meta)
    if not is_admin:
        abort(403)
    meta = user_meta.get(user)
    if not meta or meta.get("role") == "admin":
        abort(404)

    config = meta.get("config", {})
    if not isinstance(config, dict):
        config = {}
    action = request.form.get("action", "")

    if action == "set_learning_time":
        flags = config.setdefault("flags", {})
        flags["learning_time"] = request.form.get("learning_time") == "on"
        _save_user_config(user, config)
        log.info("user config updated: %s learning_time=%s by %s", user, flags["learning_time"], session.get("user"))
        return redirect(url_for("user_stats", user=user, message="Lernzeit gespeichert"))

    if action == "save_override":
        date_str = request.form.get("override_date", "").strip()
        if not _valid_date(date_str):
            return redirect(url_for("user_stats", user=user, message="Ungueltiges Datum"))
        limit_minutes_raw = request.form.get("limit_minutes", "").strip()
        curfew_raw = request.form.get("curfew", "").strip()
        disable_curfew = request.form.get("disable_curfew") == "on"
        notes_raw = request.form.get("notes", "").strip()

        override: dict[str, object] = {}
        if limit_minutes_raw:
            try:
                limit_minutes = int(limit_minutes_raw)
                if limit_minutes < 0:
                    raise ValueError
                override["limit_minutes"] = limit_minutes
            except ValueError:
                return redirect(url_for("user_stats", user=user, message="Ungueltiges Tageslimit"))
        if curfew_raw:
            if not _valid_curfew_value(curfew_raw):
                return redirect(url_for("user_stats", user=user, message="Ungueltige Sperrzeit"))
            override["curfew"] = curfew_raw
        if disable_curfew:
            override["disable_curfew"] = True
        if notes_raw:
            override["notes"] = notes_raw

        overrides = config.setdefault("overrides", {})
        dates = overrides.setdefault("dates", {})
        if override:
            dates[date_str] = override
        else:
            dates.pop(date_str, None)
        _save_user_config(user, config)
        log.info("user config updated: %s override=%s by %s", user, date_str, session.get("user"))
        return redirect(url_for("user_stats", user=user, message="Tages-Override gespeichert"))

    if action == "clear_override":
        date_str = request.form.get("override_date", "").strip()
        overrides = config.get("overrides", {})
        dates = overrides.get("dates", {})
        if date_str in dates:
            del dates[date_str]
            if not dates:
                overrides.pop("dates", None)
            if not overrides:
                config.pop("overrides", None)
            _save_user_config(user, config)
            log.info("user config updated: %s override cleared=%s by %s", user, date_str, session.get("user"))
        return redirect(url_for("user_stats", user=user, message="Tages-Override entfernt"))

    return redirect(url_for("user_stats", user=user, message="Unbekannte Aktion"))


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
    config = meta.get("config", {})
    flags = config.get("flags", {})
    overrides = config.get("overrides", {}).get("dates", {})
    today = _now_local().date()
    override_items = []
    for date_str in sorted(overrides):
        override = overrides.get(date_str, {})
        if not isinstance(override, dict):
            continue
        try:
            if date.fromisoformat(date_str) < today:
                continue
        except ValueError:
            continue
        override_items.append(
            {
                "date": date_str,
                "limit_minutes": override.get("limit_minutes", ""),
                "curfew": override.get("curfew", ""),
                "disable_curfew": bool(override.get("disable_curfew")),
                "notes": override.get("notes", ""),
            }
        )
    today_limit_minutes = _default_limit_for_day(meta, today) // 60
    today_override = _date_override(meta, today)
    if today_override.get("disable_curfew"):
        today_curfew_label = "aus"
    elif today_override.get("curfew"):
        today_curfew_label = str(today_override.get("curfew"))
    else:
        weekday = today.weekday()
        curfew_rules = config.get("rules", {}).get("curfew", {})
        if weekday in (4, 5):
            today_curfew_label = str(curfew_rules.get("fri_sat", curfew_rules.get("weekend", "-")))
        else:
            today_curfew_label = str(curfew_rules.get("school_nights", curfew_rules.get("weekdays", "-")))
    message = request.args.get("message", "")
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
        .panel {
            margin-top: 24px;
            padding: 16px;
            border: 1px solid #dcdcdc;
            border-radius: 8px;
            background: #fafafa;
        }
        .panel h4 {
            margin-top: 0;
        }
        .inline-form {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: end;
        }
        .status {
            margin-top: 12px;
            color: #555;
            font-size: 14px;
        }
        .message {
            margin-top: 12px;
            padding: 10px 12px;
            background: #eaf7ea;
            border: 1px solid #b9dfb9;
            border-radius: 6px;
            color: #245c24;
        }
        .override-list {
            margin-top: 16px;
        }
        .override-item {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            align-items: center;
            padding: 12px 14px;
            margin-top: 10px;
            border: 1px solid #e3e3e3;
            border-radius: 8px;
            background: #fff;
        }
        .override-item .override-date {
            font-size: 15px;
        }
        .override-meta {
            color: #555;
            font-size: 14px;
            margin-top: 2px;
        }
        .override-item form {
            margin: 0;
            flex-shrink: 0;
        }
        .override-item .button-remove {
            margin: 0;
            height: auto;
            line-height: 1.4;
            padding: 4px 12px;
            color: #b23b3b;
            border-color: #e0b4b4;
        }
        .checkbox-label {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .checkbox-label input {
            width: auto;
            margin: 0;
        }
        @media (max-width: 600px) {
            body { padding: 12px; }
            .inline-form {
                flex-direction: column;
                align-items: stretch;
                gap: 4px;
            }
            .panel form label {
                display: block;
                width: 100%;
                margin: 0 0 10px 0;
            }
            .panel form input:not([type=checkbox]) {
                width: 100%;
            }
            .override-item {
                flex-direction: column;
                align-items: stretch;
                gap: 10px;
            }
            .override-item form {
                align-self: flex-end;
            }
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
    {% if message %}
    <div class="message">{{ message }}</div>
    {% endif %}
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
    {% if is_admin %}
    <div class="panel">
        <h4>Regeln</h4>
        <form method="post" action="{{ url_for('update_user_config', user=user) }}" class="inline-form">
            <input type="hidden" name="action" value="set_learning_time">
            <label class="checkbox-label" style="margin: 0;">
                <input type="checkbox" name="learning_time" {% if learning_time %}checked{% endif %}>
                Lernzeit aktiv
            </label>
            <button class="button-primary" type="submit">Speichern</button>
        </form>
        <div class="status">Heute: {{ today_limit_minutes }} Minuten, Sperrzeit {{ today_curfew_label }}</div>
    </div>

    <div class="panel">
        <h4>Tages-Override</h4>
        <form method="post" action="{{ url_for('update_user_config', user=user) }}">
            <input type="hidden" name="action" value="save_override">
            <div class="inline-form">
                <label>
                    Datum
                    <input type="date" name="override_date" required>
                </label>
                <label>
                    Limit Minuten
                    <input type="number" min="0" name="limit_minutes" placeholder="z.B. 240">
                </label>
                <label>
                    Sperrzeit
                    <input type="text" name="curfew" placeholder="22:00 oder 24:00">
                </label>
                <label class="checkbox-label" style="margin: 0;">
                    <input type="checkbox" name="disable_curfew">
                    Sperrzeit aus
                </label>
            </div>
            <label>
                Notiz
                <input type="text" name="notes" placeholder="z.B. Feiertag">
            </label>
            <button class="button-primary" type="submit">Override speichern</button>
        </form>

        <div class="override-list">
            {% for item in override_items %}
            <div class="override-item">
                <div>
                    <strong class="override-date">{{ item.date }}</strong>
                    <div class="override-meta">
                        Limit: {{ item.limit_minutes if item.limit_minutes != "" else "-" }},
                        Sperrzeit: {% if item.disable_curfew %}aus{% else %}{{ item.curfew if item.curfew else "-" }}{% endif %}
                        {% if item.notes %}, {{ item.notes }}{% endif %}
                    </div>
                </div>
                <form method="post" action="{{ url_for('update_user_config', user=user) }}">
                    <input type="hidden" name="action" value="clear_override">
                    <input type="hidden" name="override_date" value="{{ item.date }}">
                    <button type="submit" class="button-remove">Entfernen</button>
                </form>
            </div>
            {% endfor %}
            {% if not override_items %}
            <div class="override-meta">Keine Tages-Overrides gesetzt.</div>
            {% endif %}
        </div>
    </div>
    {% endif %}
</body>
</html>
    """
    return render_template_string(
        html,
        user=user,
        stats=stats,
        avg_minutes=avg_minutes,
        scale=scale,
        is_admin=is_admin,
        learning_time=bool(flags.get("learning_time")),
        today_limit_minutes=today_limit_minutes,
        today_curfew_label=today_curfew_label,
        override_items=override_items,
        message=message,
    )


def _run_check(fn):
    try:
        return fn()
    except Exception as exc:
        return f"ERROR: {exc}"


def _check_db_dir() -> str:
    probe = DB_DIR / ".watchdog.tmp"
    probe.write_text("ok")
    probe.unlink()
    return "ok"


def _check_disk_space() -> str:
    stat = os.statvfs(DB_DIR)
    free = stat.f_frsize * stat.f_bavail
    if free < 100 * 1024 * 1024:
        return f"ERROR: low disk space ({free // (1024 * 1024)}MB free)"
    return "ok"


def _check_password_file() -> str:
    if not PASSWORD_FILE.exists():
        return "ERROR: password file missing"
    users = _load_users()
    if not users:
        return "ERROR: no users loaded"
    return f"ok ({len(users)} users)"


@app.get("/watchdog")
def watchdog():
    checks = {
        "db_dir": _run_check(_check_db_dir),
        "disk_space": _run_check(_check_disk_space),
        "users": _run_check(_check_password_file),
    }
    overall = "ERROR" if any(v.startswith("ERROR") for v in checks.values()) else "ok"
    now = _now_local().strftime("%Y-%m-%d %H:%M:%S")
    body = (
        f"status: {overall}\n"
        f"name: MinerTimer\n"
        f"version: {CLIENT_VERSION}\n"
        f"time: {now}\n"
        + "\n".join(f"{k}: {v}" for k, v in checks.items())
        + "\n"
    )
    return Response(body, status=200, mimetype="text/plain")


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
        client_version=CLIENT_VERSION,
    )
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
