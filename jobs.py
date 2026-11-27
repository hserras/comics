"""Fila de downloads em memória. Um único worker processa os trabalhos pela ordem de chegada."""
import itertools
import threading
from collections import deque
from dataclasses import dataclass, field, replace
from typing import NamedTuple

from history import issue_key
from service import ComicService, DownloadEvent

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class Job:
    id: int
    pack_title: str
    issues: list[dict]
    state: str = QUEUED
    index: int = 0  # edição em curso, base 1 (0 = ainda não começou)
    current_title: str = ""
    bytes_done: int = 0
    bytes_total: int | None = None
    succeeded: int = 0
    log: list[tuple[str, str]] = field(default_factory=list)  # (nível "ok"/"warn", texto)
    manual_links: list[tuple[str, str]] = field(default_factory=list)
    error: str = ""
    cancel_requested: bool = False

    @property
    def total(self) -> int:
        return len(self.issues)

    @property
    def percent(self) -> int | None:
        if not self.bytes_total:
            return None
        return min(100, self.bytes_done * 100 // self.bytes_total)


class EnqueueResult(NamedTuple):
    job: Job | None  # None quando todas as edições já estavam na fila
    skipped: int  # edições ignoradas por já estarem na fila ou em curso
    ahead: int  # trabalhos à frente deste (em curso + em fila)


class DownloadQueue:
    def __init__(self, service: ComicService, keep_finished: int = 20, autostart: bool = True) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._pending: deque[Job] = deque()
        self._running: Job | None = None
        self._finished: deque[Job] = deque(maxlen=keep_finished)
        self._ids = itertools.count(1)
        if autostart:
            threading.Thread(target=self._loop, name="download-worker", daemon=True).start()

    def enqueue(self, pack_title: str, issues: list[dict]) -> EnqueueResult:
        """Põe as edições em fila. As que já estão na fila ou em curso são ignoradas."""
        with self._lock:
            busy = set(self._states_locked())
            fresh: list[dict] = []
            for issue in issues:
                key = issue_key(issue["article_url"])
                if key not in busy:
                    busy.add(key)
                    fresh.append(issue)

            skipped = len(issues) - len(fresh)
            ahead = len(self._pending) + (1 if self._running else 0)
            if not fresh:
                return EnqueueResult(None, skipped, ahead)

            job = Job(id=next(self._ids), pack_title=pack_title, issues=fresh)
            self._pending.append(job)
            self._wake.notify()
            return EnqueueResult(replace(job, issues=list(fresh)), skipped, ahead)

    def states(self) -> dict[str, str]:
        """Chave da edição (issue_key) -> QUEUED ou RUNNING, para as que ainda não terminaram."""
        with self._lock:
            return self._states_locked()

    def _states_locked(self) -> dict[str, str]:
        result: dict[str, str] = {}
        job = self._running
        if job is not None:
            for pos, issue in enumerate(job.issues, 1):
                if pos < job.index:
                    continue  # já tratada neste trabalho
                state = RUNNING if pos == job.index else QUEUED
                result[issue_key(issue["article_url"])] = state
        for job in self._pending:
            for issue in job.issues:
                result[issue_key(issue["article_url"])] = QUEUED
        return result

    def cancel(self, job_id: int) -> bool:
        """Remove um trabalho em fila, ou pede a paragem do que está em curso (após a edição atual)."""
        with self._lock:
            for job in self._pending:
                if job.id == job_id:
                    self._pending.remove(job)
                    job.state = CANCELLED
                    self._finished.appendleft(job)
                    return True
            if self._running is not None and self._running.id == job_id:
                self._running.cancel_requested = True
                return True
        return False

    def snapshot(self) -> dict:
        """Cópia do estado atual, segura para mostrar enquanto o worker continua a trabalhar."""
        with self._lock:
            def copy(job: Job) -> Job:
                return replace(job, log=list(job.log), manual_links=list(job.manual_links))

            return {
                "running": copy(self._running) if self._running else None,
                "pending": [copy(j) for j in self._pending],
                "finished": [copy(j) for j in self._finished],
            }

    def run_next(self, block: bool = True) -> bool:
        """Processa o próximo trabalho. Devolve False se não havia nenhum e block=False."""
        with self._wake:
            while not self._pending:
                if not block:
                    return False
                self._wake.wait()
            job = self._pending.popleft()
            job.state = RUNNING
            self._running = job

        try:
            self._process(job)
        except Exception as exc:
            with self._lock:
                job.state = FAILED
                job.error = str(exc)
        finally:
            with self._lock:
                self._running = None
                self._finished.appendleft(job)
        return True

    def _loop(self) -> None:
        while True:
            self.run_next()

    def _process(self, job: Job) -> None:
        library_ok, library_error = self._service.check_library_ready()
        if not library_ok:
            raise RuntimeError(library_error)

        result = self._service.download(
            job.pack_title,
            job.issues,
            on_event=lambda event: self._on_event(job, event),
            should_stop=lambda: job.cancel_requested,
        )
        with self._lock:
            job.manual_links = result.manual_links
            job.state = CANCELLED if result.cancelled else DONE

    def _on_event(self, job: Job, event: DownloadEvent) -> None:
        title = event.issue["title"]
        with self._lock:
            if event.kind == "started":
                job.index = event.index
                job.current_title = title
                job.bytes_done = 0
                job.bytes_total = None
            elif event.kind == "progress":
                job.bytes_done = event.bytes_done
                job.bytes_total = event.bytes_total
            elif event.kind == "saved":
                job.succeeded += 1
                job.log.append(("ok", f"{title}: guardado ({event.label})"))
            elif event.kind == "warning":
                job.log.append(("warn", f"{title}: {event.detail}"))
            elif event.kind == "read_failed":
                job.log.append(("warn", f"{title}: falha ao ler a página ({event.detail})"))
            elif event.kind == "no_links":
                job.log.append(("warn", f"{title}: sem links detetados"))
            elif event.kind == "manual":
                job.log.append(("warn", f"{title}: requer download manual"))
