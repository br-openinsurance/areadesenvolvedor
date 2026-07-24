#!/usr/bin/env python3
"""Sincroniza docs/config/apis_fases.json com os specs em documentation/source/files/swagger.

Fonte da verdade: documentation/source/files/swagger/<fase>/... (nova arquitetura)
Saida:            docs/config/apis_fases.json

Nao mexe em docs/config/apis.json -- esse arquivo e gerado pelo build_swagger_pages.py
a partir da estrutura legada (arquivos soltos e current/) e consumido diretamente pelo
docs/index.html (configUrl do swagger-ui-dist).

As URLs geradas no apis_fases.json continuam apontando para ./specs/ (onde o
build_swagger_pages.py publica os arquivos para o GitHub Pages).

Comportamento:
  - Adiciona endpoints presentes em documentation mas ausentes no apis.json
  - Remove endpoints do apis.json cujos arquivos nao existem mais em documentation
  - O estagio de um endpoint ja existente e sempre preservado entre execucoes
    (inclusive 'current') -- nunca e recomputado automaticamente a partir da versao
  - Uma versao nova de um grupo ja existente entra como 'retired' por padrao; para
    promove-la (a current ou qualquer outro estagio) use stage_overrides.json
  - Excecao: se o grupo inteiro e novo (nenhuma versao dele existia antes), a versao
    mais alta entre as novas entra como 'current' automaticamente (bootstrap)
  - Aplica overrides de stage_overrides.json e limpa o arquivo apos aplicar
  - Bloqueia com erro se estagios unicos se repetirem por (fase, group)
  - Bloqueia com erro se algum endpoint estiver com stage "" (vazio) -- ocorre
    quando build_swagger_pages.py publica uma versao nova ainda nao promovida;
    defina o estagio via promote_spec.py antes de sincronizar

Estagios unicos (max 1 por grupo): certifying, current, developing, deprecated, release-candidate
Estagios repetiveis: retired
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional, Tuple


VALID_EXTENSIONS = {".yaml", ".yml", ".json"}
PHASES = {"fase-1", "fase-2", "fase-3", "monitoring", "pcm"}

OPENAPI_REGEX = re.compile(r"(?m)^\s*(openapi|swagger)\s*:")
TITLE_REGEX = re.compile(r'(?m)^[ \t]{0,12}title\s*:\s*["\']?([^"\n\'#]+)')
VERSION_REGEX = re.compile(r'(?m)^[ \t]{0,12}version\s*:\s*["\']?([^"\n\'#]+)')
VERSION_FROM_STEM_RE = re.compile(r"^.+-v([\d]+(?:\.[\d]+)*)$")

STAGE_ORDER = {
    "current": 0,
    "certifying": 1,
    "release-candidate": 2,
    "developing": 3,
    "deprecated": 4,
    "retired": 5,
}
VALID_STAGES = set(STAGE_ORDER)
UNIQUE_STAGES = VALID_STAGES - {"retired"}

DIRECTORIES_ORDER = {"fase-1": 0, "fase-2": 1, "fase-3": 2, "monitoring": 3, "pcm": 4}


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------

def extract_version_from_stem(stem: str) -> Optional[str]:
    m = VERSION_FROM_STEM_RE.match(stem)
    return m.group(1) if m else None


def read_spec_meta(file_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Retorna (title, version) do arquivo OpenAPI, ou (None, None) se invalido."""
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
        return title, version

    if not OPENAPI_REGEX.search(text):
        return None, None

    title_match = TITLE_REGEX.search(text)
    version_match = VERSION_REGEX.search(text)
    title = title_match.group(1).strip() if title_match else None
    version = version_match.group(1).strip() if version_match else None
    return title or None, version or None


def readable_name(title: Optional[str], api_key: str, version: Optional[str], stage: str) -> str:
    base = title if title else re.sub(r"[-_]+", " ", api_key).strip().title()
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


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def collect_from_documentation(swagger_root: Path) -> Tuple[list[dict], list[str]]:
    """Varre documentation/source/files/swagger e retorna (entries, skipped).

    A URL de cada entry aponta para ./specs/... (nao para o caminho do source),
    pois e esse o caminho servido pelo GitHub Pages apos o build_swagger_pages.py.
    """
    entries: list[dict] = []
    skipped: list[str] = []

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

        title, version_from_spec = read_spec_meta(file_path)
        if title is None and version_from_spec is None:
            skipped.append("/".join(parts))
            continue

        version_from_name = extract_version_from_stem(Path(filename).stem)
        version = version_from_spec or version_from_name

        # URL aponta para ./specs/ — caminho final apos build_swagger_pages
        entries.append({
            "url": "./specs/" + "/".join(parts),
            "title": title,
            "group": group,
            "fase": fase,
            "version": version,
        })

    return entries, skipped


def load_apis_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("urls", [])
    except (OSError, json.JSONDecodeError):
        return []


def load_stage_overrides(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Warning: nao foi possivel ler {path.name}: {exc}")
        return {}

    raw = payload.get("overrides", {})
    if not isinstance(raw, dict):
        return {}

    result: dict[str, dict[str, str]] = {}
    for group, versions in raw.items():
        if not isinstance(versions, dict):
            continue
        cleaned: dict[str, str] = {}
        for ver, stage in versions.items():
            if stage not in VALID_STAGES:
                print(f"  Warning: estagio invalido '{stage}' para {group}/{ver} -- ignorado.")
                continue
            cleaned[str(ver)] = stage
        if cleaned:
            result[str(group)] = cleaned
    return result


def clear_stage_overrides(path: Path) -> None:
    """Zera apenas o corpo de 'overrides', preservando outras chaves (ex.: '_comment')."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    payload["overrides"] = {}
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Validacao
# ---------------------------------------------------------------------------

def validate_unique_stages(entries: list[dict]) -> list[str]:
    """Retorna lista de erros para estagios unicos duplicados por (fase, group)."""
    stage_map: dict = defaultdict(lambda: defaultdict(list))

    for e in entries:
        if e["stage"] in UNIQUE_STAGES:
            stage_map[(e["fase"], e["group"])][e["stage"]].append(e.get("version") or "?")

    errors = []
    for (fase, group), stages in sorted(stage_map.items()):
        for stage, versions in sorted(stages.items()):
            if len(versions) > 1:
                errors.append(
                    f"  {fase}/{group}: estagio '{stage}' aparece {len(versions)}x "
                    f"(versoes: {', '.join(versions)})"
                )
    return errors


def validate_missing_stages(entries: list[dict]) -> list[str]:
    """Retorna erros para entries com stage vazio/indefinido ('')."""
    return [
        f"  {e['fase']}/{e['group']} v{e.get('version') or '?'}: sem estagio definido"
        for e in entries
        if not e.get("stage")
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    swagger_root = repo_root / "documentation" / "source" / "files" / "swagger"
    apis_json_path = repo_root / "docs" / "config" / "apis_fases.json"
    overrides_path = repo_root / "docs" / "config" / "stage_overrides.json"

    if not swagger_root.exists():
        raise SystemExit(f"Diretorio nao encontrado: {swagger_root}")

    # 1. Fonte da verdade: documentation
    doc_entries, skipped = collect_from_documentation(swagger_root)
    doc_by_url = {e["url"]: e for e in doc_entries}
    doc_url_set = set(doc_by_url)

    # 2. Estado atual do apis.json
    current_entries = load_apis_json(apis_json_path)
    api_by_url = {e["url"]: e for e in current_entries}
    api_url_set = set(api_by_url)

    # 3. Overrides
    overrides = load_stage_overrides(overrides_path)
    overrides_applied: list[str] = []

    # 4. Diff
    added_urls = doc_url_set - api_url_set
    removed_urls = api_url_set - doc_url_set

    # 5. Bootstrap: versao mais alta apenas entre grupos totalmente novos (sem
    #    nenhuma entrada previa no apis.json). Grupos ja existentes nunca tem
    #    seu estagio recomputado por aqui -- so via override.
    existing_groups = {(e["fase"], e["group"]) for e in current_entries}
    bootstrap_highest: dict[tuple[str, str], tuple] = {}
    for e in doc_entries:
        k = (e["fase"], e["group"])
        if k in existing_groups:
            continue
        sk = version_sort_key(e["version"])
        if k not in bootstrap_highest or sk > bootstrap_highest[k]:
            bootstrap_highest[k] = sk

    # 6. Constroi lista final
    final_entries: list[dict] = []

    for e in doc_entries:
        url = e["url"]
        group = e["group"]
        fase = e["fase"]
        version = e["version"]
        k = (fase, group)

        override_stage = overrides.get(group, {}).get(version)
        existing = api_by_url.get(url)

        if override_stage:
            stage = override_stage
            prev = existing["stage"] if existing else "novo"
            overrides_applied.append(f"    {group}/{version}: {prev} -> {override_stage}")
        elif existing:
            stage = existing["stage"]
        elif k in bootstrap_highest and version_sort_key(version) == bootstrap_highest[k]:
            stage = "current"
        else:
            stage = "retired"

        final_entries.append({
            "url": url,
            "name": readable_name(e["title"], group, version, stage),
            "group": group,
            "fase": fase,
            "stage": stage,
            "version": version,
        })

    # 7. Validacao de estagios unicos
    errors = validate_unique_stages(final_entries)
    if errors:
        print("ERRO: estagios unicos duplicados detectados:")
        for err in errors:
            print(err)
        print(
            "\nUse stage_overrides.json para corrigir antes de sincronizar.\n"
            "Estagios unicos (max 1 por grupo): " + ", ".join(sorted(UNIQUE_STAGES))
        )
        return 1

    # 7b. Validacao de estagios ausentes (build_swagger_pages.py publica specs novas
    #     com stage "" ate serem promovidas manualmente -- nunca sincroniza sem definir)
    missing_errors = validate_missing_stages(final_entries)
    if missing_errors:
        print("ERRO: specs sem estagio definido:")
        for err in missing_errors:
            print(err)
        print(
            "\nDefina o estagio antes de sincronizar:\n"
            "  python scripts/promote_spec.py <group> <version> <stage>\n"
            "(grava em stage_overrides.json; este script aplica o override e limpa o arquivo)."
        )
        return 1

    # 8. Ordena e escreve apis.json
    final_entries.sort(
        key=lambda e: (
            DIRECTORIES_ORDER.get(e["fase"], 99),
            e["group"],
            STAGE_ORDER.get(e["stage"], 99),
            version_sort_key(e.get("version")),
            e["url"],
        )
    )

    apis_json_path.parent.mkdir(parents=True, exist_ok=True)
    apis_json_path.write_text(
        json.dumps({"urls": final_entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 9. Limpa stage_overrides.json se havia overrides ativos
    if overrides:
        clear_stage_overrides(overrides_path)

    # 10. Relatorio
    print("sync_apis_json complete")
    print(f"  Total entries written  : {len(final_entries)}")
    if added_urls:
        print(f"  Endpoints adicionados  : {len(added_urls)}")
        for url in sorted(added_urls):
            print(f"    + {url}")
    if removed_urls:
        print(f"  Endpoints removidos    : {len(removed_urls)}")
        for url in sorted(removed_urls):
            print(f"    - {url}")
    if overrides_applied:
        print(f"  Overrides aplicados    : {len(overrides_applied)}")
        for line in overrides_applied:
            print(line)
        print("  stage_overrides.json   : limpo")
    if skipped:
        print(f"  Ignorados (nao OpenAPI): {len(skipped)}")
        for s in skipped:
            print(f"    ~ {s}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
