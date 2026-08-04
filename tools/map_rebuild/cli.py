"""Command-line entry point for the read-only map rebuild preflight."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from .preflight import CheckResult, PreflightConfig, run_preflight


@dataclass(frozen=True)
class CliConfig:
    """Values collected from command-line arguments."""

    preflight: PreflightConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="map-rebuild",
        description="Safely rebuild VMaNGOS map data on a remote Docker host.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check",
        help="Run read-only local and remote preflight checks.",
        description=(
            "Run read-only checks. This command does not upload files, run the "
            "extractor, stop containers, or change deployed data."
        ),
    )
    check.add_argument(
        "--host",
        required=True,
        help="SSH host name or address.",
    )
    check.add_argument(
        "--user",
        required=True,
        help="SSH user name.",
    )
    check.add_argument(
        "--port",
        type=int,
        default=22,
        help="SSH port (default: 22).",
    )
    check.add_argument(
        "--key",
        type=Path,
        help="Private SSH key file. If omitted, Paramiko searches the SSH agent and default keys.",
    )
    password = check.add_mutually_exclusive_group()
    password.add_argument(
        "--password",
        help=(
            "SSH password. Avoid this option because the value may be visible in "
            "shell history or process listings. Prefer --prompt-password or --password-env."
        ),
    )
    password.add_argument(
        "--password-env",
        metavar="VARIABLE",
        help="Read the SSH password from the named environment variable.",
    )
    password.add_argument(
        "--prompt-password",
        action="store_true",
        help="Prompt securely for the SSH password without echoing it.",
    )
    check.add_argument(
        "--known-hosts",
        type=Path,
        help="Known-hosts file. Defaults to the platform SSH known-hosts location.",
    )
    check.add_argument(
        "--client-version",
        default="5875",
        help="VMaNGOS client build (default: 5875).",
    )
    check.add_argument(
        "--image",
        help="Exact VMaNGOS server image to use; defaults to the standard 5875 image.",
    )
    check.add_argument(
        "--remote-client-data",
        default="/srv/rewind/client-data/{client_version}",
        help="Remote complete client-data cache. {client_version} is substituted.",
    )
    check.add_argument(
        "--remote-build-root",
        default="/srv/rewind/map-builds",
        help="Remote staging/build root.",
    )
    check.add_argument(
        "--remote-extracted-data",
        default="/home/vmangos/rewind-deploy/storage/mangosd/extracted-data",
        help="Remote currently deployed extracted-data directory.",
    )
    check.add_argument(
        "--compose-file",
        default="/home/vmangos/rewind-deploy/compose.yaml",
        help="Remote Docker Compose file to inspect.",
    )
    check.add_argument(
        "--compose-service",
        default="mangosd",
        help="Compose service that consumes extracted data (default: mangosd).",
    )
    check.add_argument(
        "--min-free-gib",
        type=float,
        default=10.0,
        help="Minimum remote free space required in GiB (default: 10).",
    )
    check.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="SSH command timeout in seconds (default: 15).",
    )
    check.add_argument(
        "--skip-image-check",
        action="store_true",
        help="Do not inspect the Docker image locally or remotely.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> CliConfig:
    image = args.image or f"ghcr.io/mserajnik/vmangos-server:{args.client_version}"
    password = args.password
    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            raise ValueError(
                f"SSH password environment variable is not set: {args.password_env}"
            )
    if args.prompt_password:
        password = getpass.getpass("SSH password: ")
    return CliConfig(
        preflight=PreflightConfig(
            host=args.host,
            user=args.user,
            port=args.port,
            key_filename=args.key,
            password=password,
            known_hosts=args.known_hosts,
            client_version=args.client_version,
            image=image,
            # Remote paths are always POSIX paths, even when this CLI runs
            # from Windows. Do not construct them with pathlib.Path.
            remote_client_data=PurePosixPath(
                args.remote_client_data.format(client_version=args.client_version)
            ),
            remote_build_root=PurePosixPath(args.remote_build_root),
            remote_extracted_data=PurePosixPath(args.remote_extracted_data),
            compose_file=PurePosixPath(args.compose_file),
            compose_service=args.compose_service,
            minimum_free_gib=args.min_free_gib,
            timeout=args.timeout,
            check_image=not args.skip_image_check,
        )
    )


def print_result(result: CheckResult) -> None:
    prefix = "✓" if result.ok else "✗"
    print(f"{prefix} {result.message}")
    if result.detail:
        for line in result.detail.splitlines():
            print(f"  {line}")


def run_check(args: argparse.Namespace) -> int:
    try:
        config = config_from_args(args)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    print("Running read-only preflight checks...\n")

    try:
        results = run_preflight(config.preflight)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # Keep CLI failures concise and user-facing.
        print(f"✗ Preflight could not complete: {exc}", file=sys.stderr)
        return 1

    for result in results:
        print_result(result)

    failed = any(not result.ok for result in results)
    print("\nPreflight failed." if failed else "\nPreflight passed.")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return run_check(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
