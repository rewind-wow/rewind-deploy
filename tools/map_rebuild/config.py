"""Persistent, non-secret profile storage for the interactive map shell."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import tempfile
from typing import Any

from .preflight import PreflightConfig

CONFIG_VERSION = 1
DEFAULT_PROFILE = "production"
_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    """Raised when saved configuration cannot be read or validated."""


def config_path() -> Path:
    """Return an OS-appropriate per-user configuration path."""
    if platform.system() == "Windows":
        root = os.environ.get("APPDATA") or os.environ.get("USERPROFILE") or "~"
        return Path(os.path.expanduser(root)) / "RewindWoW" / "map-rebuild" / "config.json"
    if platform.system() == "Darwin":
        root = os.environ.get("HOME") or "~"
        return (
            Path(os.path.expanduser(root))
            / "Library"
            / "Application Support"
            / "RewindWoW"
            / "map-rebuild"
            / "config.json"
        )
    root = os.environ.get("XDG_CONFIG_HOME")
    base = (
        Path(os.path.expandvars(os.path.expanduser(root)))
        if root
        else Path(os.path.expanduser(os.environ.get("HOME", "~"))) / ".config"
    )
    return base / "rewind-wow" / "map-rebuild" / "config.json"


def validate_profile_name(name: str) -> str:
    if not _PROFILE_RE.fullmatch(name):
        raise ConfigError(
            "Profile names must start with a letter or number and contain only "
            "letters, numbers, '-' or '_'."
        )
    return name


def _local_path(value: Any, field: str) -> Path | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a path string")
    return Path(os.path.expandvars(os.path.expanduser(value)))


def _remote_path(value: Any, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be a non-empty remote path string")
    return PurePosixPath(value)


def profile_from_config(config: PreflightConfig) -> dict[str, Any]:
    """Serialize connection settings, deliberately excluding the password."""
    return {
        "host": config.host,
        "user": config.user,
        "port": config.port,
        "key_filename": str(config.key_filename) if config.key_filename else None,
        "known_hosts": str(config.known_hosts) if config.known_hosts else None,
        "authentication": "password"
        if config.password is not None
        else "key"
        if config.key_filename
        else "agent",
        "client_version": config.client_version,
        "image": config.image,
        "remote_client_data": config.remote_client_data.as_posix(),
        "remote_build_root": config.remote_build_root.as_posix(),
        "remote_extracted_data": config.remote_extracted_data.as_posix(),
        "compose_file": config.compose_file.as_posix(),
        "compose_service": config.compose_service,
        "minimum_free_gib": config.minimum_free_gib,
        "timeout": config.timeout,
    }


def config_from_profile(data: dict[str, Any]) -> PreflightConfig:
    """Deserialize a profile; any password is intentionally not loaded."""
    required = (
        "host",
        "user",
        "port",
        "client_version",
        "image",
        "remote_client_data",
        "remote_build_root",
        "remote_extracted_data",
        "compose_file",
        "compose_service",
        "minimum_free_gib",
        "timeout",
    )
    missing = [field for field in required if field not in data]
    if missing:
        raise ConfigError("Profile is missing: " + ", ".join(missing))

    authentication = data.get("authentication", "agent")
    if authentication not in {"password", "key", "agent"}:
        raise ConfigError("authentication must be password, key, or agent")
    key = _local_path(data.get("key_filename"), "key_filename")
    if authentication == "key" and key is None:
        raise ConfigError("key authentication requires key_filename")

    try:
        port = int(data["port"])
        minimum_free_gib = float(data["minimum_free_gib"])
        timeout = float(data["timeout"])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid numeric profile value: {exc}") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("port must be between 1 and 65535")
    if minimum_free_gib < 0 or timeout <= 0:
        raise ConfigError("minimum_free_gib must be non-negative and timeout positive")

    return PreflightConfig(
        host=str(data["host"]),
        user=str(data["user"]),
        port=port,
        key_filename=key,
        password=None,
        known_hosts=_local_path(data.get("known_hosts"), "known_hosts"),
        client_version=str(data["client_version"]),
        image=str(data["image"]),
        remote_client_data=_remote_path(data["remote_client_data"], "remote_client_data"),
        remote_build_root=_remote_path(data["remote_build_root"], "remote_build_root"),
        remote_extracted_data=_remote_path(
            data["remote_extracted_data"], "remote_extracted_data"
        ),
        compose_file=_remote_path(data["compose_file"], "compose_file"),
        compose_service=str(data["compose_service"]),
        minimum_free_gib=minimum_free_gib,
        timeout=timeout,
        check_image=True,
    )


class ProfileStore:
    """Versioned JSON store containing profiles but no passwords."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()
        self.active = DEFAULT_PROFILE
        self.profiles: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Could not read {self.path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
            raise ConfigError(f"Unsupported configuration format in {self.path}")
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            raise ConfigError("profiles must be an object")

        self.active = validate_profile_name(str(data.get("active_profile", DEFAULT_PROFILE)))
        self.profiles = {}
        for name, profile in profiles.items():
            name = validate_profile_name(str(name))
            if not isinstance(profile, dict):
                raise ConfigError(f"Profile '{name}' must be an object")
            config_from_profile(profile)  # Validate before exposing it to the shell.
            self.profiles[name] = profile
        if self.active not in self.profiles and self.profiles:
            self.active = next(iter(self.profiles))

    def get(self, name: str | None = None) -> PreflightConfig | None:
        profile = self.profiles.get(name or self.active)
        return config_from_profile(profile) if profile else None

    def authentication(self, name: str | None = None) -> str:
        profile = self.profiles.get(name or self.active)
        return str(profile.get("authentication", "agent")) if profile else "agent"

    def save_profile(self, name: str, config: PreflightConfig) -> None:
        name = validate_profile_name(name)
        self.profiles[name] = profile_from_config(config)
        self.active = name
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CONFIG_VERSION,
            "active_profile": self.active,
            "profiles": self.profiles,
        }
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if platform.system() != "Windows":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def delete(self, name: str) -> None:
        name = validate_profile_name(name)
        if name not in self.profiles:
            raise ConfigError(f"Profile '{name}' does not exist")
        if len(self.profiles) == 1:
            raise ConfigError("Cannot delete the last profile")
        del self.profiles[name]
        if self.active == name:
            self.active = next(iter(self.profiles))
        self.save()
