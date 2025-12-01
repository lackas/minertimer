#!/bin/zsh

###
# Core MINERTIMER script. Kills minecraft Java edition on MacOS after 30 min.
# Developed and owned by Soferio Pty Limited.
###

# Time limit in seconds (e.g., 1800 for half an hour)
TIME_LIMIT_DEFAULT=1800
DISPLAY_5_MIN_WARNING=true
DISPLAY_1_MIN_WARNING=true
NOTIFICATION_URL="http://minecraft.lackas.net/timer"

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

while true; do
    
    MINECRAFT_PIDS=$(ps aux | grep -Eiww "[M]inecraft|[N]oRiskClient|[M]odrinthApp/meta" | awk '{print $2}')
    MINECRAFT_UID=$(ps aux | grep -Eiww "[M]inecraft|[N]oRiskClient|[M]odrinthApp/meta" | awk '{print $1}' | head -1 )
    # If Minecraft is running
    
    if [ -n "$MINECRAFT_PIDS" ]; then
        if [ -n "$NOTIFICATION_URL" ]; then
            # echo $url
            url="$NOTIFICATION_URL/$MINECRAFT_UID/$CURRENT_DATE/$TOTAL_PLAYED_TIME/$TIME_LIMIT"
            res=`curl -s $url`
            # echo "RESPONSE: $res"
            if [[ $? -eq 0 && "$res" =~ ^[0-9]+$ && $res -ne $TIME_LIMIT && $res -gt $TOTAL_PLAYED_TIME ]]; then
                echo "Increasing TIME_LIMIT from $TIME_LIMIT to $res ($TOTAL_PLAYED_TIME played)"
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
        
        # Sleep, then increment the playtime
        sleep $RECHECK_TIME
        TOTAL_PLAYED_TIME=$((TOTAL_PLAYED_TIME + $RECHECK_TIME))

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
done


# vim: set expandtab tabstop=4 shiftwidth=4 softtabstop=4:
