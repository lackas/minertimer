# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MinerTimer is a parental control system for limiting Minecraft playtime (Java & Bedrock Edition). It consists of:
- **Web Backend**: Python Flask application serving admin dashboard and REST API
- **macOS Client**: Shell script (zsh) running as LaunchDaemon that monitors Minecraft processes
- **Windows Client**: PowerShell script running as NSSM service or Scheduled Task

## Build and Run Commands

### Docker (Production)
```bash
docker build -f web/Dockerfile -t minertimer .
docker-compose -f web/docker-compose.yml up
```

### Local Development
```bash
cd web
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
TIMEZONE=Europe/Berlin SECRET_KEY=dev-key API_TOKEN=dev-token python minertimer.py
```

### Environment Setup
```bash
bash web/setup-env.sh  # Generates .env with random SECRET_KEY and API_TOKEN
```

### macOS Client Installation
```bash
sudo bash install_minertimer.sh     # Install client daemon
sudo bash uninstall_minertimer.sh   # Uninstall
```

### Windows Client Installation
```powershell
# Using NSSM (recommended, requires nssm.exe):
.\install_minertimer_win.ps1

# Using Task Scheduler (no third-party tools):
.\install_minertimer_win.ps1 -UseTaskScheduler

# Uninstall:
.\uninstall_minertimer_win.ps1
```

## Architecture

### Data Flow
1. macOS daemon (`minertimer.sh`) detects Minecraft processes every 30 seconds
2. Reports playtime to Flask server via `GET /update/<user>/<date>/<played>/<client_max>`
3. Server stores state in `web/db/<user>-YYYY-MM-DD` files (line 1: seconds played, line 2: max time)
4. Returns updated max time (admin may have adjusted it)
5. Daemon enforces limits by killing Minecraft when time exceeded

### Key Files
- `web/minertimer.py` - Main Flask application (all routes, templates inline)
- `minertimer.sh` - macOS daemon script (process monitoring, HTTP reporting)
- `minertimer.ps1` - Windows client script (PowerShell, same logic as macOS)
- `web/db/password` - User credentials (format: `user:password:role:default_minutes`)

### User Roles
- `user` - Can only view/manage own playtime
- `admin` - Can adjust all users' time limits, view all stats, download installer

### REST API Endpoints
| Endpoint | Purpose |
|----------|---------|
| `/update/<user>/<date>/<played>/<client_max>` | Client playtime update |
| `/increase?user=X&time=Y&stop=1` | Admin: adjust time limits |
| `/players` | AJAX partial for dashboard |
| `/user/<username>` | User statistics (30-day chart) |
| `/install` | Download macOS installer script |
| `/install/win` | Download Windows installer script |

## Conventions

- **Time units**: Stored as seconds in database, displayed as minutes in UI
- **Process detection (macOS)**: single `$PROCESS_PATTERN` in `minertimer.sh`, matched with `grep -Eiww` against `ps aux`: `[M]inecraft|[N]oRiskClient|[M]odrinthApp/meta`
- **Process detection (Windows)**: `$COMMAND_LINE_PATTERN` for `javaw.exe`/`java.exe` command lines, `$PROCESS_NAME_PATTERN` for launcher process names (`Minecraft.Windows` Bedrock, `NoRiskClient`, `Modrinth`)
- **Dawn (Feather) needs no pattern of its own**, verified against a real `ps aux` dump: its game JVM carries `minecraft` as a whole word four times (bundled runtime under `.dawn/cache/minecraft/`, the `.minecraft` profile dir, `-Dminecraft.launcher.brand`, `net.minecraft.client.main.Main`), its launcher and jcef helpers carry it nowhere. Version 3 added `[D]awn [(]Feather[)]|[D]awnLauncher|[.]dawn` and was reverted in 4: that also matched the launcher, which burns quota while a child only browses the launcher webview and closes its window at the limit. **The limit ends the game, not the launcher**
- **Adding a launcher**: prefer whatever marks the *game*; match the launcher only if its time should count. Never a bare product name — Dawn is also Chromium's WebGPU backend, so a bare `dawn` matches `--enable-dawn-features` and would kill a browser. Mind `grep -w`: it tests the character *after* the match, so a pattern ending in `/` never matches `/.dawn/runtime`. Verify against a real dump that includes the game JVM *and* the launcher, and check which side each alternative hits
- **Playtime accounting**: count the time a cycle *actually* took, never a flat `$RECHECK_TIME` — the process scan and the HTTP round trip sit on top of the sleep, which measured 30.8s real per 30s counted on Windows (~3%, seven free minutes in a four-hour allowance). Both clients therefore clamp the measured delta to `2 * RECHECK_TIME` and fall back to the nominal value outside that range: a machine suspended mid-game wakes with the game still in the process list and hours of wall clock gone, and an unbounded delta would eat the whole day's quota in one step. The Windows client additionally carries the sub-second remainder into the next cycle, because rounding each cycle on its own is biased (30.6s → 31s inflates playtime by the very percent we are fixing); `minertimer.sh` needs no remainder, its `date +%s` deltas are whole seconds whose truncation error is bounded and non-cumulative
- **Client version reporting**: clients send `X-Client-Version`, `X-Client-Platform` and (since 5) `X-Client-Host` on every `/update`; the server keeps **one record per machine** in `web/db/clients/<user>.json` (`{"machines": {"<platform>:<host>": {...}}}`, the single-slot format of client 4 is migrated on read and superseded by the first report carrying a host). One slot per user would only ever show the machine that reported last — a child may play mostly on the gaming PC and sometimes on the Mac. A machine is flagged `≠ CLIENT_VERSION` only while it is recent (`CLIENT_RECENT_HOURS`), shown with its date when older, and forgotten after `CLIENT_FORGET_DAYS`. All header values are unauthenticated, so they are whitelisted before being stored or rendered
- **Update check**: on its own timer (`UPDATE_CHECK_INTERVAL`, default hourly, overridable in the client `.env`), *not* tied to the daily rollover and not tied to anyone playing — the rollover branch only fires if the machine happens to be awake when the date changes, and a MacBook that spends the night closed stayed on an old version for days. The timer starts at "now" rather than zero so a self-update that kept disagreeing with the server restarts at most once per interval instead of in a tight loop. `/version` both logs and **records** the reporting client, which is the only sign of life from a machine nobody is playing on. A check carries no username — the clients only learn one from the owner of the running game process — so it is attributed to a machine the dashboard already knows from an earlier `/update` (`_user_for_machine`, exact `platform:host` first, a hostless record of a client 4 and older only when it is unambiguous). Without this a Mac that updated itself overnight kept showing "update pending" until the child next played, which was two days later
- **Rolling out a client change**: bump `VERSION` in `minertimer.sh`/`minertimer.ps1` **and** `CLIENT_VERSION` in `web/minertimer.py` together, then rebuild. From client 5 on, a change lands within the hour; clients 4 and older only compare at the daily rollover, so they need to be awake at midnight (or wake up later in the day, which fires the branch on the next cycle)
- **Restart after self-update**: macOS relies on the LaunchDaemon's `KeepAlive`, Windows-NSSM on `AppExit Default Restart`. The Scheduled Task variant has only an `AtStartup` trigger plus restart-on-failure, so the updater exits **non-zero** on purpose — a clean exit would end the task and leave the machine unmonitored until the next reboot. Clients older than version 4 still exit 0 and need one manual `Start-ScheduledTask` after their update
- **Play day**: the day runs from 06:00 to 06:00 in the configured TIMEZONE (`DAY_START_HOUR` in `web/minertimer.py`, mirrored in both clients — the server resolves quota and curfew for the date the client reports, so the three values have to agree). A day that flipped at midnight handed out a fresh quota in the middle of the night and retired the evening's curfew together with the date it belonged to: a Saturday `24:00` expired on a day the client had already left behind, so playing past midnight came with 240 new minutes. Everything that means "today" uses `_effective_day()`
- **Night block**: a hard floor independent of quota and curfew — nothing runs between `rules.curfew.night_block_start` (default `NIGHT_BLOCK_START_DEFAULT`, 01:00) and the start of the play day. It is keyed on wall-clock time via `_effective_day(now)`, **not** on the date the client sends, so clients older than 5 — which still roll over at midnight — are caught too. A start at or after `DAY_START_HOUR` is ignored rather than clamped: it would describe a window the play day does not own
- **Lifting a rule for one night**: `overrides.dates.<D>.disable_curfew` means "stay up late", `overrides.dates.<D>.open_end` means "all night" and lifts curfew *and* night block. They are separate because several holidays already carry `disable_curfew`, and widening that flag would have turned them into open end retroactively. Both are keyed on the **evening** date, which the play day makes automatic. Note the cap is written to the state file when a rule bites, so setting `open_end` after the fact does not give the evening back — only the admin's "+ time" does
- **Ending a session**: the server never sends a stop command, it answers with a limit and the client ends the game when its own counter reaches it. While a night rule is active the answer is a hard `0`, not the capped maximum: capping only works while the client's counter agrees with ours, and a counter that just reset — at the day change, or on a fresh install after a self-update — reads any positive limit as permission to play. A client 4 machine does exactly that at the midnight rollover, resetting its counter and then updating itself. The *stored* maximum stays capped to the playtime so the dashboard and the statistics keep the real numbers
- **Time increments**: Admin can add [5, 15, 30, 60] minutes

## Debugging

### macOS
```bash
# Check if daemon is running
sudo launchctl list | grep com.soferio.minertimer_daily_timer

# View daemon logs
log show --predicate 'processImagePath CONTAINS "minertimer"' --last 1h

# Unload/reload daemon
sudo launchctl unload /Library/LaunchDaemons/com.soferio.minertimer_daily_timer.plist
sudo launchctl load /Library/LaunchDaemons/com.soferio.minertimer_daily_timer.plist
```

### Windows
```powershell
# Check if service/task is running
Get-ScheduledTask -TaskName MinerTimer   # Task Scheduler
Get-Service MinerTimer                    # NSSM service

# View logs (NSSM)
Get-Content C:\ProgramData\minertimer\service.log -Tail 50

# Enable debug mode
New-Item C:\ProgramData\minertimer\debug -ItemType File

# Disable debug mode
Remove-Item C:\ProgramData\minertimer\debug
```

## Test Credentials (from password.dist)
- **User**: alice / Sunshine42
- **Admin**: charlie / Library11
