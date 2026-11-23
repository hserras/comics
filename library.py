"""Destinos na biblioteca: onde cada edição fica, e deteção de ficheiros que já existem."""
from pathlib import Path

from downloader import COMIC_EXTENSIONS
from history import STATUS_DOWNLOADED, History, issue_key
from titles import plan_names


def resolve_dir(base_dir: Path, folder: str) -> Path:
    """Reutiliza uma pasta existente que só difira nas maiúsculas (NAS Linux distingue-as)."""
    if base_dir.is_dir():
        wanted = folder.casefold()
        for child in base_dir.iterdir():
            if child.is_dir() and child.name.casefold() == wanted:
                return child
    return base_dir / folder


def plan_destination(base_dir: Path, title: str) -> tuple[Path, str]:
    """(pasta de destino, nome do ficheiro sem extensão) para uma edição."""
    folder, stem = plan_names(title)
    return resolve_dir(base_dir, folder), stem


def find_existing(target_dir: Path, stem: str) -> Path | None:
    """Procura um ficheiro de comic com este nome (qualquer extensão) na pasta."""
    if not target_dir.is_dir():
        return None
    wanted = stem.casefold()
    for child in target_dir.iterdir():
        if (
            child.is_file()
            and not child.name.startswith(".")
            and child.suffix.lower() in COMIC_EXTENSIONS
            and child.stem.casefold() == wanted
        ):
            return child
    return None


def existing_file_for(history: History, base_dir: Path, title: str, article_url: str) -> Path | None:
    """
    Ficheiro já existente no destino desta edição. Ignora-o se estiver registado para outra
    edição (dois artigos podem dar o mesmo nome, por exemplo sem ano), para não os misturar.
    """
    target_dir, stem = plan_destination(base_dir, title)
    existing = find_existing(target_dir, stem)
    if existing is None:
        return None
    owner = history.owner_of(existing)
    return existing if owner in (None, issue_key(article_url)) else None


def adopt_existing_files(
    history: History,
    base_dir: Path,
    issues: list[dict],
    statuses: dict[str, str],
    pack_title: str,
) -> int:
    """Regista como descarregadas as edições cujo ficheiro já está na biblioteca (ex.: copiado à mão)."""
    adopted = 0
    for issue in issues:
        url = issue["article_url"]
        if statuses.get(url) == STATUS_DOWNLOADED:
            continue
        existing = existing_file_for(history, base_dir, issue["title"], url)
        if existing is not None:
            history.record(
                url, issue["title"], STATUS_DOWNLOADED, pack_title=pack_title, file_path=existing,
                note="já existia na biblioteca",
            )
            statuses[url] = STATUS_DOWNLOADED
            adopted += 1
    return adopted


def migrate_library(history: History, base_dir: Path, apply: bool) -> list[tuple[str, str, str]]:
    """
    Reorganiza os ficheiros já descarregados (uma pasta por pack) para <Série>/<nome>.
    Sem apply, só simula. Devolve [(resultado, caminho_atual, caminho_novo)], com resultado em
    'move', 'ok' (já no sítio), 'conflict' (destino ocupado) ou 'missing' (ficheiro não existe).
    """
    results: list[tuple[str, str, str]] = []
    planned: set[str] = set()

    for row in history.downloaded_rows():
        old = history.library_dir / row["rel_path"]
        if not old.is_file():
            results.append(("missing", row["rel_path"], ""))
            continue

        target_dir, stem = plan_destination(base_dir, row["title"])
        new = target_dir / f"{stem}{old.suffix.lower()}"
        if old.parent == new.parent and old.name == new.name:
            results.append(("ok", row["rel_path"], row["rel_path"]))
            continue

        new_rel = new.relative_to(history.library_dir).as_posix()
        taken = new.exists() or new_rel.casefold() in planned
        if taken and not (old.parent == new.parent and old.name.casefold() == new.name.casefold()):
            results.append(("conflict", row["rel_path"], new_rel))
            continue

        planned.add(new_rel.casefold())
        results.append(("move", row["rel_path"], new_rel))
        if apply:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.replace(new)
            history.update_path(row["key"], new)

    if apply:
        prune_empty_dirs(base_dir)
    return results


def prune_empty_dirs(base_dir: Path) -> list[Path]:
    """Remove as subpastas vazias de base_dir (nunca a própria base_dir nem pastas com ficheiros)."""
    removed: list[Path] = []
    if not base_dir.is_dir():
        return removed
    for folder in sorted((p for p in base_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()  # falha se tiver conteúdo, incluindo .part de downloads em curso
            removed.append(folder)
        except OSError:
            pass
    return removed
