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
- **Process detection (macOS)**: Matches `[M]inecraft|[N]oRiskClient|[M]odrinthApp/meta`
- **Process detection (Windows)**: Checks `javaw.exe`/`java.exe` command lines for "minecraft", plus `Minecraft.Windows` (Bedrock), `NoRiskClient`, `Modrinth`
- **Daily reset**: Calendar-day based using configured TIMEZONE (default: Europe/Berlin)
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
