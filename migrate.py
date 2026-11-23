#!/usr/bin/env python3
"""
Reorganiza os comics já descarregados (uma pasta por pack) para <Série>/<Série> #NNN (Ano).<ext>.

  python migrate.py                       simula e mostra o que seria movido
  python migrate.py --apply               move os ficheiros e atualiza o histórico
  python migrate.py --comicinfo           simula a gravação de ComicInfo.xml nos .cbz já descarregados
  python migrate.py --apply --comicinfo   move e grava o ComicInfo.xml

Não apaga ficheiros: se o destino já existir, a edição fica onde está e aparece como conflito.
O ComicInfo.xml só é gravado em .cbz que ainda não o tenham e cujo título seja reconhecido.
"""
import argparse
import sys

from rich.console import Console
from rich.table import Table

from config import HISTORY_DB, LIBRARY_DIR, MARVEL_DIR_NAME, check_library_ready
from history import History
from library import migrate_library, tag_library

console = Console()

LABELS = {
    "move": "[green]mover[/green]",
    "ok": "[dim]já no sítio[/dim]",
    "conflict": "[red]conflito (destino ocupado)[/red]",
    "missing": "[yellow]ficheiro em falta[/yellow]",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="faz mesmo as alterações (por omissão só simula)")
    parser.add_argument("--comicinfo", action="store_true", help="grava ComicInfo.xml nos .cbz já descarregados")
    args = parser.parse_args()

    ok, error = check_library_ready()
    if not ok:
        console.print(f"[bold red]{error}[/bold red]")
        return 1

    history = History(HISTORY_DB, LIBRARY_DIR)
    results = migrate_library(history, LIBRARY_DIR / MARVEL_DIR_NAME, apply=args.apply)

    changes = [r for r in results if r[0] != "ok"]
    if changes:
        table = Table(show_lines=False)
        table.add_column("Resultado")
        table.add_column("Atual", overflow="fold")
        table.add_column("Novo", overflow="fold")
        for kind, old, new in changes:
            table.add_row(LABELS[kind], old, new)
        console.print(table)

    counts = {kind: sum(1 for r in results if r[0] == kind) for kind in LABELS}
    console.print(
        f"{counts['move']} a mover, {counts['ok']} já no sítio, "
        f"{counts['conflict']} em conflito, {counts['missing']} em falta."
    )
    if counts["move"] and not args.apply:
        console.print("[yellow]Simulação: nada foi alterado. Corre com --apply para mover.[/yellow]")
    elif counts["move"]:
        console.print("[green]Migração concluída.[/green]")

    if args.comicinfo:
        tagged = tag_library(history, apply=args.apply)
        to_tag = sum(1 for kind, _ in tagged if kind == "tag")
        console.print(
            f"ComicInfo.xml: {to_tag} {'gravados' if args.apply else 'a gravar'}, "
            f"{sum(1 for kind, _ in tagged if kind == 'skip')} ignorados "
            "(já têm metadados, não são .cbz ou o título não é reconhecido)."
        )
        if to_tag and not args.apply:
            console.print("[yellow]Simulação: nada foi alterado. Corre com --apply para gravar.[/yellow]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
