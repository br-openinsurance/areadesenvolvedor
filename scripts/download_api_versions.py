from pathlib import Path
from urllib.parse import urljoin, urlparse
import re
import time
import csv

import requests
from bs4 import BeautifulSoup
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


PHASE_3_URL = (
    "https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/4391146/"
    "Fase%2B3%2B-%2BServi%2Bos%2Bde%2BInicia%2Bo%2Bde%2BMovimenta%2Bo"
)

OUTPUT_ROOT = Path("open-insurance-apis")
PHASE_SLUG = "fase-3"

VERIFY_SSL = False
TIMEOUT = 60
SLEEP_BETWEEN_REQUESTS = 0.25

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


PHASE_3_API_TITLES = [
    "API - Claim Notification",
    "API - Dynamic Fields",
    "API - Endorsement",
    "API - Notifications",
    "API - Quote Patrimonial",
    "API - Quote Acceptance And Branches Abroad",
    "API - Quote Auto",
    "API - Quote Financial Risk",
    "API - Quote Housing",
    "API - Quote Responsibility",
    "API - Quote Rural",
    "API - Quote Transport",
    "API - Webhook",
    "API de Previdência - Contratação e Portabilidade",
    "API Resgate – Previdência e Capitalização",
    "API de Pessoas - Contratação",
    "API de Capitalização - Pagamento de Sorteio e Contratação",
]


def slugify(text: str) -> str:
    text = text.strip()

    text = re.sub(r"^API\s*-\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^API\s+de\s+", "", text, flags=re.IGNORECASE)

    text = text.lower()

    replacements = {
        "ç": "c",
        "ã": "a",
        "á": "a",
        "à": "a",
        "â": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
        "–": "-",
        "—": "-",
        "&": "and",
    }

    for source, target in replacements.items():
        text = text.replace(source, target)

    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)

    return text.strip("-")


def request_get(session: requests.Session, url: str) -> requests.Response:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    time.sleep(SLEEP_BETWEEN_REQUESTS)
    return response


def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    response = request_get(session, url)
    return BeautifulSoup(response.text, "html.parser")


def github_blob_to_raw(url: str) -> str:
    parsed = urlparse(url)

    if parsed.netloc != "github.com":
        return url

    parts = parsed.path.strip("/").split("/")

    # https://github.com/org/repo/blob/branch/path/file.yaml
    if len(parts) >= 5 and parts[2] == "blob":
        org = parts[0]
        repo = parts[1]
        branch = parts[3]
        file_path = "/".join(parts[4:])
        return f"https://raw.githubusercontent.com/{org}/{repo}/{branch}/{file_path}"

    return url


def find_raw_link_on_github_page(session: requests.Session, page_url: str) -> str:
    soup = get_soup(session, page_url)

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = a["href"]

        if text == "raw" or "raw.githubusercontent.com" in href:
            return urljoin(page_url, href)

    raise RuntimeError(f"Não encontrei link Raw no GitHub: {page_url}")


def resolve_download_url(session: requests.Session, version_url: str) -> str:
    parsed = urlparse(version_url)

    if parsed.netloc == "raw.githubusercontent.com":
        return version_url

    if parsed.netloc == "github.com":
        raw_url = github_blob_to_raw(version_url)

        if raw_url != version_url:
            return raw_url

        return find_raw_link_on_github_page(session, version_url)

    return version_url


def infer_filename_from_url(url: str, api_slug: str) -> str:
    filename = Path(urlparse(url).path).name

    if filename.endswith((".yaml", ".yml", ".json")):
        return filename

    return f"{api_slug}.yaml"


def looks_like_openapi(content: bytes) -> bool:
    head = content[:4000].decode("utf-8", errors="ignore").lower()

    markers = [
        "openapi:",
        '"openapi"',
        "swagger:",
        '"swagger"',
        "paths:",
        '"paths"',
    ]

    return any(marker in head for marker in markers)


def download_file(session: requests.Session, download_url: str, destination: Path) -> None:
    response = request_get(session, download_url)
    content = response.content

    content_type = response.headers.get("content-type", "").lower()

    if "text/html" in content_type and not looks_like_openapi(content):
        raise RuntimeError(f"A URL retornou HTML em vez de YAML/Swagger: {download_url}")

    if not looks_like_openapi(content):
        print(f"[AVISO] Conteúdo não parece OpenAPI/Swagger: {download_url}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def extract_phase3_api_pages(session: requests.Session) -> dict[str, str]:
    """
    Extrai as páginas das APIs da Fase 3.
    Ignora diagramas e documentos complementares.
    """
    soup = get_soup(session, PHASE_3_URL)

    api_pages = {}
    expected = set(PHASE_3_API_TITLES)

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)

        if text in expected:
            api_slug = slugify(text)
            api_url = urljoin(PHASE_3_URL, a["href"])
            api_pages[api_slug] = api_url

    return api_pages


def extract_history_url_from_api_page(
    session: requests.Session,
    api_slug: str,
    api_url: str,
) -> str:
    """
    Entra na página da API e procura:
    Histórico de Versões - API ...
    """
    soup = get_soup(session, api_url)

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()

        if "histórico de versões" in text or "historico de versoes" in text:
            if "api" in text:
                return urljoin(api_url, a["href"])

    raise RuntimeError(f"Não encontrei Histórico de Versões para {api_slug}: {api_url}")


def extract_all_versions_from_history(
    session: requests.Session,
    history_url: str,
) -> dict[str, str]:
    """
    Entra no Histórico de Versões e captura TODAS as versões linkadas.
    """
    soup = get_soup(session, history_url)

    versions = {}

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)

        if VERSION_RE.match(text):
            version = text
            version_url = urljoin(history_url, a["href"])
            versions.setdefault(version, version_url)

    return versions


def save_api_versions(
    session: requests.Session,
    api_slug: str,
    versions: dict[str, str],
) -> list[dict]:
    logs = []

    for version, source_url in sorted(versions.items(), reverse=True):
        try:
            download_url = resolve_download_url(session, source_url)
            filename = infer_filename_from_url(download_url, api_slug)

            destination = OUTPUT_ROOT / PHASE_SLUG / api_slug / version / filename

            print(f"\n[{PHASE_SLUG}] {api_slug} {version}")
            print(f"  origem:   {source_url}")
            print(f"  download: {download_url}")
            print(f"  destino:  {destination}")

            download_file(session, download_url, destination)

            logs.append(
                {
                    "phase": PHASE_SLUG,
                    "api": api_slug,
                    "version": version,
                    "status": "ok",
                    "source_url": source_url,
                    "download_url": download_url,
                    "destination": str(destination),
                    "error": "",
                }
            )

        except Exception as exc:
            print(f"[ERRO] {PHASE_SLUG}/{api_slug}/{version}: {exc}")

            logs.append(
                {
                    "phase": PHASE_SLUG,
                    "api": api_slug,
                    "version": version,
                    "status": "error",
                    "source_url": source_url,
                    "download_url": "",
                    "destination": "",
                    "error": str(exc),
                }
            )

    return logs


def write_report(logs: list[dict]) -> None:
    report_path = OUTPUT_ROOT / PHASE_SLUG / "download-report-fase-3.csv"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "phase",
        "api",
        "version",
        "status",
        "source_url",
        "download_url",
        "destination",
        "error",
    ]

    with report_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(logs)

    print(f"\nRelatório salvo em: {report_path}")


def main() -> int:
    session = requests.Session()
    session.verify = VERIFY_SSL
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; open-insurance-fase3-all-versions-downloader/1.0)"
            )
        }
    )

    print(f"Lendo página da Fase 3: {PHASE_3_URL}")

    api_pages = extract_phase3_api_pages(session)

    print("\nAPIs encontradas na Fase 3:")
    for api_slug, api_url in api_pages.items():
        print(f"  - {api_slug}: {api_url}")

    missing = [
        slugify(title)
        for title in PHASE_3_API_TITLES
        if slugify(title) not in api_pages
    ]

    if missing:
        print("\n[AVISO] Algumas APIs esperadas não foram encontradas:")
        for api_slug in missing:
            print(f"  - {api_slug}")

    logs = []

    for api_slug, api_url in sorted(api_pages.items()):
        print(f"\n=== API: {api_slug} ===")
        print(f"Página da API: {api_url}")

        try:
            history_url = extract_history_url_from_api_page(session, api_slug, api_url)
            print(f"Histórico: {history_url}")

            versions = extract_all_versions_from_history(session, history_url)

            if not versions:
                print(f"[AVISO] Nenhuma versão encontrada no histórico de {api_slug}")
                continue

            print("Versões encontradas:")
            for version in sorted(versions.keys(), reverse=True):
                print(f"  - {version}")

            logs.extend(save_api_versions(session, api_slug, versions))

        except Exception as exc:
            print(f"[ERRO] Falha geral na API {api_slug}: {exc}")

            logs.append(
                {
                    "phase": PHASE_SLUG,
                    "api": api_slug,
                    "version": "",
                    "status": "error",
                    "source_url": api_url,
                    "download_url": "",
                    "destination": "",
                    "error": str(exc),
                }
            )

    write_report(logs)

    errors = [item for item in logs if item["status"] == "error"]

    print("\nResumo:")
    print(f"  Total de downloads/tentativas: {len(logs)}")
    print(f"  OK: {len(logs) - len(errors)}")
    print(f"  Erros: {len(errors)}")

    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())