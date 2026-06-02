#!/usr/bin/env python3
"""Build static Swagger UI pages for GitHub Pages.

Scans:
- documentation/source/files/swagger
- documentation/source/files/swagger/current

Publishes:
- docs/specs/*
- docs/config/apis.json
- docs/.nojekyll
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


VALID_EXTENSIONS = {".yaml", ".yml", ".json"}
OPENAPI_REGEX = re.compile(r"(?m)^\s*(openapi|swagger)\s*:")
TITLE_REGEX = re.compile(r'(?m)^[ \t]{0,12}title\s*:\s*["\']?([^"\n\'#]+)')
VERSION_REGEX = re.compile(r'(?m)^[ \t]{0,12}version\s*:\s*["\']?([^"\n\'#]+)')


@dataclass
class CandidateSpec:
    source: Path
    relative_from_swagger: str
    publish_relative: str
    source_group: str
    title: Optional[str]
    version: Optional[str]


def is_openapi_spec(file_path: Path) -> Tuple[bool, Optional[str], Optional[str]]:
    suffix = file_path.suffix.lower()
    text = file_path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return False, None, None

        if not isinstance(payload, dict):
            return False, None, None

        if "openapi" not in payload and "swagger" not in payload:
            return False, None, None

        info = payload.get("info", {}) if isinstance(payload.get("info"), dict) else {}
        title = info.get("title") if isinstance(info.get("title"), str) else None
        version = info.get("version") if isinstance(info.get("version"), str) else None
        return True, clean_value(title), clean_value(version)

    if not OPENAPI_REGEX.search(text):
        return False, None, None

    title_match = TITLE_REGEX.search(text)
    version_match = VERSION_REGEX.search(text)
    title = clean_value(title_match.group(1) if title_match else None)
    version = clean_value(version_match.group(1) if version_match else None)
    return True, title, version


def clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().strip('"').strip("'").strip()
    return normalized or None


def readable_name(
    publish_relative: str, title: Optional[str], version: Optional[str], source_group: str
) -> str:
    if title:
        if version and version.lower() not in title.lower():
            base_name = f"{title} - v{version}"
        else:
            base_name = title
        return f"{base_name} ({source_group})"

    stem = Path(publish_relative).stem
    pretty = re.sub(r"[-_]+", " ", stem).strip()
    pretty = re.sub(r"\s+", " ", pretty).title() if pretty else stem
    if version:
        base_name = f"{pretty} - v{version}"
    else:
        base_name = pretty
    return f"{base_name} ({source_group})"


def collect_candidates(swagger_root: Path) -> Tuple[List[CandidateSpec], List[Path]]:
    candidates: List[CandidateSpec] = []
    invalid: List[Path] = []

    for file_path in sorted(swagger_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in VALID_EXTENSIONS:
            continue

        relative = file_path.relative_to(swagger_root).as_posix()
        from_current = relative.startswith("current/")
        source_group = "current" if from_current else "base"
        publish_relative = relative

        valid, title, version = is_openapi_spec(file_path)
        if not valid:
            invalid.append(file_path)
            continue

        candidates.append(
            CandidateSpec(
                source=file_path,
                relative_from_swagger=relative,
                publish_relative=publish_relative,
                source_group=source_group,
                title=title,
                version=version,
            )
        )

    return candidates, invalid


def resolve_unique_publish_paths(candidates: List[CandidateSpec]) -> Dict[str, CandidateSpec]:
    selected: Dict[str, CandidateSpec] = {}

    for candidate in candidates:
        key = candidate.publish_relative.lower()
        selected[key] = candidate

    return selected

def api_group_key(publish_relative: str) -> str:
    parts = Path(publish_relative).parts
    if parts and parts[0].lower() == "current":
        parts = parts[1:]
    return Path(*parts).as_posix().lower()


def write_output(
    root: Path, selected: Dict[str, CandidateSpec]
) -> Tuple[List[dict], List[Tuple[str, Path, str]]]:
    docs_dir = root / "docs"
    specs_dir = docs_dir / "specs"
    config_dir = docs_dir / "config"

    specs_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    copied_files: List[Tuple[str, Path, str]] = []
    api_urls: List[dict] = []

    for candidate in sorted(selected.values(), key=lambda item: item.publish_relative.lower()):
        destination = specs_dir / Path(candidate.publish_relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate.source, destination)

        relative_url = f"./specs/{Path(candidate.publish_relative).as_posix()}"
        name = readable_name(
            candidate.publish_relative,
            candidate.title,
            candidate.version,
            candidate.source_group,
        )
        api_urls.append({"url": relative_url, "name": name, "group": api_group_key(candidate.publish_relative)})
        copied_files.append((candidate.publish_relative, candidate.source, candidate.source_group))

    apis_json_path = config_dir / "apis.json"
    apis_json_path.write_text(
        json.dumps({"urls": api_urls}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    nojekyll_path = docs_dir / ".nojekyll"
    if not nojekyll_path.exists():
        nojekyll_path.write_text("", encoding="utf-8")

    return api_urls, copied_files


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    swagger_root = repo_root / "documentation" / "source" / "files" / "swagger"
    current_root = swagger_root / "current"

    if not swagger_root.exists():
        raise SystemExit(f"Swagger directory not found: {swagger_root}")

    if not current_root.exists():
        print(f"Warning: current directory not found: {current_root}")

    candidates, invalid = collect_candidates(swagger_root)
    selected = resolve_unique_publish_paths(candidates)
    api_urls, copied_files = write_output(repo_root, selected)

    print("Swagger pages build complete")
    print(f"- Valid specs found: {len(candidates)}")
    print(f"- Invalid/ignored files: {len(invalid)}")
    print(f"- Published specs: {len(copied_files)}")
    print("- Source separation: enabled (base and current are both published)")

    if invalid:
        print("- Ignored files that are not valid OpenAPI/Swagger:")
        for path in invalid:
            print(f"  * {path.relative_to(repo_root).as_posix()}")

    print("- Generated config entries:")
    for entry in api_urls:
        print(f"  * {entry['name']} -> {entry['url']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
