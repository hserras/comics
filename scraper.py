import random
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests

BASE_URL = "https://getcomics.org"
DEFAULT_TAG_PATH = "/tag/marvel-now/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
}

SECTION_HEADERS = {
    "DC COMICS",
    "MARVEL COMICS",
    "IMAGE COMICS",
    "INDIE COMICS",
    "OTHER COMICS",
    "NOTES",
}

DIRECT_HOST_HINTS = (
    "getcomics.org/download",
    "getcomics.org/dlds/",
    "getcomics.org/dls/",
    "getcomics.info/dls/",
    "getcomics.info/dlds/",
)

GETCOMICS_DOMAINS = ("getcomics.org", "getcomics.info")

MIRROR_DOMAINS = {
    "PIXELDRAIN": ("pixeldrain.com",),
    "VIKINGFILE": ("vikingfile.com",),
    "DATANODES": ("datanodes.to",),
    "MEDIAFIRE": ("mediafire.com",),
    "TERABOX": ("terabox.com", "1024terabox.com"),
    "MEGA": ("mega.nz", "mega.io"),
}


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _host_matches(hostname: str, domains: tuple[str, ...]) -> bool:
    return any(hostname == d or hostname.endswith(f".{d}") for d in domains)


def _mirror_from_host(hostname: str) -> str | None:
    for mirror, domains in MIRROR_DOMAINS.items():
        if _host_matches(hostname, domains):
            return mirror
    return None


def _mirror_from_text(text_upper: str) -> str | None:
    for mirror in MIRROR_DOMAINS:
        if re.search(rf"\b{mirror}\b", text_upper):
            return mirror
    return None


def _build_tag_page_url(page: int, tag_path: str = DEFAULT_TAG_PATH) -> str:
    clean_path = tag_path.strip("/")
    base = f"{BASE_URL}/{clean_path}/"
    if page <= 1:
        return base
    return f"{base}page/{page}/"


def _get_with_retry(
    session: requests.Session,
    url: str,
    timeout: int = 20,
    max_retries: int = 4,
    base_delay: float = 2.0,
    allow_404: bool = False,
) -> requests.Response:
    """
    Faz GET com retry automático em HTTP 429 / erros temporários 5xx.
    Com allow_404=True devolve o 404 sem levantar exceção (fim de paginação);
    caso contrário, um 404 levanta HTTPError de imediato, sem retries.
    """
    resp = None
    for attempt in range(max_retries):
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 404:
            if not allow_404:
                resp.raise_for_status()
            return resp

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                sleep_for = float(retry_after) + random.uniform(0.5, 1.5)
            else:
                sleep_for = base_delay * (2 ** attempt) + random.uniform(0.5, 2.0)
            time.sleep(sleep_for)
            continue

        if 500 <= resp.status_code < 600 and attempt < max_retries - 1:
            time.sleep(base_delay * (attempt + 1) + random.uniform(0.5, 1.5))
            continue

        resp.raise_for_status()
        return resp

    assert resp is not None
    resp.raise_for_status()
    return resp


def fetch_weekly_packs(
    page: int = 1,
    tag_path: str = DEFAULT_TAG_PATH,
    only_packs: bool = False,
    timeout: int = 20,
) -> list[dict]:
    """
    Obtém os artigos listados numa página de tag (ex.: /tag/marvel-now/page/50/).
    Se only_packs=True, filtra títulos com 'pack' ou 'week' ou 'now+'.
    Se a página não existir (404), devolve lista vazia [].
    """
    url = _build_tag_page_url(page=page, tag_path=tag_path)
    with requests.Session() as session:
        response = _get_with_retry(session, url, timeout=timeout, allow_404=True)
        if response.status_code == 404:
            return []

    soup = BeautifulSoup(response.text, "html.parser")
    articles = soup.find_all("article")
    packs: list[dict] = []
    seen_urls: set[str] = set()

    for article in articles:
        title_tag = (
            article.find("h1", class_="post-title")
            or article.find("h1")
            or article.find("h2")
        )
        if not title_tag:
            continue

        link_tag = title_tag.find("a", href=True)
        if not link_tag:
            continue

        title = _clean_text(link_tag.get_text())
        href = urljoin(BASE_URL, link_tag["href"].strip())

        if href in seen_urls or not title:
            continue

        if only_packs:
            t_low = title.lower()
            if not any(k in t_low for k in ("pack", "week", "now+")):
                continue

        meta_text = ""
        for p in article.find_all(["p", "div", "span"]):
            txt = _clean_text(p.get_text())
            if "Year :" in txt or "Size :" in txt:
                meta_text = txt
                break

        seen_urls.add(href)
        packs.append({
            "title": title,
            "url": href,
            "meta": meta_text,
        })

    return packs


def extract_marvel_issues(pack_url: str, timeout: int = 20) -> list[dict]:
    """
    Abre um artigo (Weekly Pack / Marvel Week / etc.) e extrai os itens sob MARVEL COMICS.
    Se o artigo não tiver cabeçalho 'MARVEL COMICS' explícito (por ser um artigo de edição única),
    devolve uma entrada única com o próprio título e URL do artigo.
    """
    with requests.Session() as session:
        response = _get_with_retry(session, pack_url, timeout=timeout)

    soup = BeautifulSoup(response.text, "html.parser")
    page_title_el = soup.find("h1", class_="post-title") or soup.find("h1")
    article_title = _clean_text(page_title_el.get_text()) if page_title_el else pack_url

    container = (
        soup.select_one("div.post-contents")
        or soup.select_one("article .entry-content")
        or soup.select_one("article")
        or soup
    )

    in_marvel = False
    issues: list[dict] = []
    seen_urls: set[str] = set()

    for el in container.find_all(["h1", "h2", "h3", "h4", "p", "li", "div"]):
        if el.name == "div" and el.find(["p", "li", "h2", "h3", "h4"]):
            continue

        normalized = _clean_text(el.get_text()).upper().rstrip(":")

        if normalized == "MARVEL COMICS" and len(normalized) <= 30:
            in_marvel = True
            continue

        if in_marvel and normalized in SECTION_HEADERS:
            break

        if not in_marvel:
            continue

        for a_tag in el.find_all("a", href=True):
            label = _clean_text(a_tag.get_text()).lower()
            if label != "download":
                continue

            full_url = urljoin(pack_url, a_tag["href"].strip())
            if full_url in seen_urls:
                continue

            raw_line = _clean_text(el.get_text())
            title = raw_line.split(":", 1)[0].strip()
            title = re.sub(r"\s*[-–—]\s*$", "", title).strip()

            if title:
                seen_urls.add(full_url)
                issues.append({
                    "title": title,
                    "article_url": full_url,
                })

    if not issues:
        return [{"title": article_title, "article_url": pack_url}]

    return issues


def resolve_issue_download_links(
    issue_url: str,
    session: requests.Session | None = None,
    timeout: int = 20,
    delay_range: tuple[float, float] = (1.5, 3.0),
) -> list[dict]:
    """
    Abre a página individual de uma edição e extrai os links de descarga reais.
    """
    if delay_range[1] > 0:
        time.sleep(random.uniform(*delay_range))

    own_session = session is None
    sess = session or requests.Session()
    try:
        response = _get_with_retry(sess, issue_url, timeout=timeout)
    finally:
        if own_session:
            sess.close()

    soup = BeautifulSoup(response.text, "html.parser")
    container = (
        soup.select_one("div.post-contents")
        or soup.select_one("article .entry-content")
        or soup.select_one("article")
        or soup
    )

    candidates: list[dict] = []
    seen: set[str] = set()

    for a_tag in container.find_all("a", href=True):
        href = urljoin(issue_url, a_tag["href"].strip())
        if not href.startswith(("http://", "https://")) or href in seen:
            continue

        text_upper = _clean_text(a_tag.get_text()).upper()
        title_upper = _clean_text(a_tag.get("title", "")).upper()
        combined = f"{text_upper} {title_upper}"
        href_lower = href.lower()
        hostname = (urlparse(href).hostname or "").lower()
        is_internal = _host_matches(hostname, GETCOMICS_DOMAINS)

        if "READ ONLINE" in combined or "DISCORD" in combined:
            continue

        is_direct_hint = any(hint in href_lower for hint in DIRECT_HOST_HINTS) or (
            ("DOWNLOAD NOW" in combined or "MAIN SERVER" in combined) and is_internal
        )

        matched_label = None
        if "DOWNLOAD NOW" in combined:
            matched_label = "DOWNLOAD NOW"
        elif "MAIN SERVER" in combined:
            matched_label = "MAIN SERVER"
        elif is_direct_hint:
            # Os botões dos mirrors passam por redirecionamentos internos (/dls/); o texto indica o mirror
            matched_label = _mirror_from_text(combined) or "DIRECT"
        elif not is_internal:
            matched_label = _mirror_from_host(hostname) or _mirror_from_text(combined)

        if matched_label:
            seen.add(href)
            candidates.append({
                "label": matched_label,
                "url": href,
                "is_direct_hint": is_direct_hint,
            })

    candidates.sort(key=lambda item: (0 if item["is_direct_hint"] else 1))
    return candidates