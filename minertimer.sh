#!/bin/zsh

###
# Core MINERTIMER script. Kills minecraft Java edition on MacOS after 30 min.
# Developed and owned by Soferio Pty Limited.
###

VERSION="5"
DEBUG_FILE="/Users/Shared/minertimer/debug"

# Processes that count as "Minecraft is running". Each alternative is matched
# case-insensitively as a whole word against the full `ps aux` line, and the
# first character sits in brackets so grep never matches its own command line.
#
# The Dawn (Feather) launcher needs **no entry of its own**, verified against a
# real `ps aux` dump of a running session on 2026-10-03: the game JVM carries
# "minecraft" as a whole word four times over (the bundled runtime under
# .dawn/cache/minecraft/, the profile's .minecraft game directory,
# -Dminecraft.launcher.brand and the net.minecraft.client.main.Main class), and
# so do the in-game Feather web helpers it spawns. The launcher process and its
# jcef browser helpers carry it nowhere.
#
# That asymmetry is exactly what we want and must not be "improved": the time
# limit is supposed to end the *game*, not the launcher. Matching the launcher
# bundle, "DawnLauncher" or ".dawn" would count the quota down while a child
# only browses the launcher's ad webview, and would close its window when the
# time is up. Tried that in version 3, reverted here.
PROCESS_PATTERN="[M]inecraft|[N]oRiskClient|[M]odrinthApp/meta"

# Load environment overrides (API token, URL, defaults)
ENV_FILE="/Users/Shared/minertimer/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# Time limit in seconds (e.g., 1800 for half an hour)
TIME_LIMIT_DEFAULT=${TIME_LIMIT_DEFAULT:-1800}
DISPLAY_5_MIN_WARNING=true
DISPLAY_1_MIN_WARNING=true
NOTIFICATION_URL=${NOTIFICATION_URL:-"https://minertimer.lackas.net/update"}
API_TOKEN=${API_TOKEN:-""}
CURL_HEADER_FILE=${CURL_HEADER_FILE:-"/Users/Shared/minertimer/.curl_headers"}

# Directory and file to store total played time for the day
LOG_DIRECTORY="/var/lib/minertimer"
LOG_FILE="${LOG_DIRECTORY}/minertimer_playtime.log"

# Create the directory (don't throw error if already exists)
mkdir -p $LOG_DIRECTORY

# Get the current date
CURRENT_DATE=$(date +%Y-%m-%d)

TIME_LIMIT="$TIME_LIMIT_DEFAULT"
TOTAL_PLAYED_TIME="0"

write_log() {
    echo "$CURRENT_DATE" > "$LOG_FILE"
    echo "$TOTAL_PLAYED_TIME" >> "$LOG_FILE"
    echo "$TIME_LIMIT" >> "$LOG_FILE"
}


# Read the last play date and total played time from the log file
if [ -f "$LOG_FILE" ]; then
    exec 3< $LOG_FILE
    read -r LAST_PLAY_DATE <&3
    read -r TOTAL_PLAYED_TIME <&3
    read -r TIME_LIMIT <&3
else
    LAST_PLAY_DATE="$CURRENT_DATE"
    TOTAL_PLAYED_TIME=0
    write_log
fi

 # If it's a new day, or first use, reset the playtime
if [ "$LAST_PLAY_DATE" != "$CURRENT_DATE" ]; then
    TOTAL_PLAYED_TIME=0
    TIME_LIMIT="$TIME_LIMIT_DEFAULT"
    write_log
fi

RECHECK_TIME=30

# Ensure header file exists with the token so it stays off the command line
if [ -n "$API_TOKEN" ] && [ ! -s "$CURL_HEADER_FILE" ]; then
    (umask 177; printf 'X-API-Token: %s\n' "$API_TOKEN" > "$CURL_HEADER_FILE")
fi

# Build curl args so the token stays out of process args. The version header
# lets the dashboard show which client version each machine actually runs —
# without it there is no way to tell whether an auto-update has landed. The
# host header separates the machines of one child: the dashboard keeps one
# record per machine, so a rarely used Mac stays visible next to the PC that
# reported last.
CLIENT_HOST=$(hostname -s 2>/dev/null)
CURL_BASE_ARGS=(-s -H "X-Client-Version: $VERSION" -H "X-Client-Platform: macos")
if [ -n "$CLIENT_HOST" ]; then
    CURL_BASE_ARGS+=(-H "X-Client-Host: $CLIENT_HOST")
fi
if [ -s "$CURL_HEADER_FILE" ]; then
    CURL_BASE_ARGS+=(-H "@${CURL_HEADER_FILE}")
fi

# The update check used to hang off the daily rollover, which only fires if the
# machine is awake when the date changes. A MacBook that spends the night
# closed never got there and stayed on an old version for days, so the check
# now runs on its own clock — no date change and nobody playing required.
UPDATE_CHECK_INTERVAL=${UPDATE_CHECK_INTERVAL:-3600}
# Seeded with "now" rather than 0 so the first check happens one interval after
# a start. A self-update that kept installing a version the server disagrees
# with would otherwise restart in a tight loop; this bounds it to once an hour.
LAST_UPDATE_CHECK=$(date +%s)

check_for_update() {
    local base_url server_version
    base_url="${NOTIFICATION_URL%/update}"
    server_version=$(curl "${CURL_BASE_ARGS[@]}" "$base_url/version" 2>/dev/null)
    if [ -z "$server_version" ] || [ "$server_version" = "$VERSION" ]; then
        return
    fi
    echo "Update available: $VERSION -> $server_version"
    curl "${CURL_BASE_ARGS[@]}" "$base_url/install/minertimer.sh" -o /tmp/minertimer_new.sh 2>/dev/null
    if [ -s /tmp/minertimer_new.sh ] && head -1 /tmp/minertimer_new.sh | grep -q '^#!/bin/zsh'; then
        cp /tmp/minertimer_new.sh /Users/Shared/minertimer/minertimer.sh
        chmod +x /Users/Shared/minertimer/minertimer.sh
        rm -f /tmp/minertimer_new.sh
        echo "Updated to version $server_version, restarting..."
        # The LaunchDaemon's KeepAlive starts the new script immediately.
        exit 0
    fi
}

while true; do
    CYCLE_START=$(date +%s)

    # Toggle debug tracing based on debug file
    if [ -f "$DEBUG_FILE" ]; then
        [[ ! -o xtrace ]] && echo "Debug enabled"
        set -x
    else
        [[ -o xtrace ]] && echo "Debug disabled"
        set +x
    fi

    # One ps snapshot for both values: two separate calls could disagree about
    # which processes exist, and the pattern would have to be kept in sync twice.
    MINECRAFT_PS=$(ps aux | grep -Eiww "$PROCESS_PATTERN")
    MINECRAFT_PIDS=$(echo "$MINECRAFT_PS" | awk '{print $2}')
    MINECRAFT_UID=$(echo "$MINECRAFT_PS" | awk '{print $1}' | head -1 )
    # If Minecraft is running
    
    if [ -n "$MINECRAFT_PIDS" ]; then
        if [ -n "$NOTIFICATION_URL" ]; then
            url="$NOTIFICATION_URL/$MINECRAFT_UID/$CURRENT_DATE/$TOTAL_PLAYED_TIME/$TIME_LIMIT"
            res=$(curl "${CURL_BASE_ARGS[@]}" "$url")
            curl_exit=$?
            if [[ $curl_exit -ne 0 ]]; then
                echo "curl failed (exit $curl_exit) for $url"
            elif [[ ! "$res" =~ ^[0-9]+$ ]]; then
                echo "Unexpected server response: '$res'"
            fi
            if [[ $curl_exit -eq 0 && "$res" =~ ^[0-9]+$ && $res -ne $TIME_LIMIT ]]; then
                echo "Updating TIME_LIMIT from $TIME_LIMIT to $res ($TOTAL_PLAYED_TIME played)"
                if (( res > TIME_LIMIT )); then
                    increase_minutes=$(( (res - TIME_LIMIT) / 60 ))
                    say "Time extension of $increase_minutes minutes granted"
                fi
                TIME_LIMIT="$res"
                REMAINING=$((TIME_LIMIT - TOTAL_PLAYED_TIME))
                if (( REMAINING > 300 )); then
                    DISPLAY_5_MIN_WARNING=true
                fi
                if (( REMAINING > 60 )); then
                    DISPLAY_1_MIN_WARNING=true
                fi
            fi
        fi 
        
        # If the time limit has been reached, kill the Minecraft process
        if ((TOTAL_PLAYED_TIME >= TIME_LIMIT)); then
            echo $MINECRAFT_PIDS | xargs kill
            echo "Minecraft has been closed after reaching the daily time limit."
            osascript -e 'display notification "Minecraft time expired" with title "Minecraft Closed"'
            afplay /System/Library/Sounds/Glass.aiff 
        elif ((TOTAL_PLAYED_TIME >= TIME_LIMIT - 300)) && [ "$DISPLAY_5_MIN_WARNING" = true ]; then
            osascript -e 'display notification "Minecraft will exit in 5 minutes" with title "Minecraft Time Expiring Soon"'
            say "Minecraft time will expire in 5 minutes"
            DISPLAY_5_MIN_WARNING=false
        elif ((TOTAL_PLAYED_TIME >= TIME_LIMIT - 60)) && [ "$DISPLAY_1_MIN_WARNING" = true ]; then
            osascript -e 'display notification "Minecraft will exit in 1 minute" with title "Minecraft Time Expiring"'
            say "Minecraft time will expire in 1 minute"
            DISPLAY_1_MIN_WARNING=false
        fi
        
        # Sleep, then add the time that actually passed. Adding a flat
        # $RECHECK_TIME undercounts, because the ps scan and the curl round
        # trip sit on top of the sleep: on Windows that measured 30.8s of real
        # time per 30 counted, about 3% — seven free minutes in a four-hour
        # allowance.
        #
        # The upper bound is what makes this safe. A laptop suspended mid-game
        # wakes up with Minecraft still in ps and hours of wall-clock gone, and
        # an unbounded delta would swallow the whole day's quota in one step;
        # the same goes for an NTP correction jumping the clock forward. A
        # backwards jump lands below 1 and falls back as well.
        sleep $RECHECK_TIME
        CYCLE_SECONDS=$(( $(date +%s) - CYCLE_START ))
        if (( CYCLE_SECONDS < 1 || CYCLE_SECONDS > 2 * RECHECK_TIME )); then
            CYCLE_SECONDS=$RECHECK_TIME
        fi
        TOTAL_PLAYED_TIME=$((TOTAL_PLAYED_TIME + CYCLE_SECONDS))

        write_log

        # # Update the total played time in the log file (Note on mac -i requires extension)
        # sed -i '' "$ s/.*/$TOTAL_PLAYED_TIME/" "$LOG_FILE"

    else
        sleep $RECHECK_TIME
    fi

    # Get the current date
    CURRENT_DATE=$(date +%Y-%m-%d)

    # Read the last play date from the log file
    if [ -f "$LOG_FILE" ]; then
        LAST_PLAY_DATE=$(head -n 1 "$LOG_FILE")
    else
        # This error should not happen because log file created above
        echo "ERROR - NO LOG FILE"
    fi

    # If it's a new day, reset the playtime
    if [ "$LAST_PLAY_DATE" != "$CURRENT_DATE" ]; then
        TOTAL_PLAYED_TIME=0
        TIME_LIMIT="$TIME_LIMIT_DEFAULT"
        DISPLAY_5_MIN_WARNING=true
        DISPLAY_1_MIN_WARNING=true
        write_log
    fi

    # Check for updates on our own schedule, independent of the rollover.
    NOW_SECONDS=$(date +%s)
    if (( NOW_SECONDS - LAST_UPDATE_CHECK >= UPDATE_CHECK_INTERVAL || NOW_SECONDS < LAST_UPDATE_CHECK )); then
        LAST_UPDATE_CHECK=$NOW_SECONDS
        check_for_update
    fi
done


# vim: set expandtab tabstop=4 shiftwidth=4 softtabstop=4:
