#!/usr/bin/env python3
import re
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from config import HISTORY_DB, LIBRARY_DIR, MARVEL_DIR_NAME, check_library_ready
from downloader import download_single_issue, is_aria2c_available, sanitize_filename
from history import (
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_MANUAL,
    STATUS_MISSING,
    History,
)
from scraper import (
    DEFAULT_TAG_PATH,
    extract_marvel_issues,
    fetch_weekly_packs,
    resolve_issue_download_links,
)

console = Console()
BASE_DOWNLOAD_DIR = LIBRARY_DIR / MARVEL_DIR_NAME

STATUS_LABELS = {
    STATUS_DOWNLOADED: "[green]✓ descarregada[/green]",
    STATUS_FAILED: "[red]✗ falhou[/red]",
    STATUS_MANUAL: "[yellow]manual[/yellow]",
    STATUS_MISSING: "[yellow]✗ ficheiro em falta[/yellow]",
}


def show_packs_table(packs: list[dict], page: int, tag_path: str) -> None:
    console.clear()
    table = Table(
        title=f"[bold magenta]GetComics ({tag_path}) — Página {page}[/bold magenta]",
        header_style="bold cyan",
        border_style="bright_black",
    )
    table.add_column("Nº", style="dim", width=4, justify="center")
    table.add_column("Artigo / Pack", style="white")
    table.add_column("Info", style="green")

    for idx, pack in enumerate(packs, 1):
        table.add_row(str(idx), pack["title"], pack["meta"] or "-")

    console.print(table)


def show_packs_menu_options() -> None:
    console.print("\n[bold cyan]Opções:[/bold cyan]")
    console.print("  • [bold]<nº>[/bold] — Abrir o artigo / pack indicado (ex.: [bold]1[/bold])")
    console.print("  • [bold]p<nº>[/bold] — Saltar para uma página específica (ex.: [bold]p50[/bold])")
    console.print("  • [bold]n[/bold] — Próxima página")
    console.print("  • [bold]p[/bold] — Página anterior")
    console.print("  • [bold]q[/bold] — Sair")


def show_marvel_table(pack_title: str, issues: list[dict], statuses: dict[str, str]) -> None:
    table = Table(
        title=f"[bold yellow]Marvel em: {pack_title}[/bold yellow]",
        header_style="bold cyan",
        border_style="bright_black",
    )
    table.add_column("Nº", style="dim", width=4, justify="center")
    table.add_column("Edição / Item", style="white")
    table.add_column("Estado")

    for idx, issue in enumerate(issues, 1):
        status = statuses.get(issue["article_url"])
        table.add_row(str(idx), issue["title"], STATUS_LABELS.get(status, "-"))

    done = sum(1 for s in statuses.values() if s == STATUS_DOWNLOADED)
    console.print(table)
    console.print(f"Descarregadas: [bold]{done}/{len(issues)}[/bold]")


def show_issues_menu_options() -> None:
    console.print("\n[bold cyan]Opções de download:[/bold cyan]")
    console.print("  • [bold]a[/bold] — Descarregar as edições que faltam (salta as já descarregadas)")
    console.print("  • [bold]t[/bold] — Descarregar todas, incluindo as já descarregadas")
    console.print("  • [bold]<nºs / intervalos>[/bold] — Descarregar apenas os escolhidos (ex.: [bold]1, 4, 7-9[/bold])")
    console.print("  • [bold]v[/bold] — Voltar à lista de artigos / packs")


def parse_page_jump(raw_choice: str) -> int | None:
    match = re.fullmatch(r"(?:p|pg|pag|pagina)\s*(\d+)", raw_choice.strip().lower())
    if not match:
        return None
    page_num = int(match.group(1))
    return page_num if page_num >= 1 else None


def parse_issue_selection(raw_choice: str, total: int) -> list[int] | str | None:
    choice = raw_choice.strip().lower()
    if choice in {"a", "pending", ""}:
        return "pending"
    if choice in {"t", "todas", "all"}:
        return "all"
    if choice in {"v", "voltar", "b", "back", "q"}:
        return "back"

    selected_indices: set[int] = set()
    parts = [p.strip() for p in choice.split(",") if p.strip()]
    if not parts:
        return None

    for part in parts:
        if "-" in part:
            bounds = part.split("-", 1)
            if not (bounds[0].isdigit() and bounds[1].isdigit()):
                return None
            start, end = int(bounds[0]), int(bounds[1])
            if start > end or start < 1 or end > total:
                return None
            selected_indices.update(range(start - 1, end))
        else:
            if not part.isdigit():
                return None
            num = int(part)
            if not (1 <= num <= total):
                return None
            selected_indices.add(num - 1)

    return sorted(selected_indices)


def main() -> None:
    page = 1
    last_valid_page = 1
    tag_path = DEFAULT_TAG_PATH
    use_aria2c = is_aria2c_available()

    history = History(HISTORY_DB, LIBRARY_DIR)
    reconciled = history.reconcile()
    if reconciled["missing"]:
        console.print(
            f"[yellow]{reconciled['missing']} edição(ões) do histórico já não existem no disco "
            "e voltam a aparecer como por descarregar.[/yellow]"
        )
        Prompt.ask("Prime Enter para continuar")

    while True:
        while True:
            packs = None
            error = None
            with console.status(f"[bold green]A carregar {tag_path} (página {page})...[/bold green]"):
                try:
                    packs = fetch_weekly_packs(page=page, tag_path=tag_path)
                except Exception as exc:
                    error = exc

            if error is not None:
                console.print(f"[bold red]Erro ao carregar página {page}:[/bold red] {error}")
                if page != last_valid_page:
                    page = last_valid_page
                    Prompt.ask(f"A regressar à página {last_valid_page}. Prime Enter para continuar")
                    continue
                return

            if not packs:
                console.print(f"[yellow]A página {page} não tem artigos ou não existe.[/yellow]")
                if page != last_valid_page:
                    page = last_valid_page
                    Prompt.ask(f"A regressar à página {last_valid_page}. Prime Enter para continuar")
                    continue
                return

            last_valid_page = page
            break

        show_packs_table(packs, page, tag_path)
        show_packs_menu_options()
        raw_choice = Prompt.ask("\nEscolha", default="1").strip().lower()

        if raw_choice == "q":
            console.print("[yellow]A sair.[/yellow]")
            return
        if raw_choice == "n":
            page += 1
            continue
        if raw_choice == "p":
            page = max(1, page - 1)
            continue

        jump_page = parse_page_jump(raw_choice)
        if jump_page is not None:
            page = jump_page
            continue

        if not raw_choice.isdigit() or not (1 <= int(raw_choice) <= len(packs)):
            console.print("[red]Opção inválida. Vê a lista de opções acima.[/red]")
            Prompt.ask("Prime Enter para continuar")
            continue

        selected_pack = packs[int(raw_choice) - 1]

        marvel_issues = None
        error = None
        with console.status(f"[bold green]A extrair conteúdos de '{selected_pack['title']}'...[/bold green]"):
            try:
                marvel_issues = extract_marvel_issues(selected_pack["url"])
            except Exception as exc:
                error = exc

        if error is not None:
            console.print(f"[bold red]Erro ao ler o artigo:[/bold red] {error}")
            Prompt.ask("Prime Enter para continuar")
            continue

        while True:
            statuses = history.statuses(issue["article_url"] for issue in marvel_issues)

            console.clear()
            show_marvel_table(selected_pack["title"], marvel_issues, statuses)

            pack_folder = BASE_DOWNLOAD_DIR / sanitize_filename(selected_pack["title"])
            console.print(f"\nPasta de destino: [bold cyan]{pack_folder}[/bold cyan]")
            show_issues_menu_options()

            raw_selection = Prompt.ask("\nEscolha", default="a")

            parsed = parse_issue_selection(raw_selection, len(marvel_issues))
            if parsed is None:
                console.print("[red]Seleção inválida. Vê as opções de download acima.[/red]")
                Prompt.ask("Prime Enter para tentar novamente")
                continue

            if parsed == "back":
                break

            if parsed == "all":
                issues_to_download = marvel_issues
            elif parsed == "pending":
                issues_to_download = [
                    issue for issue in marvel_issues
                    if statuses.get(issue["article_url"]) != STATUS_DOWNLOADED
                ]
            else:
                issues_to_download = [marvel_issues[i] for i in parsed]

            if not issues_to_download:
                console.print("[green]Todas as edições deste pack já estão descarregadas.[/green]")
                Prompt.ask("Prime Enter para continuar")
                continue

            library_ok, library_error = check_library_ready()
            if not library_ok:
                console.print(f"[bold red]{library_error}[/bold red]")
                Prompt.ask("Prime Enter para continuar")
                continue

            skipped = len(marvel_issues) - len(issues_to_download) if parsed == "pending" else 0
            succeeded = 0
            manual_links: list[tuple[str, str]] = []

            with requests.Session() as session:
                for idx, issue in enumerate(issues_to_download, 1):
                    console.print(f"\n[bold][{idx}/{len(issues_to_download)}][/bold] {issue['title']}")

                    try:
                        candidates = resolve_issue_download_links(
                            issue["article_url"],
                            session=session,
                            delay_range=(1.8, 3.5),
                        )
                    except Exception as exc:
                        manual_links.append((issue["title"], f"{issue['article_url']} ({exc})"))
                        history.record(
                            issue["article_url"], issue["title"], STATUS_FAILED,
                            pack_title=selected_pack["title"], note=str(exc),
                        )
                        console.print(f"[yellow]⚠ Falha ao ler página da edição:[/yellow] {exc}")
                        continue

                    if not candidates:
                        manual_links.append((issue["title"], issue["article_url"]))
                        history.record(
                            issue["article_url"], issue["title"], STATUS_MANUAL,
                            pack_title=selected_pack["title"], note=issue["article_url"],
                        )
                        console.print(f"[yellow]⚠ Sem links detetados, abrir artigo:[/yellow] {issue['article_url']}")
                        continue

                    downloaded_this_issue = False
                    fallback_url = candidates[0]["url"]

                    for cand in candidates:
                        ok, result = download_single_issue(
                            title=issue["title"],
                            url=cand["url"],
                            target_dir=pack_folder,
                            session=session,
                            use_aria2c=use_aria2c,
                        )
                        if ok:
                            succeeded += 1
                            downloaded_this_issue = True
                            history.record(
                                issue["article_url"], issue["title"], STATUS_DOWNLOADED,
                                pack_title=selected_pack["title"], file_path=Path(result),
                            )
                            console.print(f"[green]✓ Guardado ({cand['label']}):[/green] {result}")
                            break
                        else:
                            fallback_url = result

                    if not downloaded_this_issue:
                        manual_links.append((issue["title"], fallback_url))
                        history.record(
                            issue["article_url"], issue["title"], STATUS_MANUAL,
                            pack_title=selected_pack["title"], note=fallback_url,
                        )
                        console.print(f"[yellow]⚠ Requer download manual / host externo:[/yellow] {fallback_url}")

            summary_lines = [
                f"Concluídos automaticamente: [bold green]{succeeded}/{len(issues_to_download)}[/bold green]",
                f"Pasta: [cyan]{pack_folder}[/cyan]",
            ]
            if skipped:
                summary_lines.insert(1, f"Já descarregadas (saltadas): [bold]{skipped}[/bold]")
            if manual_links:
                summary_lines.append("\n[yellow]Links que exigem abertura no browser:[/yellow]")
                for title, link in manual_links:
                    summary_lines.append(f"  • {title}: {link}")

            console.print(Panel("\n".join(summary_lines), title="Resumo do Download", border_style="green"))
            Prompt.ask("\nPrime Enter para voltar à lista de edições deste pack")


if __name__ == "__main__":
    main()