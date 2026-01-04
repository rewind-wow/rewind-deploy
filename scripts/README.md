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
