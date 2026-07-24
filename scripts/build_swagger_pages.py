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
from collections import defaultdict
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
VALID_STAGES = set(STAGE_ORDER)
UNIQUE_STAGES = VALID_STAGES - {"retired"}


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

    return f"{base_name} ({stage or 'unknown'})"


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


def load_apis_fases(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("urls", [])
    except (OSError, json.JSONDecodeError):
        return []


def assign_stages(
    candidates: List[CandidateSpec],
    existing_entries: List[dict],
) -> Dict[str, str]:
    """Return {publish_relative: stage}.

    Nenhum estagio e decidido automaticamente por este script. O estagio de uma
    versao ja publicada e sempre preservado entre execucoes (inclusive 'current'),
    lido diretamente do apis_fases.json existente -- quem define o estagio de cada
    versao e o desenvolvedor (editando apis_fases.json ou via promote_spec.py).
    Uma versao nunca antes publicada entra com stage "" (vazio/indefinido) ate
    ser promovida manualmente; nunca vira 'current' ou 'retired' sozinha, mesmo
    sendo a mais nova do grupo. O front-end (docs/index.html) exibe stage vazio
    como "unknown" e o sync_apis_json.py bloqueia a sincronizacao ate ser definido.
    """
    existing_by_url = {e.get("url"): e for e in existing_entries}

    stages: Dict[str, str] = {}
    for c in candidates:
        url = f"./specs/{c.publish_relative}"
        existing = existing_by_url.get(url)
        stages[c.publish_relative] = existing["stage"] if existing else ""

    return stages


def validate_unique_stages(candidates: List[CandidateSpec], stages: Dict[str, str]) -> List[str]:
    """Retorna erros para estagios unicos (todos exceto 'retired') duplicados por (fase, group)."""
    stage_map: Dict[Tuple[str, str], Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))

    for c in candidates:
        stage = stages.get(c.publish_relative, "")
        if stage in UNIQUE_STAGES:
            stage_map[(c.fase, c.group)][stage].append(c.version or "?")

    errors: List[str] = []
    for (fase, group), stage_versions in sorted(stage_map.items()):
        for stage, versions in sorted(stage_versions.items()):
            if len(versions) > 1:
                errors.append(
                    f"  {fase}/{group}: estagio '{stage}' aparece {len(versions)}x "
                    f"(versoes: {', '.join(versions)})"
                )
    return errors


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
    apis_fases_path = repo_root / "docs" / "config" / "apis_fases.json"

    if not swagger_root.exists():
        raise SystemExit(f"Swagger directory not found: {swagger_root}")

    candidates, invalid = collect_candidates(swagger_root)
    selected = resolve_unique_publish_paths(candidates)
    selected_candidates = list(selected.values())

    existing_entries = load_apis_fases(apis_fases_path)
    stages = assign_stages(selected_candidates, existing_entries)

    stage_errors = validate_unique_stages(selected_candidates, stages)
    if stage_errors:
        print("ERRO: estagios unicos duplicados detectados:")
        for err in stage_errors:
            print(err)
        print(
            "\nEdite docs/config/apis_fases.json (ou use promote_spec.py) para corrigir.\n"
            "Estagios unicos (max 1 por grupo): " + ", ".join(sorted(UNIQUE_STAGES))
        )
        return 1

    api_urls, copied_files = write_output(repo_root, selected, stages)

    legacy_candidates, legacy_invalid = collect_legacy_candidates(swagger_root)
    legacy_urls, legacy_copied = write_legacy_output(repo_root, legacy_candidates)

    print("Swagger pages build complete")

    print("\n[Nova arquitetura -> docs/config/apis_fases.json]")
    print(f"- Valid specs found   : {len(candidates)}")
    print(f"- Invalid/ignored     : {len(invalid)}")
    print(f"- Published specs     : {len(copied_files)}")
    print(f"- Source separation   : by fase and group")

    missing_stage = [c for c in selected_candidates if not stages.get(c.publish_relative)]
    if missing_stage:
        print(f"- Sem estagio definido: {len(missing_stage)} (aparecem como stage \"\" / \"unknown\")")
        for c in missing_stage:
            print(f"  * {c.fase}/{c.group} v{c.version or '?'} -> defina via promote_spec.py")

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
