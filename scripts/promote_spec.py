#!/usr/bin/env python3
"""Atualiza o estágio de uma versão de API no stage_overrides.json e sincroniza o apis.json.

Nenhum arquivo de spec é movido. A URL física permanece a mesma; apenas o campo
`stage` no apis.json (e no select do Swagger UI) é atualizado.

Specs organizadas em docs/specs/fase-1, fase-2, fase-3/<group>/<filename>-v<version>.yaml.
Nenhum estágio é atribuído automaticamente: toda versão nova entra sem estágio
("" / indefinido) até ser definida manualmente com este script.

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

  # Remover um override pendente (ainda não aplicado por sync_apis_json.py)
  python scripts/promote_spec.py --remove auto-insurance 2.0.0
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
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


def apis_fases_path() -> Path:
    return repo_root() / "docs" / "config" / "apis_fases.json"


def load_persisted_stages() -> dict[str, str]:
    """Retorna {url: stage} conforme persistido em docs/config/apis_fases.json.

    Essa e a fonte da verdade do estagio efetivo de cada versao -- nunca mais
    recomputada a partir da versao mais alta do grupo (isso mudou de dono para
    sync_apis_json.py/build_swagger_pages.py, que preservam o estagio gravado
    aqui entre execucoes).
    """
    path = apis_fases_path()
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("urls", [])
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        e.get("url"): e.get("stage", "")
        for e in entries
        if isinstance(e, dict)
    }


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
    persisted_stages = load_persisted_stages()

    # (fase, group, version, url, stage persistido em apis_fases.json)
    rows: list[tuple[str, str, str, str, str]] = []
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

        url = "./specs/" + "/".join(parts)
        persisted_stage = persisted_stages.get(url, "")
        rows.append((fase, group, version, url, persisted_stage))

    rows.sort(key=lambda r: (FASE_ORDER.get(r[0], 99), r[1], version_sort_key(r[2])))

    if not rows:
        print("Nenhuma spec encontrada em docs/specs/fase-1, fase-2 ou fase-3.")
        return

    print(f"\n{'Fase':<10} {'Grupo':<45} {'Versão':<12} {'Persistido':<15} {'Efetivo'}")
    print("-" * 95)
    has_pending_override = False
    for fase, group, version, _url, persisted_stage in rows:
        override_stage = active_overrides.get(group, {}).get(version)
        effective_stage = override_stage or persisted_stage or "(indefinido)"
        persisted_display = persisted_stage or "(indefinido)"
        marker = ""
        if override_stage and override_stage != persisted_stage:
            marker = " *"
            has_pending_override = True
        print(f"  {fase:<8} {group:<43} {version:<12} {persisted_display:<15} {effective_stage}{marker}")

    if has_pending_override:
        print("\n  * = override pendente em stage_overrides.json (aplica no proximo sync_apis_json.py)")
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
