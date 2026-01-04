#!/usr/bin/env bash

set -euo pipefail

default_repo_url="https://github.com/rewind-wow/classic"
default_revision="development"
default_world_db_repo_url="https://github.com/brotalnia/database.git"
default_server_tag="vmangos-server:custom"
default_database_tag="vmangos-database:custom"
# Allow overriding docker build flags; default pulls latest base and disables cache
# shellcheck disable=SC2206
default_build_flags=(${DOCKER_BUILD_FLAGS:---pull --no-cache})

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

prompt() {
  local message="$1" default="$2" value
  read -r -p "$message [$default]: " value
  echo "${value:-$default}"
}

prompt_bool() {
  local message="$1" default="$2" value
  read -r -p "$message [$default]: " value
  value="${value:-$default}"
  case "${value,,}" in
    y|yes) return 0 ;;
    n|no) return 1 ;;
    *) echo "Invalid choice, using default: $default" >&2; [[ "${default,,}" =~ ^y ]] ;;
  esac
}

echo "Configure VMaNGOS build arguments"
repo_url="$(prompt "VMANGOS_REPOSITORY_URL" "$default_repo_url")"
revision="$(prompt "VMANGOS_REVISION" "$default_revision")"
world_db_repo_url="$(prompt "VMANGOS_WORLD_DB_REPOSITORY_URL (database build only)" "$default_world_db_repo_url")"

build_server=false
build_database=false
if prompt_bool "Build server image?" "y"; then
  build_server=true
fi
if prompt_bool "Build database image?" "y"; then
  build_database=true
fi

if ! $build_server && ! $build_database; then
  echo "Nothing to build; exiting."
  exit 0
fi

if [[ "$build_server" == true ]]; then
  server_tag="$(prompt "Server image tag" "$default_server_tag")"
  echo "Building server image ($server_tag)..."
  docker build \
    "${default_build_flags[@]}" \
    -f "$repo_root/docker/server/Dockerfile" \
    --build-arg "VMANGOS_REPOSITORY_URL=$repo_url" \
    --build-arg "VMANGOS_REVISION=$revision" \
    --tag "$server_tag" \
    "$repo_root"
fi

if [[ "$build_database" == true ]]; then
  database_tag="$(prompt "Database image tag" "$default_database_tag")"
  echo "Building database image ($database_tag)..."
  docker build \
    "${default_build_flags[@]}" \
    -f "$repo_root/docker/database/Dockerfile" \
    --build-arg "VMANGOS_REPOSITORY_URL=$repo_url" \
    --build-arg "VMANGOS_REVISION=$revision" \
    --build-arg "VMANGOS_WORLD_DB_REPOSITORY_URL=$world_db_repo_url" \
    --tag "$database_tag" \
    "$repo_root"
fi

echo "Done."
