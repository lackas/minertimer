#!/bin/sh
set -euo pipefail

ENV_FILE="${1:-.env}"

if [ -f "$ENV_FILE" ]; then
    echo "Env file already exists at $ENV_FILE. Remove it first if you want new values." >&2
    exit 1
fi

SECRET_KEY=$(python3 - <<'PY' 2>/dev/null || python - <<'PY')
import secrets
print(secrets.token_urlsafe(32))
PY

API_TOKEN=$(python3 - <<'PY' 2>/dev/null || python - <<'PY')
import uuid
print(uuid.uuid4())
PY

cat > "$ENV_FILE" <<EOF
SECRET_KEY=$SECRET_KEY
API_TOKEN=$API_TOKEN
NOTIFICATION_URL=http://minecraft.lackas.net/update
EOF

echo "Created $ENV_FILE with random SECRET_KEY and API_TOKEN."
echo "For the Mac client, copy this file to /Users/Shared/minertimer/.env (sudo may be required)."
