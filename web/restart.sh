#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

git -C "$SCRIPT_DIR/.." pull
docker compose -f "$SCRIPT_DIR/docker-compose-caddy.yml" down
docker compose -f "$SCRIPT_DIR/docker-compose-caddy.yml" up --build -d
