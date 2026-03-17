#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DISALLOWED_EXACT = {
    ".env",
    ".DS_Store",
}

DISALLOWED_PREFIXES = (
    ".claude/",
    ".kilocode/",
    ".playwright/",
    "runtime/",
    "output/",
    "database/",
    "gallery/",
    "local_vps/",
)

DISALLOWED_SEGMENTS = (
    "/__pycache__/",
    "__pycache__/",
)

DISALLOWED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}

ALLOWED_MEDIA_PREFIXES = (
    "src/assets/",
    "docs/",
)

DISALLOWED_NAME_PREFIXES = (
    "test_out_",
    "_test_",
    "card_final",
)

SECRET_PATTERNS = {
    "openai-style key": re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    "google api key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "github token": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "meta access token": re.compile(r"EAACEdEose0cBA[0-9A-Za-z]+"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
}


def get_tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_working_tree_paths() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            _old_path, new_path = entry.split(" -> ", 1)
            entry = new_path.strip()
        if entry:
            paths.append(entry)
    return paths


def classify_path(path_str: str) -> str | None:
    if path_str in DISALLOWED_EXACT:
        return f"tracked local/private file: {path_str}"

    for prefix in DISALLOWED_PREFIXES:
        if path_str.startswith(prefix):
            return f"tracked ignored/local directory content: {path_str}"

    for segment in DISALLOWED_SEGMENTS:
        if segment in path_str:
            return f"tracked Python cache artifact: {path_str}"

    path = Path(path_str)
    if any(path.name.startswith(prefix) for prefix in DISALLOWED_NAME_PREFIXES):
        return f"tracked generated smoke/output artifact: {path_str}"

    suffix = path.suffix.lower()
    if suffix in DISALLOWED_SUFFIXES and not path_str.startswith(ALLOWED_MEDIA_PREFIXES):
        return f"tracked generated/binary artifact outside allowed docs/assets paths: {path_str}"

    return None


def scan_for_secret_patterns(paths: list[str]) -> list[str]:
    findings = []
    for rel_path in paths:
        suffix = Path(rel_path).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ttf", ".mp4", ".mov", ".avi", ".mkv"}:
            continue

        abs_path = PROJECT_ROOT / rel_path
        if not abs_path.exists():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as error:
            findings.append(f"could not read {rel_path}: {error}")
            continue

        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{label} pattern matched in {rel_path}")
    return findings


def check_tracked_files() -> list[str]:
    tracked_files = get_tracked_files()
    path_findings = [finding for path in tracked_files if (finding := classify_path(path))]
    secret_findings = scan_for_secret_patterns(tracked_files)
    return path_findings + secret_findings


def check_working_tree() -> list[str]:
    return [finding for path in get_working_tree_paths() if (finding := classify_path(path))]


def main():
    parser = argparse.ArgumentParser(description="Check repository hygiene.")
    parser.add_argument(
        "--check-working-tree",
        action="store_true",
        help="Also inspect dirty/untracked working-tree paths for local junk patterns.",
    )
    args = parser.parse_args()

    findings = check_tracked_files()
    if args.check_working_tree:
        findings.extend(check_working_tree())

    if findings:
        print("Repository hygiene check failed:")
        for finding in findings:
            print(f"- {finding}")
        raise SystemExit(1)

    print("Repository hygiene check passed.")


if __name__ == "__main__":
    main()
