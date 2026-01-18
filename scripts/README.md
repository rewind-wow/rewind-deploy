# Building custom VMaNGOS images

Use this guide if you want to build images from your own VMaNGOS fork or a specific revision. For general usage, see `README.md` in the repo root.

## Prerequisites
- Docker and Compose installed.
- Repo cloned; configs copied: `cp config/mangosd.conf.example config/mangosd.conf` and `cp config/realmd.conf.example config/realmd.conf`.
- Compose file copied: `cp compose.yaml.example compose.yaml`.

## Build your images
Run the helper script and provide your fork URL and revision when prompted:

```sh
bash scripts/build-custom-images.sh
```

The script asks for:
- `VMANGOS_REPOSITORY_URL` (your fork URL)
- `VMANGOS_REVISION` (branch/commit/tag)
- `VMANGOS_WORLD_DB_REPOSITORY_URL` (only if you build a custom database image)
- Whether to build server and/or database images
- Tags to apply (defaults: `vmangos-server:custom`, `vmangos-database:custom`)

By default the script builds with `--pull --no-cache` to ensure fresh bases and code. Override with `DOCKER_BUILD_FLAGS` if you want to reuse cache, e.g. `DOCKER_BUILD_FLAGS="" bash scripts/build-custom-images.sh`.

You can also build manually with `docker build` (see the “If you build the images yourself” section in the root README).

## Point Compose at your images
Edit `compose.yaml` so it uses the tags you built:

- Server (your fork/revision): set `realmd.image` and `mangosd.image` to your server tag. The script defaults to `vmangos-server:custom`. You can see your built tag with `docker images | grep vmangos-server`.
- Database: only change `database.image` if you built a custom database image (script default tag is `vmangos-database:custom`). Otherwise leave the published image as-is.

## Configure and start
- In `compose.yaml`, adjust `TZ` and any `VMANGOS_REALMLIST_*` values you need.
- Ensure client data is extracted per the main README if you have not already.
- Start: `docker compose up -d` (run `docker compose pull` first if your images are in a registry).

That’s it—your Compose stack will now run using the code from your fork/revision.

## Rebuild and restart in one step

To rebuild the images with the helper script and restart the stack:

```sh
bash scripts/rebuild-and-restart.sh
```

The script expects `compose.yaml` to exist in the repo root and will run `docker compose down` followed by `docker compose up -d`. If your setup requires sudo, prefix via `DOCKER_COMPOSE_CMD="sudo docker compose" bash scripts/rebuild-and-restart.sh`.

## Monitor the core repo and redeploy

Use this if you want a standalone script that checks a local core repo for new commits and, when a new commit is detected, rebuilds and restarts the stack:

```sh
CORE_REPO_PATH=/path/to/core \
VMANGOS_REPOSITORY_URL=https://github.com/rewind-wow/experimental \
CORE_BRANCH=development \
VMANGOS_SERVER_TAG=vmangos-server:custom \
CHECK_INTERVAL_SECONDS=300 \
bash scripts/monitor-core-and-redeploy.sh
```

Notes:
- `CORE_REPO_PATH` is required (or pass it as the first argument).
- If `VMANGOS_REPOSITORY_URL` is not set, the script uses the `origin` remote URL from `CORE_REPO_PATH`. This should be an HTTPS URL if Docker builds need to clone without SSH credentials.
- Set `BUILD_DATABASE=true` if you also want to rebuild the database image.
- For cron, omit `CHECK_INTERVAL_SECONDS` and schedule the script at your desired interval.

### Cron example

Run every 5 minutes, logging output to a file:

```cron
*/5 * * * * CORE_REPO_PATH=/path/to/core VMANGOS_REPOSITORY_URL=https://github.com/rewind-wow/experimental CORE_BRANCH=development VMANGOS_SERVER_TAG=vmangos-server:custom /bin/bash /path/to/rewind-deploy/scripts/monitor-core-and-redeploy.sh >> /var/log/vmangos-core-monitor.log 2>&1
```

If your setup requires sudo for Docker, you can add `DOCKER_COMPOSE_CMD="sudo docker compose"` to the environment portion of the cron line.
