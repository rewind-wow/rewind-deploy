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
dbc_repo_path="${DBC_REPO_PATH:-/home/vmangos/rewind-deploy/storage/mangosd/extracted-data/5875/dbc}"
dbc_remote="${DBC_REMOTE:-origin}"
dbc_branch="${DBC_BRANCH:-main}"
dbc_state_file="${DBC_STATE_FILE:-$repo_root/storage/core-monitor/dbc_last_seen}"
monitor_dbc_repo="${MONITOR_DBC_REPO:-true}"
monitor_log_file="${MONITOR_LOG_FILE:-$repo_root/storage/core-monitor/core-monitor.log}"
log_file="${LOG_FILE:-$repo_root/storage/core-monitor/error.log}"
lock_dir="${LOCK_DIR:-$repo_root/storage/core-monitor/lock}"
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

lock_held="false"

cleanup_lock() {
  if [[ "$lock_held" == "true" ]]; then
    rmdir "$lock_dir" 2>/dev/null || true
    lock_held="false"
  fi
}

log_error() {
  local message="$1"
  mkdir -p "$(dirname "$log_file")"
  printf '%s %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$message" >> "$log_file"
}

log_info() {
  local message="$1"
  mkdir -p "$(dirname "$monitor_log_file")"
  printf '%s %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$message" >> "$monitor_log_file"
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

run_cmd_capture_output() {
  local output_var="$1"
  local label="$2"
  shift 2
  local tmp
  local output
  tmp="$(mktemp)"
  if "$@" >"$tmp" 2>&1; then
    output="$(cat "$tmp")"
    rm -f "$tmp"
    printf -v "$output_var" '%s' "$output"
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
monitor_dbc_repo="$(normalize_bool "$monitor_dbc_repo")"

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

if [[ "$monitor_dbc_repo" == "true" && ! -d "$dbc_repo_path/.git" ]]; then
  echo "DBC_REPO_PATH does not look like a git repository: $dbc_repo_path" >&2
  exit 1
fi

trap cleanup_lock EXIT

get_repo_url() {
  local repo_url
  if [[ -n "${VMANGOS_REPOSITORY_URL:-}" ]]; then
    echo "$VMANGOS_REPOSITORY_URL"
    return 0
  fi
  if ! run_cmd_capture_output repo_url "Core remote lookup" git -C "$core_repo_path" remote get-url "$core_remote"; then
    return 1
  fi
  echo "$repo_url"
}

get_remote_hash() {
  local hash
  if ! run_cmd_capture "Core fetch" git -C "$core_repo_path" fetch --prune "$core_remote"; then
    return 1
  fi
  if ! run_cmd_capture_output hash "Core revision lookup" git -C "$core_repo_path" rev-parse "$core_remote/$core_branch"; then
    return 1
  fi
  echo "$hash"
}

get_dbc_remote_info() {
  local hash
  if ! run_cmd_capture_output hash "DBC fetch" git -C "$dbc_repo_path" fetch --prune "$dbc_remote"; then
    return 1
  fi

  if ! run_cmd_capture_output hash "DBC revision lookup" git -C "$dbc_repo_path" rev-parse "$dbc_remote/$dbc_branch"; then
    return 1
  fi

  printf '%s %s\n' "$dbc_branch" "$hash"
}

read_state() {
  local file="$1"
  local status_var="$2"
  local hash_var="$3"
  local parsed_status=""
  local parsed_hash=""

  if [[ -f "$file" ]]; then
    read -r parsed_status parsed_hash < "$file" || true
    if [[ -z "$parsed_hash" && -n "$parsed_status" && "$parsed_status" != "ok" && "$parsed_status" != "fail" ]]; then
      # Backward compatibility for the initial one-field state format.
      parsed_hash="$parsed_status"
      parsed_status="ok"
    fi
  fi

  printf -v "$status_var" '%s' "$parsed_status"
  printf -v "$hash_var" '%s' "$parsed_hash"
}

write_state() {
  local file="$1"
  local status="$2"
  local hash="$3"

  mkdir -p "$(dirname "$file")"
  echo "$status $hash" > "$file"
}

restart_stack() {
  echo "Recreating containers..."
  if ! run_cmd_capture "Docker compose down" "${compose_cmd[@]}" -f "$compose_file" down; then
    echo "Container shutdown failed; leaving state unchanged." >&2
    echo "See $log_file for details." >&2
    return 1
  fi
  if ! run_cmd_capture "Docker compose up" "${compose_cmd[@]}" -f "$compose_file" up -d; then
    echo "Container startup failed; leaving state unchanged." >&2
    echo "See $log_file for details." >&2
    return 1
  fi
}

process_core_update() {
  local repo_url="" remote_hash="" last_hash="" last_status=""

  if ! repo_url="$(get_repo_url)"; then
    log_error "Unable to determine repository URL for remote $core_remote."
    echo "Unable to determine repository URL; set VMANGOS_REPOSITORY_URL." >&2
    return 1
  fi
  if [[ -z "$repo_url" ]]; then
    echo "Unable to determine repository URL; set VMANGOS_REPOSITORY_URL." >&2
    return 1
  fi

  if ! remote_hash="$(get_remote_hash)"; then
    echo "Unable to fetch $core_remote/$core_branch. See $log_file for details." >&2
    return 1
  fi

  read_state "$state_file" last_status last_hash

  if [[ -z "$last_hash" && "$run_on_first_check" != "true" ]]; then
    write_state "$state_file" "ok" "$remote_hash"
    return 0
  fi

  if [[ "$remote_hash" == "$last_hash" && "$last_status" != "fail" ]]; then
    return 0
  fi

  if [[ "$last_status" == "fail" && "$remote_hash" == "$last_hash" ]]; then
    echo "Skipping core update $remote_hash; previous build failed. Clear $state_file to retry."
    return 0
  fi

  echo "Core update detected: ${last_hash:-none} -> $remote_hash"
  log_info "Core update detected: ${last_hash:-none} -> $remote_hash"

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
      write_state "$state_file" "fail" "$remote_hash"
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
      write_state "$state_file" "fail" "$remote_hash"
      return 1
    fi
  fi

  if ! restart_stack; then
    return 1
  fi

  write_state "$state_file" "ok" "$remote_hash"
}

process_dbc_update() {
  local detected_dbc_branch="" remote_hash="" last_hash="" last_status="" dbc_remote_info=""

  if ! dbc_remote_info="$(get_dbc_remote_info)"; then
    echo "Unable to query the DBC remote. See $log_file for details." >&2
    return 1
  fi
  read -r detected_dbc_branch remote_hash <<<"$dbc_remote_info"

  read_state "$dbc_state_file" last_status last_hash

  if [[ -z "$last_hash" && "$run_on_first_check" != "true" ]]; then
    write_state "$dbc_state_file" "ok" "$remote_hash"
    return 0
  fi

  if [[ "$remote_hash" == "$last_hash" && "$last_status" != "fail" ]]; then
    return 0
  fi

  if [[ "$last_status" == "fail" && "$remote_hash" == "$last_hash" ]]; then
    echo "Skipping DBC update $remote_hash; previous update failed. Clear $dbc_state_file to retry."
    return 0
  fi

  echo "DBC update detected: ${last_hash:-none} -> $remote_hash"
  log_info "DBC update detected: ${last_hash:-none} -> $remote_hash"
  echo "Pulling DBC repository ($detected_dbc_branch)..."
  log_info "Pulling DBC repository ($detected_dbc_branch)..."
  if ! run_cmd_capture "DBC pull" git -C "$dbc_repo_path" pull --ff-only "$dbc_remote" "$detected_dbc_branch"; then
    echo "DBC pull failed; leaving containers running and not updating state." >&2
    echo "See $log_file for details." >&2
    write_state "$dbc_state_file" "fail" "$remote_hash"
    return 1
  fi

  if ! restart_stack; then
    return 1
  fi

  write_state "$dbc_state_file" "ok" "$remote_hash"
}

run_once() {
  local exit_code=0

  if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "Another monitor run is in progress; skipping."
    return 0
  fi
  lock_held="true"

  if ! process_core_update; then
    exit_code=1
  fi

  if [[ "$monitor_dbc_repo" == "true" ]]; then
    if ! process_dbc_update; then
      exit_code=1
    fi
  fi

  cleanup_lock
  return "$exit_code"
}

if [[ -n "$check_interval_seconds" ]]; then
  while true; do
    run_once
    sleep "$check_interval_seconds"
  done
else
  run_once
fi
