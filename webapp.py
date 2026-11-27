"""Interface web (Flask). Corre num único processo: a fila de downloads vive em memória."""
import hmac
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import DATA_DIR
from history import (
    GETCOMICS_DOMAINS,
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_MANUAL,
    STATUS_MISSING,
    issue_key,
)
from jobs import QUEUED, RUNNING, DownloadQueue
from service import ComicService

PACK_CACHE_SECONDS = 600
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# estado -> (texto, classe CSS)
STATUS_LABELS = {
    STATUS_DOWNLOADED: ("✓ descarregada", "ok"),
    STATUS_FAILED: ("✗ falhou", "bad"),
    STATUS_MANUAL: ("manual", "warn"),
    STATUS_MISSING: ("✗ ficheiro em falta", "warn"),
    QUEUED: ("⏳ na fila", "info"),
    RUNNING: ("⬇ a descarregar", "info"),
}


def _is_getcomics_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and any(
        host == d or host.endswith(f".{d}") for d in GETCOMICS_DOMAINS
    )


def _load_secret_key(data_dir: Path) -> str:
    """Chave das sessões, guardada em disco para os logins sobreviverem a um reinício."""
    path = data_dir / "secret_key"
    try:
        return path.read_text().strip()
    except OSError:
        key = secrets.token_hex(32)
        data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(key)
        return key


def create_app(
    service: ComicService,
    queue: DownloadQueue,
    password: str | None = None,
    secret_key: str | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = secret_key or _load_secret_key(DATA_DIR)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    pack_cache: dict[str, tuple[float, list[dict]]] = {}

    def load_pack(url: str, title: str) -> list[dict]:
        cached = pack_cache.get(url)
        if cached and time.monotonic() - cached[0] < PACK_CACHE_SECONDS:
            return cached[1]
        issues = service.load_pack({"url": url, "title": title})
        pack_cache[url] = (time.monotonic(), issues)
        return issues

    @app.before_request
    def require_login():
        if password is None or request.endpoint in ("login", "static"):
            return None
        if session.get("auth"):
            return None
        if request.headers.get("HX-Request"):
            return Response(status=401, headers={"HX-Redirect": url_for("login")})
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            given = request.form.get("password", "")
            if password is not None and hmac.compare_digest(given.encode(), password.encode()):
                session["auth"] = True
                return redirect(url_for("index"))
            time.sleep(1)  # trava tentativas de adivinhar a palavra-passe
            flash("Palavra-passe errada.", "bad")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        page = max(1, request.args.get("page", 1, type=int))
        packs, error = [], None
        try:
            packs = service.list_packs(page)
            if not packs:
                error = f"A página {page} não tem artigos ou não existe."
        except Exception as exc:
            error = f"Erro ao carregar a página {page}: {exc}"
        return render_template("packs.html", packs=packs, page=page, error=error)

    @app.route("/pack")
    def pack():
        url = request.args.get("url", "")
        title = request.args.get("title", "") or url
        if not _is_getcomics_url(url):
            abort(400)
        try:
            issues = load_pack(url, title)
        except Exception as exc:
            flash(f"Erro ao ler o artigo: {exc}", "bad")
            return redirect(url_for("index"))

        statuses = service.issue_statuses(title, issues)
        in_queue = queue.states()
        rows = []
        for n, issue in enumerate(issues):
            state = in_queue.get(issue_key(issue["article_url"])) or statuses.get(issue["article_url"])
            text, css = STATUS_LABELS.get(state, ("-", ""))
            rows.append({"n": n, "title": issue["title"], "status": text, "css": css})

        done = sum(1 for s in statuses.values() if s == STATUS_DOWNLOADED)
        return render_template(
            "pack.html", url=url, title=title, rows=rows, done=done, base_dir=service.base_dir
        )

    @app.post("/enqueue")
    def enqueue():
        url = request.form.get("url", "")
        title = request.form.get("title", "") or url
        mode = request.form.get("mode", "")
        if not _is_getcomics_url(url):
            abort(400)

        try:
            issues = load_pack(url, title)
        except Exception as exc:
            flash(f"Erro ao ler o artigo: {exc}", "bad")
            return redirect(url_for("index"))

        if mode in ("pending", "all"):
            statuses = service.issue_statuses(title, issues)
            chosen = service.select_issues(issues, statuses, mode)
        elif mode == "selected":
            indices = {
                int(v) for v in request.form.getlist("i") if v.isdigit() and int(v) < len(issues)
            }
            chosen = [issues[i] for i in sorted(indices)]
        else:
            abort(400)

        if not chosen:
            flash("Não há edições para descarregar com esta escolha.", "warn")
        else:
            result = queue.enqueue(title, chosen)
            added = len(chosen) - result.skipped
            note = f" ({result.skipped} já estavam na fila)" if result.skipped else ""
            if result.job is None:
                flash("Todas as edições escolhidas já estão na fila.", "warn")
            elif result.ahead:
                flash(f"{added} edição(ões) em fila{note}. Há {result.ahead} trabalho(s) à frente.", "ok")
            else:
                flash(f"{added} edição(ões) adicionada(s){note}. O download começa já.", "ok")
        return redirect(url_for("pack", url=url, title=title))

    @app.route("/queue")
    def queue_page():
        return render_template("queue.html", **queue.snapshot())

    @app.route("/queue/panel")
    def queue_panel():
        return render_template("_queue_panel.html", **queue.snapshot())

    @app.route("/queue/summary")
    def queue_summary():
        return render_template("_summary.html", **queue.snapshot())

    @app.post("/queue/cancel/<int:job_id>")
    def queue_cancel(job_id: int):
        if not queue.cancel(job_id):
            flash("Esse trabalho já terminou.", "warn")
        return redirect(url_for("queue_page"))

    return app


def main() -> None:
    from waitress import serve

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8080"))
    password = os.environ.get("WEB_PASSWORD") or None

    if password is None and host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"WEB_HOST={host} expõe a aplicação na rede. Define WEB_PASSWORD para a proteger, "
            "ou usa WEB_HOST=127.0.0.1."
        )

    service = ComicService()
    queue = DownloadQueue(service)
    app = create_app(service, queue, password)
    print(f"A servir em http://{host}:{port}  (biblioteca: {service.base_dir})")
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
