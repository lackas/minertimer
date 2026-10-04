#!/bin/sh
# Rebuild and restart the MinerTimer server from this checkout.
#
# The compose file is load-bearing: Caddy reaches this service by container
# name (`reverse_proxy web-minertimer-1:8000`), which only resolves while the
# container sits on the shared `wtb-shared` network that docker-compose.yml
# joins. The older docker-compose-caddy.yml attaches `caddy_default` instead,
# so starting that one leaves the site unreachable behind a container that
# looks perfectly healthy.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

git -C "$SCRIPT_DIR/.." pull --ff-only

# `up --build -d` recreates the container in place. A `down` first would take
# the site offline for the whole build instead of the second it takes to swap
# the container, and the clients would log failed reports meanwhile.
docker compose -f "$COMPOSE_FILE" up --build -d

# Print the client version the server now advertises: clients compare
# themselves against this number, so it is the one value a rollout is about.
# Also doubles as a health check — curl -f fails the script on a broken start.
printf 'waiting for the server'
i=0
while [ "$i" -lt 15 ]; do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/version >/dev/null 2>&1; then
        break
    fi
    printf '.'
    i=$((i + 1))
    sleep 1
done
echo
printf 'client version served: '
curl -fsS --max-time 10 http://127.0.0.1:8000/version
echo
