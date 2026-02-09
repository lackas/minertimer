#Requires -Version 5.1

###
# Windows MinerTimer client. Kills Minecraft Java Edition on Windows after daily time limit.
# Port of the macOS minertimer.sh script.
###

$VERSION = "2"
$BASE_DIR = Join-Path $env:ProgramData "minertimer"
$DEBUG_FILE = Join-Path $BASE_DIR "debug"
$ENV_FILE = Join-Path $BASE_DIR ".env"
$LOG_FILE = Join-Path $BASE_DIR "minertimer_playtime.log"

# Defaults
$script:TIME_LIMIT_DEFAULT = 1800
$script:NOTIFICATION_URL = "https://minertimer.lackas.net/update"
$script:API_TOKEN = ""

# Load environment overrides
if (Test-Path $ENV_FILE) {
    foreach ($line in Get-Content $ENV_FILE) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            Set-Variable -Name $matches[1] -Value $matches[2] -Scope Script
        }
    }
}

$RECHECK_TIME = 30

# State
$script:CURRENT_DATE = Get-Date -Format "yyyy-MM-dd"
$script:TIME_LIMIT = [int]$script:TIME_LIMIT_DEFAULT
$script:TOTAL_PLAYED_TIME = 0
$script:DISPLAY_5_MIN_WARNING = $true
$script:DISPLAY_1_MIN_WARNING = $true

# Ensure base directory exists
New-Item -ItemType Directory -Path $BASE_DIR -Force | Out-Null

function Write-StateLog {
    @($script:CURRENT_DATE, $script:TOTAL_PLAYED_TIME, $script:TIME_LIMIT) |
        Set-Content $LOG_FILE
}

function Show-Notification {
    param([string]$Title, [string]$Message)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.BalloonTipTitle = $Title
        $notify.BalloonTipText = $Message
        $notify.Visible = $true
        $notify.ShowBalloonTip(5000)
        Start-Sleep -Milliseconds 200
        $notify.Dispose()
    } catch {
        Write-Host "NOTIFICATION: $Title - $Message"
    }
}

function Invoke-Speech {
    param([string]$Text)
    try {
        Add-Type -AssemblyName System.Speech -ErrorAction Stop
        $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
        $synth.Speak($Text)
        $synth.Dispose()
    } catch {
        Write-Host "TTS: $Text"
    }
}

function Get-MinecraftProcesses {
    $found = [System.Collections.ArrayList]@()

    # Check javaw.exe / java.exe whose command line contains "minecraft"
    try {
        $javaProcs = Get-CimInstance Win32_Process `
            -Filter "Name = 'javaw.exe' OR Name = 'java.exe'" `
            -ErrorAction SilentlyContinue
        foreach ($jp in $javaProcs) {
            if ($jp.CommandLine -imatch 'minecraft') {
                $p = Get-Process -Id $jp.ProcessId -ErrorAction SilentlyContinue
                if ($p) { [void]$found.Add($p) }
            }
        }
    } catch {}

    # Check for Minecraft (launcher/Bedrock), NoRiskClient, Modrinth
    foreach ($proc in (Get-Process -ErrorAction SilentlyContinue)) {
        if ($proc.ProcessName -imatch '^Minecraft|NoRiskClient|Modrinth') {
            if ($proc.Id -notin $found.Id) {
                [void]$found.Add($proc)
            }
        }
    }

    return $found
}

function Get-ProcessOwner {
    param([System.Diagnostics.Process]$Process)
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.Id)" -ErrorAction Stop
        $owner = Invoke-CimMethod -InputObject $cim -MethodName GetOwner -ErrorAction Stop
        if ($owner.User) { return $owner.User }
    } catch {}
    return $env:USERNAME
}

# ----- Read existing state -----
if (Test-Path $LOG_FILE) {
    $lines = @(Get-Content $LOG_FILE)
    if ($lines.Count -ge 3) {
        $LAST_PLAY_DATE = $lines[0]
        $script:TOTAL_PLAYED_TIME = [int]$lines[1]
        $script:TIME_LIMIT = [int]$lines[2]
    } else {
        $LAST_PLAY_DATE = $script:CURRENT_DATE
        Write-StateLog
    }
} else {
    $LAST_PLAY_DATE = $script:CURRENT_DATE
    Write-StateLog
}

# Reset on new day
if ($LAST_PLAY_DATE -ne $script:CURRENT_DATE) {
    $script:TOTAL_PLAYED_TIME = 0
    $script:TIME_LIMIT = [int]$script:TIME_LIMIT_DEFAULT
    Write-StateLog
}

# Build HTTP headers
$headers = @{}
if ($script:API_TOKEN) {
    $headers["X-API-Token"] = $script:API_TOKEN
}

# ===== Main loop =====
while ($true) {
    # Debug toggle
    if (Test-Path $DEBUG_FILE) {
        $VerbosePreference = "Continue"
    } else {
        $VerbosePreference = "SilentlyContinue"
    }

    $mcProcesses = @(Get-MinecraftProcesses)

    if ($mcProcesses.Count -gt 0) {
        $mcUser = Get-ProcessOwner $mcProcesses[0]

        # Report to server
        if ($script:NOTIFICATION_URL) {
            $url = "$($script:NOTIFICATION_URL)/$mcUser/$($script:CURRENT_DATE)/$($script:TOTAL_PLAYED_TIME)/$($script:TIME_LIMIT)"
            try {
                $res = (Invoke-WebRequest -Uri $url -Headers $headers `
                    -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop).Content.Trim()
                if ($res -match '^\d+$') {
                    $serverMax = [int]$res
                    if ($serverMax -ne $script:TIME_LIMIT) {
                        Write-Host "Updating TIME_LIMIT from $($script:TIME_LIMIT) to $serverMax ($($script:TOTAL_PLAYED_TIME) played)"
                        if ($serverMax -gt $script:TIME_LIMIT) {
                            $increaseMins = [math]::Floor(($serverMax - $script:TIME_LIMIT) / 60)
                            Invoke-Speech "Time extension of $increaseMins minutes granted"
                        }
                        $script:TIME_LIMIT = $serverMax
                        $remaining = $script:TIME_LIMIT - $script:TOTAL_PLAYED_TIME
                        if ($remaining -gt 300) { $script:DISPLAY_5_MIN_WARNING = $true }
                        if ($remaining -gt 60) { $script:DISPLAY_1_MIN_WARNING = $true }
                    }
                } else {
                    Write-Host "Unexpected server response: '$res'"
                }
            } catch {
                Write-Host "Server request failed: $_"
            }
        }

        # Enforce time limit
        if ($script:TOTAL_PLAYED_TIME -ge $script:TIME_LIMIT) {
            foreach ($proc in $mcProcesses) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
            Write-Host "Minecraft has been closed after reaching the daily time limit."
            Show-Notification "Minecraft Closed" "Minecraft time expired"
            try { [System.Media.SystemSounds]::Exclamation.Play() } catch {}
        }
        elseif ($script:TOTAL_PLAYED_TIME -ge ($script:TIME_LIMIT - 300) -and $script:DISPLAY_5_MIN_WARNING) {
            Show-Notification "Minecraft Time Expiring Soon" "Minecraft will exit in 5 minutes"
            Invoke-Speech "Minecraft time will expire in 5 minutes"
            $script:DISPLAY_5_MIN_WARNING = $false
        }
        elseif ($script:TOTAL_PLAYED_TIME -ge ($script:TIME_LIMIT - 60) -and $script:DISPLAY_1_MIN_WARNING) {
            Show-Notification "Minecraft Time Expiring" "Minecraft will exit in 1 minute"
            Invoke-Speech "Minecraft time will expire in 1 minute"
            $script:DISPLAY_1_MIN_WARNING = $false
        }

        Start-Sleep -Seconds $RECHECK_TIME
        $script:TOTAL_PLAYED_TIME += $RECHECK_TIME
        Write-StateLog
    } else {
        Start-Sleep -Seconds $RECHECK_TIME
    }

    # Update date
    $script:CURRENT_DATE = Get-Date -Format "yyyy-MM-dd"

    # Check for day change
    if (Test-Path $LOG_FILE) {
        $LAST_PLAY_DATE = (Get-Content $LOG_FILE -TotalCount 1)
    }

    if ($LAST_PLAY_DATE -ne $script:CURRENT_DATE) {
        $script:TOTAL_PLAYED_TIME = 0
        $script:TIME_LIMIT = [int]$script:TIME_LIMIT_DEFAULT
        $script:DISPLAY_5_MIN_WARNING = $true
        $script:DISPLAY_1_MIN_WARNING = $true
        Write-StateLog

        # Auto-update check
        $baseUrl = $script:NOTIFICATION_URL -replace '/update$', ''
        try {
            $serverVersion = (Invoke-WebRequest -Uri "$baseUrl/version" -Headers $headers `
                -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop).Content.Trim()
            if ($serverVersion -and $serverVersion -ne $VERSION) {
                Write-Host "Update available: $VERSION -> $serverVersion"
                $newScript = (Invoke-WebRequest -Uri "$baseUrl/install/minertimer.ps1" `
                    -Headers $headers -TimeoutSec 30 -UseBasicParsing -ErrorAction Stop).Content
                if ($newScript -and $newScript -match '^\s*#') {
                    $scriptPath = Join-Path $BASE_DIR "minertimer.ps1"
                    Set-Content -Path $scriptPath -Value $newScript -Encoding UTF8
                    Write-Host "Updated to version $serverVersion, restarting..."
                    exit 0
                }
            }
        } catch {
            # Update check failed, continue
        }
    }
}

# vim: set expandtab tabstop=4 shiftwidth=4 softtabstop=4:
