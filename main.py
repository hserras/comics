#!/usr/bin/env python3
import re

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Prompt
from rich.table import Table

from history import (
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_MANUAL,
    STATUS_MISSING,
)
from scraper import DEFAULT_TAG_PATH
from service import ComicService, DownloadEvent

console = Console()

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


class DownloadView:
    """Mostra no terminal os eventos de um download: linhas de estado e a barra de progresso."""

    def __init__(self) -> None:
        self._progress: Progress | None = None
        self._task = None

    def _stop_bar(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task = None

    def __call__(self, event: DownloadEvent) -> None:
        issue = event.issue
        if event.kind == "progress":
            if self._progress is None:
                self._progress = Progress(
                    BarColumn(bar_width=None),
                    "[progress.percentage]{task.percentage:>3.1f}%",
                    "•",
                    DownloadColumn(binary_units=True),
                    "•",
                    TransferSpeedColumn(),
                    "•",
                    TimeRemainingColumn(compact=True),
                    transient=True,
                    console=console,
                )
                self._task = self._progress.add_task(issue["title"], total=event.bytes_total)
                self._progress.start()
            self._progress.update(self._task, completed=event.bytes_done, total=event.bytes_total)
            return

        self._stop_bar()
        if event.kind == "started":
            console.print(f"\n[bold][{event.index}/{event.total}][/bold] {issue['title']}")
        elif event.kind == "warning":
            console.print(f"[yellow]{event.detail}[/yellow]")
        elif event.kind == "read_failed":
            console.print(f"[yellow]⚠ Falha ao ler página da edição:[/yellow] {event.detail}")
        elif event.kind == "no_links":
            console.print(f"[yellow]⚠ Sem links detetados, abrir artigo:[/yellow] {event.detail}")
        elif event.kind == "saved":
            console.print(f"[green]✓ Guardado ({event.label}):[/green] {event.detail}")
        elif event.kind == "manual":
            console.print(f"[yellow]⚠ Requer download manual / host externo:[/yellow] {event.detail}")


def main() -> None:
    page = 1
    last_valid_page = 1
    tag_path = DEFAULT_TAG_PATH

    service = ComicService()
    reconciled = service.reconcile()
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
                    packs = service.list_packs(page, tag_path)
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
                marvel_issues = service.load_pack(selected_pack)
            except Exception as exc:
                error = exc

        if error is not None:
            console.print(f"[bold red]Erro ao ler o artigo:[/bold red] {error}")
            Prompt.ask("Prime Enter para continuar")
            continue

        while True:
            statuses = service.issue_statuses(selected_pack["title"], marvel_issues)

            console.clear()
            show_marvel_table(selected_pack["title"], marvel_issues, statuses)

            console.print(
                f"\nPasta de destino: [bold cyan]{service.base_dir}[/bold cyan] "
                "[dim](uma subpasta por série)[/dim]"
            )
            show_issues_menu_options()

            raw_selection = Prompt.ask("\nEscolha", default="a")

            parsed = parse_issue_selection(raw_selection, len(marvel_issues))
            if parsed is None:
                console.print("[red]Seleção inválida. Vê as opções de download acima.[/red]")
                Prompt.ask("Prime Enter para tentar novamente")
                continue

            if parsed == "back":
                break

            issues_to_download = service.select_issues(marvel_issues, statuses, parsed)

            if not issues_to_download:
                console.print("[green]Todas as edições deste pack já estão descarregadas.[/green]")
                Prompt.ask("Prime Enter para continuar")
                continue

            library_ok, library_error = service.check_library_ready()
            if not library_ok:
                console.print(f"[bold red]{library_error}[/bold red]")
                Prompt.ask("Prime Enter para continuar")
                continue

            skipped = len(marvel_issues) - len(issues_to_download) if parsed == "pending" else 0
            result = service.download(selected_pack["title"], issues_to_download, DownloadView())

            summary_lines = [
                f"Concluídos automaticamente: [bold green]{result.succeeded}/{result.total}[/bold green]",
                f"Pasta: [cyan]{service.base_dir}[/cyan]",
            ]
            if skipped:
                summary_lines.insert(1, f"Já descarregadas (saltadas): [bold]{skipped}[/bold]")
            if result.manual_links:
                summary_lines.append("\n[yellow]Links que exigem abertura no browser:[/yellow]")
                for title, link in result.manual_links:
                    summary_lines.append(f"  • {title}: {link}")

            console.print(Panel("\n".join(summary_lines), title="Resumo do Download", border_style="green"))
            Prompt.ask("\nPrime Enter para voltar à lista de edições deste pack")


if __name__ == "__main__":
    main()
