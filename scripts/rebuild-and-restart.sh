#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_root/compose.yaml"
# Allow overriding compose command (e.g., DOCKER_COMPOSE_CMD="sudo docker compose")
# shellcheck disable=SC2206
compose_cmd=(${DOCKER_COMPOSE_CMD:-docker compose})

if [[ ! -f "$compose_file" ]]; then
  echo "Compose file not found at $compose_file. Copy compose.yaml.example first." >&2
  exit 1
fi

"$repo_root/scripts/build-custom-images.sh"

echo "Recreating containers..."
"${compose_cmd[@]}" -f "$compose_file" down
"${compose_cmd[@]}" -f "$compose_file" up -d
