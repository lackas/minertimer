# MinerTimer

Parental control system for limiting Minecraft playtime (Java & Bedrock Edition) on macOS and Windows.

## Overview

MinerTimer runs a lightweight background daemon that monitors Minecraft processes, tracks daily playtime, and enforces configurable time limits. Parents manage settings through a web dashboard.

### Features

- Automatic detection of Minecraft Java Edition, Bedrock Edition, NoRiskClient, Modrinth, and Dawn (Feather) — the game itself, not the launcher window
- Dashboard shows the client version per machine — a child's gaming PC and Mac are listed separately — and marks a machine when an auto-update is still pending
- Configurable daily time limits (default: 30 minutes)
- Web dashboard for real-time monitoring and time management
- Voice and notification warnings before time expires (5 min, 1 min)
- Admin can extend or revoke time remotely via the web UI
- Per-user playtime tracking with 30-day statistics
- Auto-update: clients check hourly and update themselves from the server, whether or not anyone is playing
- Multi-platform: macOS (LaunchDaemon) and Windows (NSSM service or Scheduled Task)

### Architecture

```
[macOS Client]  ──>  [Flask Web Server]  <──  [Admin Browser]
[Windows Client] ──>     (Docker)
```

- **Clients** check for Minecraft every 30 seconds, count the time that actually elapsed, report playtime to the server, and kill the game when time runs out
- **Server** stores playtime state, serves the admin dashboard, and distributes client installers
- **Dashboard** shows live player status, lets admins adjust time limits, and provides setup instructions

## Installation

### Server (Docker)

```bash
docker compose -f web/docker-compose.yml up -d
```

Configure via `web/.env` (generate defaults with `bash web/setup-env.sh`):
- `SECRET_KEY` - Flask session secret
- `API_TOKEN` - Client authentication token
- `TIMEZONE` - Default: `Europe/Berlin`
- `NOTIFICATION_URL` - Client reporting URL

Optional per-child rules can be stored in `web/db/users/<name>.json`. Example fields:
- `flags.learning_time`
- `rules.schedule.mon_thu_minutes`
- `rules.schedule.fri_sun_minutes`
- `rules.exam_mode.active`
- `rules.exam_mode.reduction_minutes`
- `rules.curfew.school_nights`
- `rules.curfew.fri_sat`
- `rules.curfew.weekdays`
- `rules.curfew.weekend`
- `overrides.dates.<YYYY-MM-DD>.limit_minutes`
- `overrides.dates.<YYYY-MM-DD>.curfew`
- `overrides.dates.<YYYY-MM-DD>.disable_curfew`

The server currently applies the schedule, learning-time/exam reduction, and curfew rules to the daily limit. Date overrides can be used for holidays or one-off exceptions. Informational rules such as homework prerequisites or non-transferability can also be documented there for reference.

### Client: macOS

The easiest way is to log into the web dashboard as admin, click **Setup**, and download the macOS installer. Then run:

```bash
sudo bash ~/Downloads/setup.txt
```

Or install manually:

```bash
sudo bash install_minertimer.sh
```

### Client: Windows

Log into the web dashboard as admin, click **Setup**, and download the Windows installer. Run in an Administrator PowerShell:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\setup-win.ps1"
```

Or install manually with NSSM for a proper Windows Service:

```powershell
.\install_minertimer_win.ps1                   # requires nssm.exe in PATH
.\install_minertimer_win.ps1 -UseTaskScheduler  # no third-party tools needed
```

NSSM can be downloaded from [nssm.cc](https://nssm.cc/release/nssm-2.24.zip).

### Uninstall

**macOS:**
```bash
sudo bash uninstall_minertimer.sh
```

**Windows:**
```powershell
.\uninstall_minertimer_win.ps1
```

## User Management

Edit `web/db/password` (format: `user:password:role:default_minutes`):

```
alice:Sunshine42:user:30
charlie:Library11:admin:
```

Roles: `user` (can only view own stats), `admin` (full control).

## Attribution

Originally created by [Soferio Pty Ltd](https://github.com/soferio/minertimer) as a macOS-only shell script with a fixed 30-minute limit.

This fork is a substantial rewrite adding the web dashboard, REST API, multi-user support, configurable time limits, Windows support, auto-update, voice warnings, and per-user statistics.

## License

MIT License. See [LICENCE.txt](LICENCE.txt).

## Disclaimer

This is an independent project with no affiliation with or endorsement by Mojang or Minecraft.
