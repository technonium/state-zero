#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Mapping


EXPECTED_ENV = {
    "STATE_ZERO_PRIVATE_ROOT": "/opt/state-zero-private",
    "PIPELINE_MEDIA_MODE": "live_vps",
    "VPS_SSH_PATH": "/srv/state-zero-media",
}

EXPECTED_MOUNTS = (
    ("/opt/state-zero-private", "/opt/state-zero-private"),
    ("/srv/state-zero-media", "/srv/state-zero-media"),
)

EXPECTED_HOST_PATHS = (
    "/opt/state-zero-private/astrology/natal.yaml",
    "/opt/state-zero-private/astrology/dasha_periods.yaml",
    "/opt/state-zero-private/runtime/database/cards.db",
    "/opt/state-zero-private/runtime/output",
    "/opt/state-zero-private/runtime/state",
    "/srv/state-zero-media/fallback/error_404_v1/card.mp4",
    "/srv/state-zero-media/fallback/error_404_v1/card.png",
)


def check_env(env: Mapping[str, str]) -> list[str]:
    findings = []
    for key, expected in EXPECTED_ENV.items():
        actual = (env.get(key) or "").strip()
        if actual != expected:
            findings.append(f"{key} expected {expected!r}, got {actual!r}")
    return findings


def check_mounts(container_spec: Mapping) -> list[str]:
    mounts = container_spec.get("Mounts") or []
    findings = []
    for source, target in EXPECTED_MOUNTS:
        matched = False
        for mount in mounts:
            if mount.get("Source") == source and mount.get("Target") == target:
                matched = True
                if mount.get("ReadOnly") is True:
                    findings.append(f"mount {source} -> {target} is read-only")
                break
        if not matched:
            findings.append(f"missing bind mount {source} -> {target}")
    return findings


def service_env(container_spec: Mapping) -> dict[str, str]:
    parsed = {}
    for item in container_spec.get("Env") or []:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def build_host_path_probe_command(paths: tuple[str, ...] = EXPECTED_HOST_PATHS) -> str:
    checks = []
    for path in paths:
        quoted = shlex.quote(path)
        checks.append(f'if [ -e {quoted} ]; then printf "OK\\t%s\\n" {quoted}; else printf "MISSING\\t%s\\n" {quoted}; fi')
    return "; ".join(checks)


def parse_host_path_probe(output: str) -> list[str]:
    findings = []
    for line in output.splitlines():
        status, _, path = line.partition("\t")
        if status == "MISSING" and path:
            findings.append(f"missing host path {path}")
    return findings


def run_ssh(
    *,
    host: str,
    user: str,
    command: str,
    identity_file: Path | None,
) -> subprocess.CompletedProcess[str]:
    target = f"{user}@{host}"
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
    ]
    if identity_file is not None:
        args.extend(["-i", str(identity_file), "-o", "IdentitiesOnly=yes"])
    args.extend([target, command])
    return subprocess.run(args, capture_output=True, text=True, check=False)


def check_remote(host: str, user: str, service: str, identity_file: Path | None) -> list[str]:
    findings = []
    inspect_cmd = f"docker service inspect {shlex.quote(service)} --format '{{{{json .Spec.TaskTemplate.ContainerSpec}}}}'"
    inspect_result = run_ssh(
        host=host,
        user=user,
        command=inspect_cmd,
        identity_file=identity_file,
    )
    if inspect_result.returncode != 0:
        return [f"failed to inspect Docker service {service}: {inspect_result.stderr.strip()}"]

    try:
        container_spec = json.loads(inspect_result.stdout)
    except json.JSONDecodeError as error:
        return [f"failed to parse Docker service inspect JSON: {error}"]

    findings.extend(check_env(service_env(container_spec)))
    findings.extend(check_mounts(container_spec))

    path_result = run_ssh(
        host=host,
        user=user,
        command=build_host_path_probe_command(),
        identity_file=identity_file,
    )
    if path_result.returncode != 0:
        findings.append(f"failed to inspect expected host paths: {path_result.stderr.strip()}")
    else:
        findings.extend(parse_host_path_probe(path_result.stdout))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Optional safety check for Dokploy/Docker Swarm deployments that use "
            "the documented State Zero VPS media layout."
        )
    )
    parser.add_argument("--ssh-host", help="VPS host to inspect over SSH.")
    parser.add_argument("--ssh-user", default="root", help="SSH user for the VPS.")
    parser.add_argument("--identity-file", type=Path, help="SSH private key to use.")
    parser.add_argument("--service", help="Docker service name to inspect.")
    args = parser.parse_args()

    if args.ssh_host:
        if not args.service:
            parser.error("--service is required when --ssh-host is provided")
        findings = check_remote(args.ssh_host, args.ssh_user, args.service, args.identity_file)
    else:
        findings = check_env(os.environ)

    if findings:
        print("Dokploy deployment safety check failed:")
        for finding in findings:
            print(f"- {finding}")
        raise SystemExit(1)

    print("Dokploy deployment safety check passed.")


if __name__ == "__main__":
    main()
