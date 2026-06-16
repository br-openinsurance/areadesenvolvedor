#!/usr/bin/env python3
"""Sync docs/config/apis.json with all spec files found in docs/specs.

Scans docs/specs/<group>/<stage>/<version>/<file> and rebuilds apis.json
from scratch. Entries whose files no longer exist are removed; moved files
(e.g. certifying → deprecated) are updated automatically.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, Tuple


VALID_EXTENSIONS = {".yaml", ".yml", ".json"}

OPENAPI_REGEX = re.compile(r"(?m)^\s*(openapi|swagger)\s*:")
TITLE_REGEX = re.compile(r'(?m)^[ \t]{0,12}title\s*:\s*["\']?([^"\n\'#]+)')
VERSION_REGEX = re.compile(r'(?m)^[ \t]{0,12}version\s*:\s*["\']?([^"\n\'#]+)')


def clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().strip('"').strip("'").strip()
    return normalized or None


def read_spec_meta(file_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Return (title, version) from an OpenAPI spec file, or (None, None) if invalid."""
    suffix = file_path.suffix.lower()
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None

    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None, None
        if not isinstance(payload, dict):
            return None, None
        if "openapi" not in payload and "swagger" not in payload:
            return None, None
        info = payload.get("info", {}) if isinstance(payload.get("info"), dict) else {}
        title = info.get("title") if isinstance(info.get("title"), str) else None
        version = info.get("version") if isinstance(info.get("version"), str) else None
        return clean_value(title), clean_value(version)

    if not OPENAPI_REGEX.search(text):
        return None, None

    title_match = TITLE_REGEX.search(text)
    version_match = VERSION_REGEX.search(text)
    return (
        clean_value(title_match.group(1) if title_match else None),
        clean_value(version_match.group(1) if version_match else None),
    )


def readable_name(title: Optional[str], api_key: str, version: Optional[str], stage: str) -> str:
    if title:
        base = title
    else:
        base = re.sub(r"[-_]+", " ", api_key).strip().title()

    if version and version.lower() not in base.lower():
        base = f"{base} - v{version}"

    return f"{base} ({stage})"


def version_sort_key(version: Optional[str]) -> tuple:
    if not version:
        return (0,)
    parts = re.split(r"[.-]", version)
    normalized = []
    for part in parts:
        if part.isdigit():
            normalized.append(int(part))
        else:
            normalized.append(part.lower())
    return tuple(normalized)


STAGE_ORDER = {"certifying": 0, "current": 1, "deprecated": 2, "retired": 3}


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    specs_dir = repo_root / "docs" / "specs"
    apis_json_path = repo_root / "docs" / "config" / "apis.json"

    if not specs_dir.exists():
        raise SystemExit(f"specs directory not found: {specs_dir}")

    entries: list[dict] = []
    invalid: list[str] = []

    # Rebuild entirely from docs/specs/<group>/<stage>/<version>/<filename>
    for file_path in sorted(specs_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in VALID_EXTENSIONS:
            continue

        rel = file_path.relative_to(specs_dir)
        parts = rel.parts

        # Expect exactly 4 parts: group / stage / version / filename
        if len(parts) != 4:
            continue

        group, stage, version_folder, filename = parts
        relative_url = f"./specs/{group}/{stage}/{version_folder}/{filename}"

        title, version = read_spec_meta(file_path)
        if title is None and version is None:
            invalid.append(relative_url)
            continue

        name = readable_name(title, group, version or version_folder, stage)
        entries.append(
            {
                "url": relative_url,
                "name": name,
                "group": group,
                "stage": stage,
                "version": version or version_folder,
            }
        )

    entries.sort(
        key=lambda e: (
            e["group"],
            STAGE_ORDER.get(e["stage"], 99),
            version_sort_key(e.get("version")),
            e["url"],
        )
    )

    apis_json_path.write_text(
        json.dumps({"urls": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("sync_apis_json complete")
    print(f"  Total entries written : {len(entries)}")
    if invalid:
        print(f"  Skipped (not valid OpenAPI) : {len(invalid)}")
        for url in invalid:
            print(f"    - {url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
