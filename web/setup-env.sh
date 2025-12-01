#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env}"

if [ -f "$ENV_FILE" ]; then
    echo "Env file already exists at $ENV_FILE. Remove it first if you want new values." >&2
    exit 1
fi

generate_secret() {
    if command -v python3 >/dev/null 2>&1; then
        python3 - <<'PY' 2>/dev/null
import secrets
print(secrets.token_urlsafe(32))
PY
        return
    fi
    if command -v python >/dev/null 2>&1; then
        python - <<'PY' 2>/dev/null
import secrets
print(secrets.token_urlsafe(32))
PY
        return
    fi
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 48 | tr -d '\n'
        return
    fi
    uuidgen 2>/dev/null || echo "fallback-secret-$(date +%s)"
}

generate_uuid() {
    if command -v python3 >/dev/null 2>&1; then
        python3 - <<'PY' 2>/dev/null
import uuid
print(uuid.uuid4())
PY
        return
    fi
    if command -v python >/dev/null 2>&1; then
        python - <<'PY' 2>/dev/null
import uuid
print(uuid.uuid4())
PY
        return
    fi
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen
        return
    fi
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 16
        return
    fi
    echo "fallback-token-$(date +%s)"
}

SECRET_KEY=$(generate_secret)
API_TOKEN=$(generate_uuid)

cat > "$ENV_FILE" <<EOF
SECRET_KEY=$SECRET_KEY
API_TOKEN=$API_TOKEN
NOTIFICATION_URL=http://minecraft.lackas.net/update
EOF

echo "Created $ENV_FILE with random SECRET_KEY and API_TOKEN."
echo "For the Mac client, copy this file to /Users/Shared/minertimer/.env (sudo may be required)."
