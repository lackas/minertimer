# MinerTimer

Parental control system for limiting Minecraft playtime (Java & Bedrock Edition) on macOS and Windows.

## Overview

MinerTimer runs a lightweight background daemon that monitors Minecraft processes, tracks daily playtime, and enforces configurable time limits. Parents manage settings through a web dashboard.

### Features

- Automatic detection of Minecraft Java Edition, Bedrock Edition, NoRiskClient, and Modrinth
- Configurable daily time limits (default: 30 minutes)
- Web dashboard for real-time monitoring and time management
- Voice and notification warnings before time expires (5 min, 1 min)
- Admin can extend or revoke time remotely via the web UI
- Per-user playtime tracking with 30-day statistics
- Auto-update: clients update themselves from the server daily
- Multi-platform: macOS (LaunchDaemon) and Windows (NSSM service or Scheduled Task)

### Architecture

```
[macOS Client]  ──>  [Flask Web Server]  <──  [Admin Browser]
[Windows Client] ──>     (Docker)
```

- **Clients** check for Minecraft every 30 seconds, report playtime to the server, and kill the game when time runs out
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
