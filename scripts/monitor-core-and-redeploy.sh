#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="${COMPOSE_FILE:-$repo_root/compose.yaml}"
# Allow overriding compose command (e.g., DOCKER_COMPOSE_CMD="sudo docker compose")
# shellcheck disable=SC2206
compose_cmd=(${DOCKER_COMPOSE_CMD:-docker compose})

core_repo_path="${CORE_REPO_PATH:-${1:-}}"
core_remote="${CORE_REMOTE:-origin}"
core_branch="${CORE_BRANCH:-development}"
state_file="${STATE_FILE:-$repo_root/storage/core-monitor/last_seen}"
log_file="${LOG_FILE:-$repo_root/storage/core-monitor/error.log}"
run_on_first_check="${RUN_ON_FIRST_CHECK:-false}"
check_interval_seconds="${CHECK_INTERVAL_SECONDS:-}"

build_server="${BUILD_SERVER:-true}"
build_database="${BUILD_DATABASE:-false}"
server_tag="${VMANGOS_SERVER_TAG:-vmangos-server:custom}"
database_tag="${VMANGOS_DATABASE_TAG:-vmangos-database:custom}"
world_db_repo_url="${VMANGOS_WORLD_DB_REPOSITORY_URL:-https://github.com/brotalnia/database.git}"
# Allow overriding docker build flags; default pulls latest base and disables cache
# shellcheck disable=SC2206
default_build_flags=(${DOCKER_BUILD_FLAGS:---pull --no-cache})

normalize_bool() {
  case "${1,,}" in
    y|yes|true|1) echo "true" ;;
    n|no|false|0|"") echo "false" ;;
    *) echo "false" ;;
  esac
}

log_error() {
  local message="$1"
  mkdir -p "$(dirname "$log_file")"
  printf '%s %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$message" >> "$log_file"
}

run_cmd_capture() {
  local label="$1"
  shift
  local tmp
  tmp="$(mktemp)"
  if "$@" >"$tmp" 2>&1; then
    rm -f "$tmp"
    return 0
  fi
  log_error "$label failed. Output:"
  cat "$tmp" >> "$log_file"
  echo >> "$log_file"
  rm -f "$tmp"
  return 1
}

build_server="$(normalize_bool "$build_server")"
build_database="$(normalize_bool "$build_database")"
run_on_first_check="$(normalize_bool "$run_on_first_check")"

if [[ -z "$core_repo_path" ]]; then
  echo "CORE_REPO_PATH is required (or pass the path as the first argument)." >&2
  exit 1
fi

if [[ ! -f "$compose_file" ]]; then
  echo "Compose file not found at $compose_file. Copy compose.yaml.example first." >&2
  exit 1
fi

if [[ ! -d "$core_repo_path/.git" ]]; then
  echo "CORE_REPO_PATH does not look like a git repository: $core_repo_path" >&2
  exit 1
fi

get_repo_url() {
  if [[ -n "${VMANGOS_REPOSITORY_URL:-}" ]]; then
    echo "$VMANGOS_REPOSITORY_URL"
    return 0
  fi
  git -C "$core_repo_path" remote get-url "$core_remote"
}

get_remote_hash() {
  git -C "$core_repo_path" fetch --prune "$core_remote" >/dev/null
  git -C "$core_repo_path" rev-parse "$core_remote/$core_branch"
}

run_once() {
  local repo_url remote_hash last_hash

  repo_url="$(get_repo_url)"
  if [[ -z "$repo_url" ]]; then
    echo "Unable to determine repository URL; set VMANGOS_REPOSITORY_URL." >&2
    exit 1
  fi

  remote_hash="$(get_remote_hash)"
  if [[ -f "$state_file" ]]; then
    last_hash="$(cat "$state_file")"
  else
    last_hash=""
  fi

  if [[ -z "$last_hash" && "$run_on_first_check" != "true" ]]; then
    mkdir -p "$(dirname "$state_file")"
    echo "$remote_hash" > "$state_file"
    echo "No prior state; recorded $remote_hash."
    return 0
  fi

  if [[ "$remote_hash" == "$last_hash" ]]; then
    echo "No update detected ($remote_hash)."
    return 0
  fi

  echo "Update detected: ${last_hash:-none} -> $remote_hash"

  if [[ "$build_server" == "true" ]]; then
    echo "Building server image ($server_tag)..."
    if ! run_cmd_capture "Server build" docker build \
      "${default_build_flags[@]}" \
      -f "$repo_root/docker/server/Dockerfile" \
      --build-arg "VMANGOS_REPOSITORY_URL=$repo_url" \
      --build-arg "VMANGOS_REVISION=$remote_hash" \
      --tag "$server_tag" \
      "$repo_root"; then
      echo "Server build failed; leaving containers running and not updating state." >&2
      echo "See $log_file for details." >&2
      return 1
    fi
  fi

  if [[ "$build_database" == "true" ]]; then
    echo "Building database image ($database_tag)..."
    if ! run_cmd_capture "Database build" docker build \
      "${default_build_flags[@]}" \
      -f "$repo_root/docker/database/Dockerfile" \
      --build-arg "VMANGOS_REPOSITORY_URL=$repo_url" \
      --build-arg "VMANGOS_REVISION=$remote_hash" \
      --build-arg "VMANGOS_WORLD_DB_REPOSITORY_URL=$world_db_repo_url" \
      --tag "$database_tag" \
      "$repo_root"; then
      echo "Database build failed; leaving containers running and not updating state." >&2
      echo "See $log_file for details." >&2
      return 1
    fi
  fi

  echo "Recreating containers..."
  "${compose_cmd[@]}" -f "$compose_file" down
  "${compose_cmd[@]}" -f "$compose_file" up -d

  mkdir -p "$(dirname "$state_file")"
  echo "$remote_hash" > "$state_file"
}

if [[ -n "$check_interval_seconds" ]]; then
  while true; do
    run_once
    sleep "$check_interval_seconds"
  done
else
  run_once
fi
