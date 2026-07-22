#!/usr/bin/env python3
"""Atualiza o estágio de uma versão de API no stage_overrides.json e sincroniza o apis.json.

Nenhum arquivo de spec é movido. A URL física permanece a mesma; apenas o campo
`stage` no apis.json (e no select do Swagger UI) é atualizado.

Specs organizadas em docs/specs/fase-1, fase-2, fase-3/<group>/<filename>-v<version>.yaml.
Por padrão a versão mais alta de cada grupo recebe estágio "current"; as demais "retired".
Use os overrides abaixo para atribuir estágios diferentes.

Estágios válidos: certifying, current, release-candidate, developing, deprecated, retired

Uso:
  # Promover uma versão específica
  python scripts/promote_spec.py auto-insurance 2.0.0 certifying

  # Promover várias versões de uma vez
  python scripts/promote_spec.py auto-insurance 1.4.0 retired auto-insurance 2.0.0 certifying

  # Listar overrides ativos
  python scripts/promote_spec.py --list

  # Ver todos os grupos/versões disponíveis nas specs (fase-1/2/3)
  python scripts/promote_spec.py --list-specs

  # Remover um override (volta ao estágio auto-detectado)
  python scripts/promote_spec.py --remove auto-insurance 2.0.0
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

VALID_STAGES = ("certifying", "current", "release-candidate", "developing", "deprecated", "retired")
STAGE_ORDER = {s: i for i, s in enumerate(VALID_STAGES)}
PHASES = {"fase-1", "fase-2", "fase-3", "monitoring", "pcm"}
FASE_ORDER = {"fase-1": 0, "fase-2": 1, "fase-3": 2, "monitoring": 3, "pcm": 4}
VALID_EXTENSIONS = {".yaml", ".yml", ".json"}

VERSION_FROM_STEM_RE = re.compile(r"^.+-v([\d]+(?:\.[\d]+)*)$")


def version_sort_key(version: str) -> tuple:
    parts = re.split(r"[.-]", version)
    normalized = []
    for part in parts:
        if part.isdigit():
            normalized.append(int(part))
        else:
            normalized.append(part.lower())
    return tuple(normalized)


def extract_version_from_stem(stem: str) -> str | None:
    """Extrai a versão do stem de um filename como 'api-name-v1.2.3'."""
    m = VERSION_FROM_STEM_RE.match(stem)
    return m.group(1) if m else None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def overrides_path() -> Path:
    return repo_root() / "docs" / "config" / "stage_overrides.json"


def specs_dir() -> Path:
    return repo_root() / "docs" / "specs"


# ---------------------------------------------------------------------------
# Override file I/O
# ---------------------------------------------------------------------------

def load_overrides() -> dict:
    path = overrides_path()
    if not path.exists():
        return {"_comment": "", "overrides": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_overrides(data: dict) -> None:
    overrides_path().write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_set(group: str, version: str, stage: str) -> None:
    if stage not in VALID_STAGES:
        raise SystemExit(f"Estágio inválido: '{stage}'. Válidos: {', '.join(VALID_STAGES)}")

    data = load_overrides()
    overrides: dict = data.setdefault("overrides", {})
    previous = overrides.get(group, {}).get(version)

    overrides.setdefault(group, {})[version] = stage
    save_overrides(data)

    if previous:
        print(f"  Atualizado: {group}/{version}  {previous} → {stage}")
    else:
        print(f"  Adicionado: {group}/{version} = {stage}")


def cmd_remove(group: str, version: str) -> None:
    data = load_overrides()
    overrides: dict = data.get("overrides", {})

    if group not in overrides or version not in overrides[group]:
        print(f"  Nenhum override encontrado para {group}/{version}.")
        return

    removed_stage = overrides[group].pop(version)
    if not overrides[group]:
        del overrides[group]

    save_overrides(data)
    print(f"  Removido: {group}/{version} (era {removed_stage})")


def cmd_list() -> None:
    data = load_overrides()
    overrides = data.get("overrides", {})
    if not overrides:
        print("Nenhum override ativo. stage_overrides.json está vazio.")
        return

    print(f"\n{'Grupo':<45} {'Versão':<12} {'Estágio Override'}")
    print("-" * 75)
    for group in sorted(overrides):
        for version in sorted(overrides[group]):
            stage = overrides[group][version]
            print(f"  {group:<43} {version:<12} {stage}")
    print()


def cmd_list_specs() -> None:
    root = specs_dir()
    if not root.exists():
        raise SystemExit(f"Diretório não encontrado: {root}")

    data = load_overrides()
    active_overrides = data.get("overrides", {})

    # Coleta todas as versões por (fase, group) para detectar auto-stage.
    group_versions: dict[tuple[str, str], list[str]] = defaultdict(list)
    file_entries: list[tuple[str, str, str]] = []  # (fase, group, version)
    seen: set[tuple[str, str, str]] = set()

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in VALID_EXTENSIONS:
            continue

        rel = file_path.relative_to(root)
        parts = rel.parts

        if len(parts) == 3:
            fase, group, filename = parts
        elif len(parts) == 4:
            fase, _, group, filename = parts
        else:
            continue

        if fase not in PHASES:
            continue

        version = extract_version_from_stem(Path(filename).stem)
        if not version:
            continue

        key = (fase, group, version)
        if key in seen:
            continue
        seen.add(key)

        group_versions[(fase, group)].append(version)
        file_entries.append(key)

    latest: dict[tuple[str, str], str] = {
        k: max(versions, key=version_sort_key)
        for k, versions in group_versions.items()
    }

    rows: list[tuple[str, str, str, str, str]] = []
    for fase, group, version in file_entries:
        k = (fase, group)
        auto_stage = "current" if version == latest.get(k) else "retired"
        effective = active_overrides.get(group, {}).get(version, auto_stage)
        rows.append((fase, group, version, auto_stage, effective))

    rows.sort(key=lambda r: (FASE_ORDER.get(r[0], 99), r[1], version_sort_key(r[2])))

    if not rows:
        print("Nenhuma spec encontrada em docs/specs/fase-1, fase-2 ou fase-3.")
        return

    print(f"\n{'Fase':<10} {'Grupo':<45} {'Versão':<12} {'Auto':<15} {'Efetivo'}")
    print("-" * 95)
    for fase, group, version, auto_stage, effective_stage in rows:
        marker = " *" if effective_stage != auto_stage else ""
        print(f"  {fase:<8} {group:<43} {version:<12} {auto_stage:<15} {effective_stage}{marker}")

    if any(r[3] != r[4] for r in rows):
        print("\n  * = estágio sobrescrito pelo override")
    print()


def run_sync() -> None:
    sync_script = repo_root() / "scripts" / "sync_apis_json.py"
    result = subprocess.run(
        [sys.executable, str(sync_script)],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit("sync_apis_json falhou.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gerencia overrides de estágio e sincroniza apis.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--list", action="store_true", help="Lista os overrides ativos.")
    parser.add_argument("--list-specs", action="store_true", help="Lista todas as specs com estágio efetivo.")
    parser.add_argument("--no-sync", action="store_true", help="Não executa sync_apis_json após salvar.")
    parser.add_argument(
        "--remove",
        nargs=2,
        metavar=("GROUP", "VERSION"),
        help="Remove um override específico.",
    )
    parser.add_argument(
        "args",
        nargs="*",
        metavar="GROUP VERSION STAGE",
        help="Triplas group/version/stage para definir overrides.",
    )

    parsed = parser.parse_args()

    if parsed.list:
        cmd_list()
        return 0

    if parsed.list_specs:
        cmd_list_specs()
        return 0

    if parsed.remove:
        group, version = parsed.remove
        cmd_remove(group, version)
        if not parsed.no_sync:
            run_sync()
        return 0

    if parsed.args:
        if len(parsed.args) % 3 != 0:
            print("Erro: os argumentos devem vir em triplas: GROUP VERSION STAGE")
            print("Exemplo: python promote_spec.py auto-insurance 1.4.0 deprecated auto-insurance 2.0.0 current")
            return 1

        triplets = [
            (parsed.args[i], parsed.args[i + 1], parsed.args[i + 2])
            for i in range(0, len(parsed.args), 3)
        ]

        for group, version, stage in triplets:
            cmd_set(group, version, stage)

        if not parsed.no_sync:
            run_sync()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
