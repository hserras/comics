"""
Histórico de downloads (SQLite). Guarda o estado de cada edição para saber o que já foi
descarregado, o que falhou e o que exige download manual.

A chave de uma edição é o slug do seu URL, sem domínio nem categoria: a mesma edição aparece
com getcomics.org e getcomics.info, e o site pode mudá-la de categoria. Os caminhos guardam-se
relativos à biblioteca, para o histórico sobreviver a uma mudança de pasta ou de NAS.
"""
import contextlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

STATUS_DOWNLOADED = "downloaded"
STATUS_FAILED = "failed"
STATUS_MANUAL = "manual"
STATUS_MISSING = "missing"  # estava descarregada, mas o ficheiro desapareceu do disco

GETCOMICS_DOMAINS = ("getcomics.org", "getcomics.info")

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    key         TEXT PRIMARY KEY,
    article_url TEXT NOT NULL,
    title       TEXT NOT NULL,
    pack_title  TEXT,
    status      TEXT NOT NULL,
    rel_path    TEXT,
    size        INTEGER,
    note        TEXT,
    updated_at  TEXT NOT NULL
)
"""


def issue_key(article_url: str) -> str:
    parsed = urlparse(article_url)
    host = (parsed.hostname or "").lower()
    segments = [s for s in parsed.path.split("/") if s]

    if segments and any(host == d or host.endswith(f".{d}") for d in GETCOMICS_DOMAINS):
        return segments[-1].lower()

    # Encurtadores como o sh.st têm códigos sensíveis a maiúsculas
    return f"{host}/{'/'.join(segments)}"


class History:
    def __init__(self, db_path: Path, library_dir: Path):
        self.db_path = Path(db_path)
        self.library_dir = Path(library_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(SCHEMA)

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _abs_path(self, rel_path: str | None) -> Path | None:
        return self.library_dir / rel_path if rel_path else None

    def _rel_path(self, abs_path: Path) -> str:
        try:
            return abs_path.resolve().relative_to(self.library_dir.resolve()).as_posix()
        except ValueError:
            return abs_path.resolve().as_posix()

    def _effective_status(self, row: sqlite3.Row) -> str:
        if row["status"] == STATUS_DOWNLOADED:
            path = self._abs_path(row["rel_path"])
            if path is None or not path.is_file():
                return STATUS_MISSING
        return row["status"]

    def status_of(self, article_url: str) -> str | None:
        """Estado atual da edição, ou None se nunca foi tentada."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, rel_path FROM issues WHERE key = ?", (issue_key(article_url),)
            ).fetchone()
        return self._effective_status(row) if row else None

    def statuses(self, article_urls: Iterable[str]) -> dict[str, str]:
        """Estado de várias edições de uma vez. Só inclui as que têm registo."""
        result: dict[str, str] = {}
        with self._connect() as conn:
            for url in article_urls:
                row = conn.execute(
                    "SELECT status, rel_path FROM issues WHERE key = ?", (issue_key(url),)
                ).fetchone()
                if row:
                    result[url] = self._effective_status(row)
        return result

    def record(
        self,
        article_url: str,
        title: str,
        status: str,
        pack_title: str | None = None,
        file_path: Path | None = None,
        note: str | None = None,
    ) -> None:
        key = issue_key(article_url)

        # Uma nova tentativa falhada não apaga o registo de um ficheiro que continua no disco
        if status != STATUS_DOWNLOADED and self.status_of(article_url) == STATUS_DOWNLOADED:
            return

        rel_path = size = None
        if status == STATUS_DOWNLOADED and file_path is not None:
            rel_path = self._rel_path(Path(file_path))
            size = Path(file_path).stat().st_size

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO issues (key, article_url, title, pack_title, status, rel_path, size, note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    article_url = excluded.article_url,
                    title       = excluded.title,
                    pack_title  = excluded.pack_title,
                    status      = excluded.status,
                    rel_path    = excluded.rel_path,
                    size        = excluded.size,
                    note        = excluded.note,
                    updated_at  = excluded.updated_at
                """,
                (
                    key,
                    article_url,
                    title,
                    pack_title,
                    status,
                    rel_path,
                    size,
                    note,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )

    def owner_of(self, file_path: Path) -> str | None:
        """Chave da edição que tem este ficheiro registado, ou None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT key FROM issues WHERE rel_path = ?", (self._rel_path(Path(file_path)),)
            ).fetchone()
        return row["key"] if row else None

    def downloaded_rows(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT key, article_url, title, pack_title, rel_path FROM issues WHERE status = ? AND rel_path IS NOT NULL",
                (STATUS_DOWNLOADED,),
            ).fetchall()

    def update_path(self, key: str, new_file_path: Path) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE issues SET rel_path = ?, updated_at = ? WHERE key = ?",
                (
                    self._rel_path(Path(new_file_path)),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    key,
                ),
            )

    def reconcile(self) -> dict[str, int]:
        """
        Compara o histórico com o disco: ficheiros descarregados que desapareceram passam a
        'missing' (voltam a aparecer como por descarregar). Devolve {'checked': n, 'missing': m}.
        """
        checked = missing = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, rel_path FROM issues WHERE status = ?", (STATUS_DOWNLOADED,)
            ).fetchall()
            for row in rows:
                checked += 1
                path = self._abs_path(row["rel_path"])
                if path is None or not path.is_file():
                    missing += 1
                    conn.execute(
                        "UPDATE issues SET status = ?, updated_at = ? WHERE key = ?",
                        (
                            STATUS_MISSING,
                            datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            row["key"],
                        ),
                    )
        return {"checked": checked, "missing": missing}
