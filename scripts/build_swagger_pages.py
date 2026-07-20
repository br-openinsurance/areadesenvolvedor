#!/usr/bin/env python3
"""Build static Swagger UI pages for GitHub Pages.

Duas estruturas de origem sao publicadas em paralelo, de forma independente,
enquanto a arquitetura antiga nao e removida:

1) Nova arquitetura (por fase):
   documentation/source/files/swagger/<fase>/<group>/<filename>-v<version>.<ext>
   documentation/source/files/swagger/<fase>/<category>/<group>/<filename>-v<version>.<ext>
     (e.g. fase-1/monitoring/admin_metrics/admin_metrics-v1.3.0.yaml)
   -> docs/specs/<fase>/<group>/<filename>-v<version>.<ext>  (mirrors source structure)
   -> docs/config/apis_fases.json  (formato rico: url, name, group, fase, stage, version)

2) Estrutura legada (arquivos soltos, pre-fase):
   documentation/source/files/swagger/<filename>.<ext>          (stage "base")
   documentation/source/files/swagger/current/<filename>.<ext>  (stage "current")
   -> docs/specs/<filename>.<ext>
   -> docs/specs/current/<filename>.<ext>
   -> docs/config/apis.json  (formato simples: url, name -- consumido diretamente
      pelo configUrl do swagger-ui-dist em docs/index.html)

docs/.nojekyll tambem e criado se ainda nao existir.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


VALID_EXTENSIONS = {".yaml", ".yml", ".json"}
PHASES = {"fase-1", "fase-2", "fase-3", "monitoring", "pcm"}
FASE_ORDER = {"fase-1": 0, "fase-2": 1, "fase-3": 2, "monitoring": 3, "pcm": 4}

OPENAPI_REGEX = re.compile(r"(?m)^\s*(openapi|swagger)\s*:")
TITLE_REGEX = re.compile(r'(?m)^[ \t]{0,12}title\s*:\s*["\']?([^"\n\'#]+)')
VERSION_REGEX = re.compile(r'(?m)^[ \t]{0,12}version\s*:\s*["\']?([^"\n\'#]+)')
VERSION_FROM_STEM_RE = re.compile(r"^.+-v([\d]+(?:\.[\d]+)*)$")

STAGE_ORDER = {"current": 0,
    "certifying": 1,
    "release-candidate": 2,
    "developing": 3,
    "deprecated": 4,
    "retired": 5,}


@dataclass
class CandidateSpec:
    source: Path
    publish_relative: str   # fase/group/<filename>-v<version>.<ext>  (mirrors source)
    fase: str
    group: str
    title: Optional[str]
    version: Optional[str]  # from spec content or filename


@dataclass
class LegacyCandidateSpec:
    source: Path
    publish_relative: str   # <filename>.<ext>  ou  current/<filename>.<ext>
    stage: str               # "base" | "current"
    title: Optional[str]
    version: Optional[str]


def clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().strip('"').strip("'").strip()
    return normalized or None


def extract_version_from_stem(stem: str) -> Optional[str]:
    """Extrai a versão do stem de um filename como 'api-name-v1.2.3'."""
    m = VERSION_FROM_STEM_RE.match(stem)
    return m.group(1) if m else None


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


def readable_name(
    group: str,
    title: Optional[str],
    version: Optional[str],
    stage: str,
) -> str:
    if title:
        base_name = title
    else:
        base_name = re.sub(r"[-_]+", " ", group).strip().title()

    if version and version.lower() not in base_name.lower():
        base_name = f"{base_name} - v{version}"

    return f"{base_name} ({stage})"


def version_sort_key(version: Optional[str]) -> Tuple:
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


def collect_candidates(swagger_root: Path) -> Tuple[List[CandidateSpec], List[Path]]:
    """Scan swagger_root for spec files using the flat fase-based structure.

    Expected paths (relative to swagger_root):
      <fase>/<group>/<filename>-v<version>.<ext>            (3 parts)
      <fase>/<category>/<group>/<filename>-v<version>.<ext>  (4 parts)
    """
    candidates: List[CandidateSpec] = []
    invalid: List[Path] = []

    for file_path in sorted(swagger_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in VALID_EXTENSIONS:
            continue

        rel = file_path.relative_to(swagger_root)
        parts = rel.parts

        if len(parts) == 3:
            fase, group, filename = parts
        elif len(parts) == 4:
            fase, _, group, filename = parts
        else:
            continue

        if fase not in PHASES:
            continue

        valid, title, version_from_spec = is_openapi_spec(file_path)
        if not valid:
            invalid.append(file_path)
            continue

        version_from_name = extract_version_from_stem(Path(filename).stem)
        version = version_from_spec or version_from_name

        candidates.append(
            CandidateSpec(
                source=file_path,
                publish_relative="/".join(parts),
                fase=fase,
                group=group,
                title=title,
                version=version,
            )
        )

    return candidates, invalid


def collect_legacy_candidates(swagger_root: Path) -> Tuple[List[LegacyCandidateSpec], List[Path]]:
    """Scan a estrutura legada (arquivos soltos, pre-fase):

      <filename>.<ext>          -> stage "base"
      current/<filename>.<ext>  -> stage "current"
    """
    candidates: List[LegacyCandidateSpec] = []
    invalid: List[Path] = []

    def scan(directory: Path, stage: str, publish_prefix: str) -> None:
        if not directory.exists():
            return

        for file_path in sorted(directory.glob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in VALID_EXTENSIONS:
                continue

            valid, title, version = is_openapi_spec(file_path)
            if not valid:
                invalid.append(file_path)
                continue

            candidates.append(
                LegacyCandidateSpec(
                    source=file_path,
                    publish_relative=f"{publish_prefix}{file_path.name}",
                    stage=stage,
                    title=title,
                    version=version,
                )
            )

    scan(swagger_root, "base", "")
    scan(swagger_root / "current", "current", "current/")

    return candidates, invalid


def assign_stages(candidates: List[CandidateSpec]) -> Dict[str, str]:
    """Return {publish_relative: stage} — latest version per (fase, group) = current."""
    latest_key: Dict[Tuple[str, str], Tuple] = {}

    for c in candidates:
        k = (c.fase, c.group)
        sk = version_sort_key(c.version)
        if k not in latest_key or sk > latest_key[k]:
            latest_key[k] = sk

    return {
        c.publish_relative: (
            "current"
            if version_sort_key(c.version) == latest_key[(c.fase, c.group)]
            else "retired"
        )
        for c in candidates
    }


def resolve_unique_publish_paths(candidates: List[CandidateSpec]) -> Dict[str, CandidateSpec]:
    selected: Dict[str, CandidateSpec] = {}

    for candidate in candidates:
        key = candidate.publish_relative.lower()

        if key in selected:
            print(
                "Warning: duplicated publish path, replacing previous file: "
                f"{candidate.publish_relative}"
            )

        selected[key] = candidate

    return selected


def write_output(
    root: Path,
    selected: Dict[str, CandidateSpec],
    stages: Dict[str, str],
) -> Tuple[List[dict], List[Tuple[str, Path, str]]]:
    docs_dir = root / "docs"
    specs_dir = docs_dir / "specs"
    config_dir = docs_dir / "config"

    specs_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    copied_files: List[Tuple[str, Path, str]] = []
    api_urls: List[dict] = []

    ordered_candidates = sorted(
        selected.values(),
        key=lambda item: (
            FASE_ORDER.get(item.fase, 99),
            item.group,
            STAGE_ORDER.get(stages.get(item.publish_relative, "retired"), 99),
            version_sort_key(item.version),
            item.publish_relative.lower(),
        ),
    )

    for candidate in ordered_candidates:
        destination = specs_dir / Path(candidate.publish_relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate.source, destination)

        stage = stages.get(candidate.publish_relative, "retired")
        relative_url = f"./specs/{candidate.publish_relative}"
        name = readable_name(candidate.group, candidate.title, candidate.version, stage)

        api_urls.append(
            {
                "url": relative_url,
                "name": name,
                "group": candidate.group,
                "fase": candidate.fase,
                "stage": stage,
                "version": candidate.version,
            }
        )

        copied_files.append(
            (
                candidate.publish_relative,
                candidate.source,
                stage,
            )
        )

    apis_json_path = config_dir / "apis_fases.json"
    apis_json_path.write_text(
        json.dumps({"urls": api_urls}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    nojekyll_path = docs_dir / ".nojekyll"
    if not nojekyll_path.exists():
        nojekyll_path.write_text("", encoding="utf-8")

    return api_urls, copied_files


def write_legacy_output(
    root: Path,
    candidates: List[LegacyCandidateSpec],
) -> Tuple[List[dict], List[Path]]:
    """Publica a estrutura legada, preservando o formato original do apis.json
    ({"url", "name"}), consumido diretamente pelo configUrl do swagger-ui-dist.
    """
    docs_dir = root / "docs"
    specs_dir = docs_dir / "specs"
    config_dir = docs_dir / "config"

    specs_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    copied_files: List[Path] = []
    api_urls: List[dict] = []

    for candidate in candidates:
        destination = specs_dir / Path(candidate.publish_relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate.source, destination)
        copied_files.append(candidate.source)

        stem = Path(candidate.publish_relative).stem
        name = readable_name(stem, candidate.title, candidate.version, candidate.stage)

        api_urls.append({"url": f"./specs/{candidate.publish_relative}", "name": name})

    api_urls.sort(key=lambda entry: entry["url"])

    apis_json_path = config_dir / "apis.json"
    apis_json_path.write_text(
        json.dumps({"urls": api_urls}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return api_urls, copied_files


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    swagger_root = repo_root / "documentation" / "source" / "files" / "swagger"

    if not swagger_root.exists():
        raise SystemExit(f"Swagger directory not found: {swagger_root}")

    candidates, invalid = collect_candidates(swagger_root)
    stages = assign_stages(candidates)
    selected = resolve_unique_publish_paths(candidates)
    api_urls, copied_files = write_output(repo_root, selected, stages)

    legacy_candidates, legacy_invalid = collect_legacy_candidates(swagger_root)
    legacy_urls, legacy_copied = write_legacy_output(repo_root, legacy_candidates)

    print("Swagger pages build complete")

    print("\n[Nova arquitetura -> docs/config/apis_fases.json]")
    print(f"- Valid specs found   : {len(candidates)}")
    print(f"- Invalid/ignored     : {len(invalid)}")
    print(f"- Published specs     : {len(copied_files)}")
    print(f"- Source separation   : by fase and group")

    if invalid:
        print("- Ignored files (not valid OpenAPI/Swagger):")
        for path in invalid:
            print(f"  * {path.relative_to(repo_root).as_posix()}")

    print("- Generated config entries:")
    for entry in api_urls:
        print(f"  * {entry['name']} -> {entry['url']}")

    print("\n[Estrutura legada -> docs/config/apis.json]")
    print(f"- Valid specs found   : {len(legacy_candidates)}")
    print(f"- Invalid/ignored     : {len(legacy_invalid)}")
    print(f"- Published specs     : {len(legacy_copied)}")

    if legacy_invalid:
        print("- Ignored files (not valid OpenAPI/Swagger):")
        for path in legacy_invalid:
            print(f"  * {path.relative_to(repo_root).as_posix()}")

    print("- Generated config entries:")
    for entry in legacy_urls:
        print(f"  * {entry['name']} -> {entry['url']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
