# Map rebuild tool

This is the first part of the Noggit-to-VMaNGOS workflow: a friendly
interactive shell and a read-only remote preflight command. It checks whether a
later synchronization and extraction can run, but it does not upload client
files, run extractors, stop containers, or modify deployed map data.

The intended user experience is the interactive shell. It asks for the needed
connection and server details at startup, keeps them in memory for the session,
and allows them to be changed without restarting the program. Non-secret
settings can be saved in named JSON profiles. SSH passwords are never saved and
are entered again when a password-authenticated profile is loaded. Future
commands for synchronizing Noggit ADTs, extracting maps, and deploying the
result will use the same session.

## Windows support

The CLI is cross-platform Python. It uses Paramiko for SSH instead of requiring
an external `ssh` executable, so Windows users can run it from PowerShell or
Command Prompt. Paramiko uses the standard SSH key locations and honors the
local SSH agent when available. Host keys must already be present in the
configured `known_hosts` file; unknown keys are rejected rather than accepted
automatically.

Install from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

On Linux/macOS, the equivalent is:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Prepare SSH host-key verification

Connect once with the platform SSH client so the remote host is recorded in
`~/.ssh/known_hosts` (on Windows this is normally under `%USERPROFILE%\.ssh`):

```powershell
ssh vmangos@example.org
```

After verifying the host key, exit the session. Alternatively, pass an
explicit known-hosts file with `--known-hosts`.

## Start the interactive shell

Start the shell without arguments:

```powershell
map-rebuild
```

Or explicitly:

```powershell
map-rebuild shell
```

It asks for:

1. Remote server address.
2. SSH user.
3. SSH port.
4. Authentication method.
5. SSH `known_hosts` file.
6. WoW client version.
7. VMaNGOS server image.
8. Remote complete client-data cache.
9. Remote build/staging directory.
10. Remote deployed extracted-data directory.
11. Remote Compose file and service.
12. Disk-space and SSH timeout thresholds.

The shell then provides:

```text
check       Run the read-only preflight
show        Display current settings without displaying the password
configure   Ask all startup questions again
set         Change one setting during the session
save        Save non-secret settings to a JSON profile
profiles    List saved profiles
use NAME    Switch profiles
new NAME    Configure a new profile
help        Show commands
exit        Leave the shell
```

## Saved profiles

The shell stores non-secret settings in a per-user JSON file:

```text
Windows: %APPDATA%\RewindWoW\map-rebuild\config.json
macOS:   ~/Library/Application Support/RewindWoW/map-rebuild/config.json
Linux:   ~/.config/rewind-wow/map-rebuild/config.json
```

The first configured session uses the `production` profile by default. Save it
explicitly:

```text
map-rebuild> save
Saved profile 'production' ...
The SSH password was not saved; it will be requested next time.
```

The JSON contains server paths, client version, image, SSH user, and other
settings, but never contains the SSH password. It is written atomically so an
interrupted save does not replace a valid configuration with a partial file.
On Unix-like systems it is also written with user-only permissions.

Profiles are useful when the user has multiple servers:

```text
map-rebuild> new staging
# answer the setup questions
map-rebuild> save
map-rebuild> profiles
production
staging (active)
map-rebuild> use production
map-rebuild> use staging
map-rebuild> delete staging
```

Available profile commands:

```text
save                  Save current non-secret settings
profiles              List saved profiles
use NAME              Switch profiles
new NAME              Configure a new profile
delete [NAME]         Delete a profile
reload                Reload profiles from disk
```

When switching or exiting with unsaved changes, the shell asks for
confirmation. Passwords are held only in memory and must be entered again on a
future launch for password-authenticated profiles.

Examples inside the shell:

```text
map-rebuild> show
map-rebuild> check
map-rebuild> set host new-server.example.org
map-rebuild> set client_version 5875
map-rebuild> set password
map-rebuild> configure
map-rebuild> exit
```

`set password` asks for the replacement password without echoing it. Secrets
are kept only in memory for the current process and are never shown by `show`.

## Run the read-only check directly

Using a private key is preferred:

```powershell
map-rebuild check `
  --host example.org `
  --user vmangos `
  --key $env:USERPROFILE\.ssh\id_ed25519
```

Password authentication is also supported. The safest password option is an
interactive prompt; the password is not echoed or stored in shell history:

```powershell
map-rebuild check `
  --host example.org `
  --user vmangos `
  --prompt-password
```

For automation, read the password from an environment variable. Do not commit
the variable or put the password directly into a script:

```powershell
$env:REWIND_SSH_PASSWORD = Read-Host "SSH password" -AsSecureString | `
  ConvertFrom-SecureString -AsPlainText
map-rebuild check `
  --host example.org `
  --user vmangos `
  --password-env REWIND_SSH_PASSWORD
Remove-Item Env:REWIND_SSH_PASSWORD
```

`--password` is supported for testing, but is discouraged because command-line
arguments can be saved in shell history or exposed in process listings:

```powershell
map-rebuild check --host example.org --user vmangos --password "not-recommended"
```

When a password is supplied, Paramiko does not try the local SSH agent or
private-key discovery. You can still provide `--key` with a password if the
server requires a passphrase-protected key and password authentication is also
needed, although key-only authentication is preferred.

The same commands work in Command Prompt with `^` line continuations, or as a
single line:

```text
map-rebuild check --host example.org --user vmangos --key C:\Users\me\.ssh\id_ed25519
```

Defaults assume:

- client version: `5875`
- image: `ghcr.io/mserajnik/vmangos-server:5875`
- remote client cache: `/srv/rewind/client-data/5875`
- remote staging root: `/srv/rewind/map-builds`
- Compose file: `/home/vmangos/rewind-deploy/compose.yaml`
- Compose service: `mangosd`

Override paths when the target uses a different layout:

```powershell
map-rebuild check `
  --host example.org `
  --user vmangos `
  --client-version 5875 `
  --image ghcr.io/rewind-wow/vmangos-server-custom:my-build-5875 `
  --remote-client-data /home/vmangos/client-data/5875 `
  --remote-build-root /home/vmangos/map-builds `
  --compose-file /home/vmangos/rewind-deploy/compose.yaml
```

Use `--skip-image-check` if the image will be pulled later. The check never
pulls an image; it only reports whether Docker can inspect an image already
present on the remote host.

## What `check` does and does not do

`map-rebuild check` is a read-only preflight. It performs no synchronization,
extraction, or deployment. Specifically, it does not:

- upload Noggit ADTs or any other client files;
- create or modify the remote client cache;
- pull Docker images;
- run `extract-client-data` or any extractor;
- stop, restart, or recreate Docker Compose services;
- modify the deployed `extracted-data` directory; or
- create or remove a rebuild lock.

It does inspect the existing lock marker, so it can report whether another
map-rebuild operation appears to be active. It also verifies that the SSH host
key is already trusted; unknown host keys are rejected automatically.

## SSH authentication details

Authentication options are mutually exclusive for the password forms:

| Option | Use | Security notes |
| --- | --- | --- |
| `--key PATH` | Private-key authentication | Recommended. The path is local to the computer running the tool. |
| `--prompt-password` | Interactive password authentication | Recommended when using a password manually. Input is hidden. |
| `--password-env NAME` | Password authentication from an environment variable | Suitable for automation if the environment is protected. |
| `--password VALUE` | Password directly on the command line | Supported, but not recommended. |

If no password is given, Paramiko can use the SSH agent and default key files.
If a password is given, agent/default-key discovery is disabled to avoid
unexpected authentication attempts. A supplied `--key` remains available.

The tool does not write passwords to its logs or to build state. Nevertheless,
processes and CI systems can expose environment variables, so use a protected
secret mechanism for unattended execution.

## Typical server-side prerequisites

The remote account should be able to:

- log in over SSH and access its configured host key;
- run `docker version`;
- run `docker compose` against the configured Compose file;
- read the complete client-data cache;
- read the deployed extracted-data directory; and
- write to the existing staging directory.

The preflight expects the staging directory to already exist; it does not
create it. This keeps the command read-only and makes permissions explicit.
