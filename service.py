"""Lógica de negócio da aplicação, sem terminal: usada pelo CLI e, mais tarde, pela UI web."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal

import requests

from comicinfo import build_comicinfo
from config import HISTORY_DB, LIBRARY_DIR, MARVEL_DIR_NAME, check_library_ready
from downloader import download_single_issue, is_aria2c_available
from history import STATUS_DOWNLOADED, STATUS_FAILED, STATUS_MANUAL, History
from library import adopt_existing_files, existing_file_for, plan_destination, prune_empty_dirs
from scraper import (
    DEFAULT_TAG_PATH,
    extract_marvel_issues,
    fetch_weekly_packs,
    resolve_issue_download_links,
)

SelectionMode = Literal["pending", "all"]

EventKind = Literal["started", "saved", "read_failed", "no_links", "manual", "progress", "warning"]


@dataclass
class DownloadEvent:
    kind: EventKind
    issue: dict
    index: int  # 1-based, dentro desta execução
    total: int
    detail: str = ""  # caminho (saved), erro (read_failed), link (no_links/manual), aviso (warning)
    label: str = ""  # mirror usado (saved)
    bytes_done: int = 0  # só em "progress"
    bytes_total: int | None = None


@dataclass
class DownloadResult:
    succeeded: int
    total: int
    manual_links: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


EventCallback = Callable[[DownloadEvent], None]


class ComicService:
    def __init__(
        self,
        history: History | None = None,
        base_dir: Path | None = None,
        use_aria2c: bool | None = None,
        delay_range: tuple[float, float] = (1.8, 3.5),
    ) -> None:
        self.history = history or History(HISTORY_DB, LIBRARY_DIR)
        self.base_dir = base_dir or LIBRARY_DIR / MARVEL_DIR_NAME
        self.use_aria2c = is_aria2c_available() if use_aria2c is None else use_aria2c
        self.delay_range = delay_range

    def reconcile(self) -> dict:
        """Marca como em falta as edições do histórico que já não existem no disco."""
        return self.history.reconcile()

    def list_packs(self, page: int, tag_path: str = DEFAULT_TAG_PATH) -> list[dict]:
        return fetch_weekly_packs(page=page, tag_path=tag_path)

    def load_pack(self, pack: dict) -> list[dict]:
        return extract_marvel_issues(pack["url"])

    def issue_statuses(self, pack_title: str, issues: list[dict]) -> dict[str, str]:
        """Estado de cada edição; adota primeiro os ficheiros que já existem na biblioteca."""
        statuses = self.history.statuses(issue["article_url"] for issue in issues)
        adopt_existing_files(self.history, self.base_dir, issues, statuses, pack_title)
        return statuses

    @staticmethod
    def select_issues(
        issues: list[dict],
        statuses: dict[str, str],
        selection: SelectionMode | Iterable[int],
    ) -> list[dict]:
        """selection: "pending", "all" ou índices (base 0) das edições escolhidas."""
        if selection == "all":
            return list(issues)
        if selection == "pending":
            return [i for i in issues if statuses.get(i["article_url"]) != STATUS_DOWNLOADED]
        return [issues[i] for i in selection]

    @staticmethod
    def check_library_ready() -> tuple[bool, str]:
        return check_library_ready()

    def download(
        self,
        pack_title: str,
        issues: list[dict],
        on_event: EventCallback | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        """should_stop é verificado entre edições: a edição em curso termina sempre."""
        emit = on_event or (lambda event: None)
        total = len(issues)
        result = DownloadResult(succeeded=0, total=total)

        with requests.Session() as session:
            for idx, issue in enumerate(issues, 1):
                if should_stop is not None and should_stop():
                    result.cancelled = True
                    break
                emit(DownloadEvent("started", issue, idx, total))
                if self._download_issue(session, pack_title, issue, idx, total, emit, result):
                    result.succeeded += 1

        prune_empty_dirs(self.base_dir)  # downloads falhados podem deixar pastas de série vazias
        return result

    def _download_issue(
        self,
        session: requests.Session,
        pack_title: str,
        issue: dict,
        idx: int,
        total: int,
        emit: EventCallback,
        result: DownloadResult,
    ) -> bool:
        article_url = issue["article_url"]
        title = issue["title"]

        try:
            candidates = resolve_issue_download_links(
                article_url, session=session, delay_range=self.delay_range
            )
        except Exception as exc:
            result.manual_links.append((title, f"{article_url} ({exc})"))
            self.history.record(
                article_url, title, STATUS_FAILED, pack_title=pack_title, note=str(exc)
            )
            emit(DownloadEvent("read_failed", issue, idx, total, detail=str(exc)))
            return False

        if not candidates:
            result.manual_links.append((title, article_url))
            self.history.record(
                article_url, title, STATUS_MANUAL, pack_title=pack_title, note=article_url
            )
            emit(DownloadEvent("no_links", issue, idx, total, detail=article_url))
            return False

        def on_progress(done: int, size: int | None) -> None:
            emit(DownloadEvent("progress", issue, idx, total, bytes_done=done, bytes_total=size))

        fallback_url = candidates[0]["url"]
        for cand in candidates:
            target_dir, target_stem = plan_destination(self.base_dir, title)
            # Em downloads forçados substitui o ficheiro desta edição em vez de duplicar
            overwrite = existing_file_for(self.history, self.base_dir, title, article_url) is not None
            ok, outcome = download_single_issue(
                title=title,
                url=cand["url"],
                target_dir=target_dir,
                session=session,
                use_aria2c=self.use_aria2c,
                target_stem=target_stem,
                overwrite=overwrite,
                comicinfo=build_comicinfo(title, article_url),
                on_progress=on_progress,
                log=lambda message: emit(DownloadEvent("warning", issue, idx, total, detail=message)),
            )
            if ok:
                self.history.record(
                    article_url, title, STATUS_DOWNLOADED,
                    pack_title=pack_title, file_path=Path(outcome),
                )
                emit(DownloadEvent("saved", issue, idx, total, detail=outcome, label=cand["label"]))
                return True
            fallback_url = outcome

        result.manual_links.append((title, fallback_url))
        self.history.record(
            article_url, title, STATUS_MANUAL, pack_title=pack_title, note=fallback_url
        )
        emit(DownloadEvent("manual", issue, idx, total, detail=fallback_url))
        return False
