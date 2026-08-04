"""Friendly interactive shell for configuring and running map rebuild tasks."""

from __future__ import annotations

import cmd
import getpass
import os
from pathlib import Path, PurePosixPath
import platform
import shlex

from .cli import print_result
from .config import ConfigError, ProfileStore, validate_profile_name
from .preflight import CheckResult, PreflightConfig, run_preflight


DEFAULT_CLIENT_VERSION = "5875"
DEFAULT_USER = "vmangos"
DEFAULT_IMAGE = "ghcr.io/mserajnik/vmangos-server:{client_version}"
DEFAULT_CLIENT_DATA = "/srv/rewind/client-data/{client_version}"
DEFAULT_BUILD_ROOT = "/srv/rewind/map-builds"
DEFAULT_EXTRACTED_DATA = "/home/vmangos/rewind-deploy/storage/mangosd/extracted-data"
DEFAULT_COMPOSE_FILE = "/home/vmangos/rewind-deploy/compose.yaml"


def default_known_hosts() -> Path:
    if platform.system() == "Windows":
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~"
    else:
        home = os.environ.get("HOME") or "~"
    return Path(os.path.expanduser(home)) / ".ssh" / "known_hosts"


def default_key() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~"
    ssh_dir = Path(os.path.expanduser(home)) / ".ssh"
    # Prefer the modern key name, but make the prompt useful on older setups.
    ed25519 = ssh_dir / "id_ed25519"
    return ed25519 if ed25519.exists() else ssh_dir / "id_rsa"


def prompt_text(label: str, default: str | None = None, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("  A value is required.")


def prompt_int(label: str, default: int) -> int:
    while True:
        value = prompt_text(label, str(default))
        try:
            parsed = int(value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
        print("  Enter a positive whole number.")


def prompt_float(label: str, default: float) -> float:
    while True:
        value = prompt_text(label, str(default))
        try:
            parsed = float(value)
            if parsed >= 0:
                return parsed
        except ValueError:
            pass
        print("  Enter a number greater than or equal to zero.")


def prompt_auth() -> tuple[Path | None, str | None]:
    print("\nSSH authentication:")
    print("  1) Password")
    print("  2) Private key (recommended)")
    print("  3) SSH agent / default key")
    while True:
        choice = prompt_text("Choose authentication", "1")
        if choice == "1":
            return None, getpass.getpass("SSH password (hidden): ")
        if choice == "2":
            key = prompt_text("Private key path", str(default_key()))
            return Path(os.path.expandvars(os.path.expanduser(key))), None
        if choice == "3":
            return None, None
        print("  Choose 1, 2, or 3.")


class MapRebuildShell(cmd.Cmd):
    intro = (
        "\nVMaNGOS map rebuild shell\n"
        "Type 'help' for commands. Type 'check' to run the read-only preflight.\n"
    )
    prompt = "map-rebuild> "

    def __init__(self) -> None:
        super().__init__()
        self.config: PreflightConfig | None = None
        self.profile_name = "production"
        self.store = ProfileStore()
        self.dirty = False
        self._load_saved_configuration()

    def emptyline(self) -> None:
        """Do not repeat the last command when the user presses Enter."""
        return None

    def _load_saved_configuration(self) -> None:
        try:
            self.store.load()
        except ConfigError as exc:
            print(f"Could not load saved settings: {exc}")
            print("Starting with a new configuration.")
            self.configure()
            return
        saved = self.store.get()
        if saved is None:
            self.configure()
            return
        self.profile_name = self.store.active
        self.config = saved
        self._prompt_password_if_needed()
        print(f"Loaded saved profile '{self.profile_name}' from {self.store.path}")

    def _prompt_password_if_needed(self) -> None:
        if self.config is None or self.config.password is not None:
            return
        if self.store.authentication(self.profile_name) == "password":
            self.config = self._replace(password=getpass.getpass("SSH password (hidden): "))

    def configure(self, profile_name: str | None = None) -> None:
        print("\nConfigure this session. Values can be changed later with 'configure' or 'set'.")
        host = prompt_text("Remote server address", self.config.host if self.config else None, required=True)
        user = prompt_text("SSH user", self.config.user if self.config else DEFAULT_USER)
        port = prompt_int("SSH port", self.config.port if self.config else 22)
        key, password = prompt_auth()
        known_hosts = Path(
            os.path.expandvars(
                os.path.expanduser(
                    prompt_text(
                        "Known-hosts file",
                        str(self.config.known_hosts if self.config and self.config.known_hosts else default_known_hosts()),
                    )
                )
            )
        )
        client_version = prompt_text("WoW client version", self.config.client_version if self.config else DEFAULT_CLIENT_VERSION)
        image = prompt_text(
            "VMaNGOS server image",
            self.config.image if self.config else DEFAULT_IMAGE.format(client_version=client_version),
        )
        client_data = prompt_text(
            "Remote complete client-data cache",
            self.config.remote_client_data.as_posix() if self.config else DEFAULT_CLIENT_DATA.format(client_version=client_version),
        )
        build_root = prompt_text("Remote staging/build root", self.config.remote_build_root.as_posix() if self.config else DEFAULT_BUILD_ROOT)
        extracted_data = prompt_text(
            "Remote deployed extracted-data directory",
            self.config.remote_extracted_data.as_posix() if self.config else DEFAULT_EXTRACTED_DATA,
        )
        compose_file = prompt_text("Remote Compose file", self.config.compose_file.as_posix() if self.config else DEFAULT_COMPOSE_FILE)
        compose_service = prompt_text("Compose service", self.config.compose_service if self.config else "mangosd")
        minimum_free_gib = prompt_float("Minimum free disk space in GiB", self.config.minimum_free_gib if self.config else 10.0)
        timeout = prompt_float("SSH command timeout in seconds", self.config.timeout if self.config else 15.0)

        self.config = PreflightConfig(
            host=host,
            user=user,
            port=port,
            key_filename=key,
            password=password,
            known_hosts=known_hosts,
            client_version=client_version,
            image=image,
            remote_client_data=PurePosixPath(client_data),
            remote_build_root=PurePosixPath(build_root),
            remote_extracted_data=PurePosixPath(extracted_data),
            compose_file=PurePosixPath(compose_file),
            compose_service=compose_service,
            minimum_free_gib=minimum_free_gib,
            timeout=timeout,
            check_image=True,
        )
        if profile_name:
            self.profile_name = validate_profile_name(profile_name)
        self.dirty = True
        print(f"\nSession configuration updated for profile '{self.profile_name}'.")

    def do_check(self, arg: str) -> None:
        """Run the read-only preflight checks."""
        del arg
        if self.config is None:
            print("No configuration is loaded. Use 'configure'.")
            return
        print("\nRunning read-only preflight checks...\n")
        try:
            results = run_preflight(self.config)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            return
        except Exception as exc:
            print(f"Preflight could not complete: {exc}")
            return
        for result in results:
            print_result(result)
        print("\nPreflight failed." if any(not r.ok for r in results) else "\nPreflight passed.")

    def do_show(self, arg: str) -> None:
        """Show current session settings. Secrets are never displayed."""
        del arg
        if self.config is None:
            print("No configuration is loaded.")
            return
        config = self.config
        auth = "password" if config.password is not None else "private key" if config.key_filename else "agent/default key"
        values = {
            "host": config.host,
            "user": config.user,
            "port": config.port,
            "authentication": auth,
            "known_hosts": config.known_hosts or "platform default",
            "client_version": config.client_version,
            "image": config.image,
            "remote_client_data": config.remote_client_data,
            "remote_build_root": config.remote_build_root,
            "remote_extracted_data": config.remote_extracted_data,
            "compose_file": config.compose_file,
            "compose_service": config.compose_service,
            "minimum_free_gib": config.minimum_free_gib,
            "timeout": config.timeout,
        }
        for name, value in values.items():
            print(f"{name}: {value}")

    def do_configure(self, arg: str) -> None:
        """Ask all startup questions again."""
        del arg
        self.configure()

    def do_set(self, arg: str) -> None:
        """Change one setting: set <name> <value>. Use 'set password' to prompt securely."""
        if self.config is None:
            print("No configuration is loaded. Use 'configure'.")
            return
        try:
            parts = shlex.split(arg)
        except ValueError as exc:
            print(f"Invalid setting: {exc}")
            return
        if not parts:
            print("Usage: set <name> <value> (or: set password)")
            return
        name = parts[0].lower().replace("-", "_")
        if name == "password":
            password = getpass.getpass("SSH password (hidden): ")
            self.config = self._replace(password=password, key_filename=None)
            print("Password updated for this session.")
            return
        if name == "authentication":
            print("Use 'set password', 'set key_filename PATH', or 'configure'.")
            return
        if len(parts) < 2:
            print("Usage: set <name> <value>")
            return
        value = " ".join(parts[1:])
        try:
            if name in {"port"}:
                value = int(value)
            elif name in {"minimum_free_gib", "timeout"}:
                value = float(value)
            elif name in {"remote_client_data", "remote_build_root", "remote_extracted_data", "compose_file"}:
                value = PurePosixPath(value)
            elif name in {"key_filename", "known_hosts"}:
                value = Path(os.path.expandvars(os.path.expanduser(value)))
            elif name in {"password"}:
                pass
            elif name not in {
                "host", "user", "client_version", "image", "compose_service"
            }:
                print(f"Unknown setting: {name}. Use 'show' to list settings.")
                return
            self.config = self._replace(**{name: value})
            self.dirty = True
            print(f"Updated {name}. Use 'save' to persist it.")
        except (TypeError, ValueError) as exc:
            print(f"Could not update {name}: {exc}")

    def do_save(self, arg: str) -> None:
        """Save current non-secret settings to the active profile."""
        del arg
        if self.config is None:
            print("No configuration is loaded.")
            return
        try:
            self.store.save_profile(self.profile_name, self.config)
        except (ConfigError, OSError) as exc:
            print(f"Could not save settings: {exc}")
            return
        self.dirty = False
        print(f"Saved profile '{self.profile_name}' to {self.store.path}")
        print("The SSH password was not saved; it will be requested next time.")

    def do_profiles(self, arg: str) -> None:
        """List saved profiles."""
        del arg
        if not self.store.profiles:
            print("No saved profiles. Use 'save' to create one.")
            return
        for name in self.store.profiles:
            marker = " (active)" if name == self.profile_name else ""
            print(f"{name}{marker}")

    def do_use(self, arg: str) -> None:
        """Switch profile: use <profile-name>. Unsaved changes are discarded after confirmation."""
        name = arg.strip()
        if not name:
            print("Usage: use <profile-name>")
            return
        try:
            validate_profile_name(name)
        except ConfigError as exc:
            print(exc)
            return
        if name not in self.store.profiles:
            print(f"Profile '{name}' does not exist. Use 'save' to create it.")
            return
        if self.dirty and not self._confirm("Discard unsaved changes?", default=False):
            return
        self.profile_name = name
        self.config = self.store.get(name)
        self.dirty = False
        self._prompt_password_if_needed()
        print(f"Switched to profile '{name}'.")

    def do_new(self, arg: str) -> None:
        """Create a new profile by configuring it: new <profile-name>."""
        name = arg.strip()
        if not name:
            print("Usage: new <profile-name>")
            return
        try:
            validate_profile_name(name)
        except ConfigError as exc:
            print(exc)
            return
        if name in self.store.profiles:
            print(f"Profile '{name}' already exists. Use 'use {name}' instead.")
            return
        if self.dirty and not self._confirm("Discard unsaved changes?", default=False):
            return
        self.profile_name = name
        self.config = None
        self.configure(name)
        print(f"Use 'save' to save the new '{name}' profile.")

    def do_delete(self, arg: str) -> None:
        """Delete a saved profile: delete <profile-name>."""
        name = arg.strip() or self.profile_name
        if not self._confirm(f"Delete profile '{name}'?", default=False):
            return
        try:
            self.store.delete(name)
        except (ConfigError, OSError) as exc:
            print(f"Could not delete profile: {exc}")
            return
        if name == self.profile_name:
            self.profile_name = self.store.active
            self.config = self.store.get()
            self.dirty = False
            self._prompt_password_if_needed()
        print(f"Deleted profile '{name}'.")

    def do_reload(self, arg: str) -> None:
        """Reload saved profiles from disk."""
        del arg
        if self.dirty and not self._confirm("Discard unsaved changes?", default=False):
            return
        try:
            self.store.load()
            self.profile_name = self.store.active
            self.config = self.store.get()
            self.dirty = False
            if self.config:
                self._prompt_password_if_needed()
            print(f"Reloaded settings from {self.store.path}")
        except ConfigError as exc:
            print(f"Could not reload settings: {exc}")

    @staticmethod
    def _confirm(message: str, default: bool) -> bool:
        suffix = " [Y/n]" if default else " [y/N]"
        answer = input(message + suffix + ": ").strip().lower()
        return default if not answer else answer in {"y", "yes"}

    def _replace(self, **changes: object) -> PreflightConfig:
        values = self.config.__dict__.copy()  # type: ignore[union-attr]
        values.update(changes)
        return PreflightConfig(**values)

    def do_help(self, arg: str) -> None:
        if arg:
            super().do_help(arg)
            return
        print(
            """
Commands:
  check                 Run read-only SSH/Docker/server checks
  show                  Show current settings (password is hidden)
  configure             Ask all setup questions again
  set NAME VALUE        Change one setting for this session
  set password          Securely replace the SSH password
  save                  Save non-secret settings to the active profile
  profiles              List saved profiles
  use NAME              Switch to a saved profile
  new NAME              Configure a new profile
  delete [NAME]         Delete a saved profile
  reload                Reload profiles from disk
  help                  Show this help
  exit                  Leave the shell
  quit                  Leave the shell

Implemented now: check.
Future commands will use this same session for syncing ADTs, extraction,
and deployment.
"""
        )

    def do_exit(self, arg: str) -> bool:
        del arg
        if self.dirty and self._confirm("Save changes before exiting?", default=True):
            self.do_save("")
        print("Goodbye.")
        return True

    def do_quit(self, arg: str) -> bool:
        return self.do_exit(arg)

    def do_EOF(self, arg: str) -> bool:
        print()
        return self.do_exit(arg)


def run_shell() -> int:
    try:
        MapRebuildShell().cmdloop()
    except KeyboardInterrupt:
        print("\nGoodbye.")
    except EOFError:
        print("\nGoodbye.")
    return 0
