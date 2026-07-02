#!/usr/bin/env python3
"""Sync docs/config/apis.json with spec files found in docs/specs/fase-1, fase-2 and fase-3.

Scans docs/specs/<fase>/<group>/<filename>-v<version>.<ext> and rebuilds apis.json from scratch.
Within each (fase, group) pair the highest semantic version is assigned stage "current";
all older versions are assigned "retired". Entries in stage_overrides.json take
precedence over the auto-detected stage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, Tuple


VALID_EXTENSIONS = {".yaml", ".yml", ".json"}
PHASES = {"fase-1", "fase-2", "fase-3", "monitoring", "pcm"}

OPENAPI_REGEX = re.compile(r"(?m)^\s*(openapi|swagger)\s*:")
TITLE_REGEX = re.compile(r'(?m)^[ \t]{0,12}title\s*:\s*["\']?([^"\n\'#]+)')
VERSION_REGEX = re.compile(r'(?m)^[ \t]{0,12}version\s*:\s*["\']?([^"\n\'#]+)')
VERSION_FROM_STEM_RE = re.compile(r"^.+-v([\d]+(?:\.[\d]+)*)$")


def clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().strip('"').strip("'").strip()
    return normalized or None


def extract_version_from_stem(stem: str) -> Optional[str]:
    """Extrai a versão do stem de um filename como 'api-name-v1.2.3'."""
    m = VERSION_FROM_STEM_RE.match(stem)
    return m.group(1) if m else None


def read_spec_meta(file_path: Path) -> Tuple[Optional[str], Optional[str]]:
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
VALID_STAGES = set(STAGE_ORDER)
FASE_ORDER = {"fase-1": 0, "fase-2": 1, "fase-3": 2, "monitoring": 3, "pcm": 4}


def load_stage_overrides(overrides_path: Path) -> dict[str, dict[str, str]]:
    """Load stage_overrides.json → {group: {version: stage}}.

    Returns an empty dict if the file is absent or malformed.
    """
    if not overrides_path.exists():
        return {}
    try:
        payload = json.loads(overrides_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Warning: could not read {overrides_path.name}: {exc}")
        return {}

    raw = payload.get("overrides", {})
    if not isinstance(raw, dict):
        return {}

    result: dict[str, dict[str, str]] = {}
    for group, versions in raw.items():
        if not isinstance(versions, dict):
            continue
        cleaned: dict[str, str] = {}
        for version, stage in versions.items():
            if stage not in VALID_STAGES:
                print(f"  Warning: estágio inválido '{stage}' para {group}/{version} — ignorado.")
                continue
            cleaned[str(version)] = stage
        if cleaned:
            result[str(group)] = cleaned
    return result


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    specs_dir = repo_root / "docs" / "specs"
    apis_json_path = repo_root / "docs" / "config" / "apis.json"
    overrides_path = repo_root / "docs" / "config" / "stage_overrides.json"

    if not specs_dir.exists():
        raise SystemExit(f"specs directory not found: {specs_dir}")

    overrides = load_stage_overrides(overrides_path)
    overrides_applied: list[str] = []

    # Escaneia fase-1/2/3 na estrutura plana e realiza coleta dos dados.
    # Caminho esperado relativo ao specs_dir:
    #   <fase>/<group>/<filename>-v<version>.<ext>            (3 partes)
    #   <fase>/<category>/<group>/<filename>-v<version>.<ext>  (4 partes)
    raw_entries: list[dict] = []
    invalid: list[str] = []

    for file_path in sorted(specs_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in VALID_EXTENSIONS:
            continue

        rel = file_path.relative_to(specs_dir)
        parts = rel.parts

        if len(parts) == 3:
            fase, group, filename = parts
        elif len(parts) == 4:
            fase, _, group, filename = parts
        else:
            continue

        if fase not in PHASES:
            continue

        relative_url = "./specs/" + "/".join(parts)

        title, version_from_spec = read_spec_meta(file_path)
        if title is None and version_from_spec is None:
            invalid.append(relative_url)
            continue

        version_from_name = extract_version_from_stem(Path(filename).stem)
        version = version_from_spec or version_from_name

        raw_entries.append({
            "url": relative_url,
            "title": title,
            "group": group,
            "fase": fase,
            "version": version,
        })

    # Encontra a versão maior por (fase, group) para atribuir automaticamente "current".
    latest_key: dict[tuple[str, str], tuple] = {}
    for e in raw_entries:
        k = (e["fase"], e["group"])
        sk = version_sort_key(e["version"])
        if k not in latest_key or sk > latest_key[k]:
            latest_key[k] = sk

    # Atribui os stages e build as entradas finais.
    entries: list[dict] = []
    for e in raw_entries:
        group = e["group"]
        fase = e["fase"]
        resolved_version = e["version"]
        k = (fase, group)

        auto_stage = "current" if version_sort_key(resolved_version) == latest_key[k] else "retired"

        effective_stage = auto_stage
        override_stage = overrides.get(group, {}).get(resolved_version)
        if override_stage and override_stage != auto_stage:
            effective_stage = override_stage
            overrides_applied.append(
                f"    {group}/{resolved_version}: {auto_stage} → {override_stage}"
            )

        name = readable_name(e["title"], group, resolved_version, effective_stage)
        entries.append({
            "url": e["url"],
            "name": name,
            "group": group,
            "fase": fase,
            "stage": effective_stage,
            "version": resolved_version,
        })

    entries.sort(
        key=lambda e: (
            FASE_ORDER.get(e["fase"], 99),
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
    if overrides_applied:
        print(f"  Stage overrides applied: {len(overrides_applied)}")
        for line in overrides_applied:
            print(line)
    if invalid:
        print(f"  Skipped (not valid OpenAPI) : {len(invalid)}")
        for url in invalid:
            print(f"    - {url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
