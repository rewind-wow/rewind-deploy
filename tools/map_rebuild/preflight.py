"""Read-only local and remote checks for the map rebuild workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import platform
import shlex
import shutil
from typing import Callable, Sequence

try:
    import paramiko
except ModuleNotFoundError:  # Keep --help usable before dependencies are installed.
    paramiko = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PreflightConfig:
    host: str
    user: str
    port: int
    key_filename: Path | None
    password: str | None
    known_hosts: Path | None
    client_version: str
    image: str
    remote_client_data: PurePosixPath
    remote_build_root: PurePosixPath
    remote_extracted_data: PurePosixPath
    compose_file: PurePosixPath
    compose_service: str
    minimum_free_gib: float
    timeout: float
    check_image: bool = True


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str
    detail: str = ""


@dataclass(frozen=True)
class RemoteResult:
    exit_code: int
    stdout: str
    stderr: str


class RemoteConnection:
    """Small Paramiko wrapper that never executes a shell command locally."""

    def __init__(self, client: "paramiko.SSHClient", timeout: float) -> None:
        self.client = client
        self.timeout = timeout

    def run(self, command: str) -> RemoteResult:
        stdin, stdout, stderr = self.client.exec_command(command, timeout=self.timeout)
        # The command is expected to be non-interactive. Close stdin so commands
        # cannot accidentally wait for input from a preflight invocation.
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return RemoteResult(stdout.channel.recv_exit_status(), out, err)

    def close(self) -> None:
        self.client.close()


def _default_known_hosts() -> Path:
    if platform.system() == "Windows":
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~"
    else:
        home = os.environ.get("HOME") or "~"
    return Path(os.path.expanduser(home)) / ".ssh" / "known_hosts"


def _expanded_path(path: Path) -> Path:
    """Expand user-provided paths consistently on Windows and Unix."""
    return Path(os.path.expandvars(os.path.expanduser(str(path))))


def connect(config: PreflightConfig) -> tuple[RemoteConnection | None, CheckResult]:
    if paramiko is None:
        return None, CheckResult(
            False,
            "Paramiko is not installed",
            "Install the tool with: python -m pip install -e .",
        )

    client = paramiko.SSHClient()
    known_hosts = _expanded_path(config.known_hosts) if config.known_hosts else _default_known_hosts()
    try:
        if known_hosts.is_file():
            client.load_host_keys(str(known_hosts))
        else:
            return None, CheckResult(
                False,
                "SSH known-hosts file not found",
                f"Expected {known_hosts}. Add the server with the platform ssh tool or provide --known-hosts.",
            )

        # Reject unknown or changed host keys. Never use AutoAddPolicy here.
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=config.host,
            port=config.port,
            username=config.user,
            key_filename=(str(_expanded_path(config.key_filename)) if config.key_filename else None),
            password=config.password,
            allow_agent=config.password is None,
            look_for_keys=config.key_filename is None and config.password is None,
            timeout=config.timeout,
            banner_timeout=config.timeout,
            auth_timeout=config.timeout,
        )
        return RemoteConnection(client, config.timeout), CheckResult(
            True,
            "SSH connection established",
            f"{config.user}@{config.host}:{config.port}",
        )
    except (OSError, paramiko.SSHException) as exc:
        client.close()
        return None, CheckResult(False, "SSH connection failed", str(exc))


def _run_local(command: Sequence[str], timeout: float) -> tuple[int, str, str]:
    import subprocess

    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def _remote_test(remote: RemoteConnection, command: str) -> RemoteResult:
    return remote.run(command)


def _check_local_ssh() -> CheckResult:
    if shutil.which("ssh"):
        return CheckResult(True, "Local SSH client available", shutil.which("ssh") or "")
    return CheckResult(
        True,
        "Local SSH client not installed; using Paramiko",
        "This is supported on Windows and does not prevent preflight checks.",
    )


def _check_local_key(config: PreflightConfig) -> CheckResult:
    if config.key_filename is None:
        return CheckResult(True, "No explicit SSH key requested", "Paramiko will use the SSH agent or default keys")
    key = _expanded_path(config.key_filename)
    if key.is_file():
        return CheckResult(True, "SSH private key file exists", str(key))
    return CheckResult(False, "SSH private key file is missing", str(key))


def _check_local_python() -> CheckResult:
    return CheckResult(True, "Python runtime available", platform.python_version())


def _check_remote_command(
    remote: RemoteConnection,
    command: str,
    success_message: str,
    failure_message: str,
) -> CheckResult:
    result = _remote_test(remote, command)
    if result.exit_code == 0:
        return CheckResult(True, success_message, result.stdout.strip())
    detail = (result.stderr or result.stdout).strip()
    return CheckResult(False, failure_message, detail)


def _check_remote_docker(remote: RemoteConnection) -> CheckResult:
    return _check_remote_command(
        remote,
        "docker version --format '{{.Server.Version}}'",
        "Remote Docker is available",
        "Remote Docker is unavailable",
    )


def _check_remote_path(
    remote: RemoteConnection,
    path: Path,
    label: str,
    must_be_directory: bool = True,
) -> CheckResult:
    quoted = shlex.quote(path.as_posix())
    test = "-d" if must_be_directory else "-e"
    result = remote.run(f"test {test} {quoted}")
    if result.exit_code == 0:
        return CheckResult(True, f"{label} exists", path.as_posix())
    return CheckResult(False, f"{label} is missing", path.as_posix())


def _check_remote_writable_build_root(
    remote: RemoteConnection, build_root: Path
) -> CheckResult:
    quoted = shlex.quote(build_root.as_posix())
    # This creates nothing: test the existing directory and use a shell redirection
    # only against /dev/null. The directory must already exist and be writable.
    command = f"test -d {quoted} && test -w {quoted}"
    result = remote.run(command)
    if result.exit_code == 0:
        return CheckResult(True, "Remote staging directory is writable", build_root.as_posix())
    return CheckResult(
        False,
        "Remote staging directory is missing or not writable",
        build_root.as_posix(),
    )


def _check_remote_free_space(
    remote: RemoteConnection, path: Path, minimum_gib: float
) -> CheckResult:
    quoted = shlex.quote(path.as_posix())
    result = remote.run(f"df -Pk {quoted}")
    if result.exit_code != 0:
        return CheckResult(False, "Unable to determine remote free space", result.stderr.strip())
    lines = result.stdout.strip().splitlines()
    if len(lines) < 2:
        return CheckResult(False, "Unable to parse remote free space", result.stdout.strip())
    try:
        available_kib = int(lines[-1].split()[3])
    except (IndexError, ValueError) as exc:
        return CheckResult(False, "Unable to parse remote free space", str(exc))
    available_gib = available_kib / (1024 * 1024)
    detail = f"{available_gib:.1f} GiB available; {minimum_gib:.1f} GiB required"
    return CheckResult(
        available_gib >= minimum_gib,
        "Remote disk space is sufficient" if available_gib >= minimum_gib else "Remote disk space is insufficient",
        detail,
    )


def _check_compose_service(remote: RemoteConnection, config: PreflightConfig) -> CheckResult:
    compose = shlex.quote(config.compose_file.as_posix())
    service = shlex.quote(config.compose_service)
    command = f"test -f {compose} && docker compose -f {compose} config --services"
    result = remote.run(command)
    if result.exit_code != 0:
        return CheckResult(
            False,
            "Remote Docker Compose configuration is unavailable or invalid",
            (result.stderr or result.stdout).strip(),
        )
    services = result.stdout.split()
    if config.compose_service not in services:
        return CheckResult(
            False,
            f"Compose service '{config.compose_service}' was not found",
            "Services: " + ", ".join(services),
        )
    return CheckResult(True, "Remote Docker Compose service found", config.compose_service)


def _check_remote_image(remote: RemoteConnection, image: str) -> CheckResult:
    quoted = shlex.quote(image)
    result = remote.run(f"docker image inspect {quoted} >/dev/null 2>&1")
    if result.exit_code == 0:
        return CheckResult(True, "VMaNGOS extractor image is available", image)
    return CheckResult(
        False,
        "VMaNGOS extractor image is not available",
        f"{image} (preflight does not pull images)",
    )


def _check_no_active_build(remote: RemoteConnection, build_root: Path) -> CheckResult:
    # A future build command will create this marker. For now, this check only
    # detects the agreed-upon lock marker and does not create or remove anything.
    marker = shlex.quote(PurePosixPath(build_root, ".map-rebuild.lock").as_posix())
    result = remote.run(f"test ! -e {marker}")
    if result.exit_code == 0:
        return CheckResult(True, "No active map rebuild was detected")
    return CheckResult(False, "Another map rebuild appears to be active", marker)


def run_preflight(config: PreflightConfig) -> list[CheckResult]:
    """Run checks in order without mutating local or remote state."""
    results = [_check_local_python(), _check_local_ssh(), _check_local_key(config)]
    remote, ssh_result = connect(config)
    results.append(ssh_result)
    if remote is None:
        return results

    try:
        results.extend(
            [
                _check_remote_command(
                    remote,
                    "id -un",
                    "Remote user is accessible",
                    "Unable to determine remote user",
                ),
                _check_remote_docker(remote),
                _check_remote_path(remote, config.remote_client_data, "Remote client-data cache"),
                _check_remote_path(remote, config.remote_build_root, "Remote staging directory"),
                _check_remote_path(remote, config.remote_extracted_data, "Remote deployed extracted-data"),
                _check_remote_writable_build_root(remote, config.remote_build_root),
                _check_remote_free_space(
                    remote, config.remote_build_root, config.minimum_free_gib
                ),
                _check_compose_service(remote, config),
                _check_no_active_build(remote, config.remote_build_root),
            ]
        )
        if config.check_image:
            results.append(_check_remote_image(remote, config.image))
    finally:
        remote.close()
    return results
