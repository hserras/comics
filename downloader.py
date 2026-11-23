import random
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

console = Console()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://getcomics.org/",
}

COMIC_EXTENSIONS = {".cbz", ".cbr", ".zip", ".rar", ".pdf", ".7z", ".epub"}


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    return cleaned or "comic_download"


def unique_filepath(target_dir: Path, filename: str) -> Path:
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        new_candidate = target_dir / f"{stem} ({counter}){suffix}"
        if not new_candidate.exists():
            return new_candidate
        counter += 1


def is_aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def _extract_filename_from_response(response: requests.Response, fallback_title: str) -> str:
    content_disp = response.headers.get("content-disposition", "")
    match = re.search(
        r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
        content_disp,
        flags=re.IGNORECASE,
    )
    if match:
        return sanitize_filename(unquote(match.group(1)))

    url_path = Path(unquote(urlparse(response.url).path))
    if url_path.suffix.lower() in COMIC_EXTENSIONS:
        return sanitize_filename(url_path.name)

    return f"{sanitize_filename(fallback_title)}.cbz"


def _looks_like_html(path: Path) -> bool:
    with open(path, "rb") as f:
        head = f.read(256).lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html"))


def _final_name(original_name: str, target_stem: str | None) -> str:
    """Com target_stem, o nome é previsível e só a extensão vem do ficheiro descarregado."""
    if not target_stem:
        return sanitize_filename(original_name)
    suffix = Path(original_name).suffix.lower()
    return f"{target_stem}{suffix if suffix in COMIC_EXTENSIONS else '.cbz'}"


def _pick_destination(target_dir: Path, filename: str, overwrite: bool) -> Path:
    return target_dir / filename if overwrite else unique_filepath(target_dir, filename)


def download_single_issue(
    title: str,
    url: str,
    target_dir: Path,
    session: requests.Session | None = None,
    use_aria2c: bool = True,
    timeout: int = 30,
    max_retries: int = 3,
    target_stem: str | None = None,
    overwrite: bool = False,
) -> tuple[bool, str]:
    """
    Tenta descarregar uma edição a partir de um URL de descarga/redirecionamento.
    Devolve (sucesso: bool, mensagem_ou_url: str).

    target_stem fixa o nome do ficheiro (sem extensão). overwrite substitui um ficheiro com o
    mesmo nome em vez de criar "Nome (1).cbz".
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    if use_aria2c and is_aria2c_available():
        # Pasta temporária própria: o aria2c escolhe o nome do ficheiro, e assim sabemos qual foi criado
        work_dir = Path(tempfile.mkdtemp(dir=target_dir, prefix=".aria2_"))
        try:
            cmd = [
                "aria2c",
                "--console-log-level=warn",
                "--summary-interval=0",
                "--content-disposition-default-utf8=true",
                f"--user-agent={HEADERS['User-Agent']}",
                "--header=Referer: https://getcomics.org/",
                "-d",
                str(work_dir),
                url,
            ]
            subprocess.run(cmd, check=True)

            downloaded = [
                p for p in work_dir.iterdir()
                if p.is_file() and p.suffix.lower() != ".aria2"
            ]
            if not downloaded:
                console.print(f"[yellow]aria2c não gerou ficheiro para '{title}', a tentar via Python...[/yellow]")
            else:
                downloaded_file = max(downloaded, key=lambda p: p.stat().st_size)
                if downloaded_file.stat().st_size == 0 or _looks_like_html(downloaded_file):
                    console.print(f"[yellow]aria2c devolveu uma página HTML para '{title}', a tentar via Python...[/yellow]")
                else:
                    destination = _pick_destination(
                        target_dir, _final_name(downloaded_file.name, target_stem), overwrite
                    )
                    downloaded_file.replace(destination)
                    return True, str(destination)
        except (subprocess.CalledProcessError, OSError):
            console.print(f"[yellow]aria2c falhou para '{title}', a tentar via Python...[/yellow]")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    own_session = session is None
    sess = session or requests.Session()

    try:
        resp = None
        for attempt in range(max_retries):
            resp = sess.get(url, headers=HEADERS, stream=True, allow_redirects=True, timeout=timeout)
            if resp.status_code == 429:
                resp.close()
                if attempt < max_retries - 1:
                    time.sleep(2.5 * (2 ** attempt) + random.uniform(0.5, 1.5))
                    continue
            resp.raise_for_status()
            break

        assert resp is not None
        with resp:
            content_type = resp.headers.get("content-type", "").lower()

            # Evita gravar páginas HTML de hosts externos como se fossem bandas desenhadas
            if "text/html" in content_type:
                return False, resp.url

            filename = _final_name(_extract_filename_from_response(resp, title), target_stem)
            destination = _pick_destination(target_dir, filename, overwrite)
            total_bytes = int(resp.headers.get("content-length", 0))

            # O .part fica na pasta final (na NAS), para o rename no fim ser atómico e sem segunda cópia
            temp_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.part")

            try:
                progress = Progress(
                    BarColumn(bar_width=None),
                    "[progress.percentage]{task.percentage:>3.1f}%",
                    "•",
                    DownloadColumn(binary_units=True),
                    "•",
                    TransferSpeedColumn(),
                    "•",
                    TimeRemainingColumn(compact=True),
                    transient=True,
                )
                written = 0
                with progress, open(temp_path, "wb") as f:
                    task = progress.add_task(description=destination.name, total=total_bytes or None)
                    for chunk in resp.iter_content(chunk_size=1024 * 64):
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)
                            progress.update(task, advance=len(chunk))

                # Com Content-Encoding o content-length refere-se ao tamanho comprimido
                if total_bytes and not resp.headers.get("content-encoding") and written != total_bytes:
                    return False, f"{url} (download incompleto: {written} de {total_bytes} bytes)"

                # Proteção extra: verifica se o ficheiro começa por HTML
                if _looks_like_html(temp_path):
                    return False, resp.url

                if destination.exists() and not overwrite:
                    destination = unique_filepath(target_dir, filename)
                temp_path.replace(destination)
                return True, str(destination)
            finally:
                temp_path.unlink(missing_ok=True)

    except Exception as exc:
        return False, f"{url} ({exc})"
    finally:
        if own_session:
            sess.close()