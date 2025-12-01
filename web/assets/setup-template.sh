#!/bin/bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo: sudo bash setup.txt" >&2
  exit 1
fi

BASE_DIR="/Users/Shared/minertimer"
PLIST="/Library/LaunchDaemons/com.soferio.minertimer_daily_timer.plist"

mkdir -p "$BASE_DIR"

__CURL_DOWNLOAD__
chmod +x "$BASE_DIR/minertimer.sh"

cat > "$BASE_DIR/.env" <<'EOF'
API_TOKEN=__API_TOKEN__
NOTIFICATION_URL=__NOTIFICATION_URL__
EOF

cat > "$PLIST" <<'EOF'
__PLIST_CONTENT__
EOF

chown root:wheel "$PLIST"
chmod 644 "$PLIST"

launchctl bootout system/com.soferio.minertimer_daily_timer 2>/dev/null || true
launchctl load -w "$PLIST"

echo "MinerTimer installed/updated. Verify with: sudo launchctl list | grep com.soferio.minertimer_daily_timer"
