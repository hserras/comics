"""
ComicInfo.xml dentro dos .cbz: é o que o Komga, o Kavita e o Plex leem para organizar a biblioteca.

Só se grava em ficheiros que são mesmo zip (um .cbz que na verdade é RAR fica como está) e
nunca se sobrescreve um ComicInfo.xml que o ficheiro já traga. Os campos seguem a ordem do
esquema ComicInfo 2.0.
"""
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from titles import parse_title

COMICINFO_NAME = "ComicInfo.xml"
PUBLISHER = "Marvel"


def _plain_number(number: str) -> str:
    """'009' -> '9', '000.1' -> '0.1' (o ComicInfo usa o número sem zeros à esquerda)."""
    match = re.fullmatch(r"(\d+)(\..+)?", number)
    return f"{int(match.group(1))}{match.group(2) or ''}" if match else number


def build_comicinfo(title: str, article_url: str | None = None) -> str | None:
    """XML da edição, ou None se o título não for reconhecido com segurança."""
    parsed = parse_title(title)
    if parsed is None:
        return None

    root = ET.Element("ComicInfo")
    fields = [
        ("Series", parsed.series),
        ("Number", _plain_number(parsed.number)),
        ("Year", parsed.year),
        ("Publisher", PUBLISHER),
        ("Web", article_url),
    ]
    for tag, value in fields:
        if value:
            ET.SubElement(root, tag).text = value
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def has_comicinfo(path: Path) -> bool:
    with zipfile.ZipFile(path) as zf:
        return any(name.lower() == COMICINFO_NAME.lower() for name in zf.namelist())


def can_tag(path: Path) -> bool:
    """É um zip legível e ainda sem ComicInfo.xml."""
    try:
        return zipfile.is_zipfile(path) and not has_comicinfo(path)
    except zipfile.BadZipFile:
        return False


def add_comicinfo(path: Path, xml: str) -> bool:
    """
    Acrescenta o ComicInfo.xml ao zip, no próprio ficheiro. Devolve False (sem tocar no ficheiro)
    se não for um zip legível ou se já tiver ComicInfo.xml. Pensado para ficheiros temporários: se
    a gravação falhar a meio, o zip fica inutilizável e o OSError sobe.
    """
    if not can_tag(path):
        return False
    with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(COMICINFO_NAME, xml.encode("utf-8"))
    return True


def tag_existing(path: Path, xml: str) -> bool:
    """Como add_comicinfo, mas para um ficheiro final: trabalha numa cópia e só a substitui no fim."""
    if path.suffix.lower() != ".cbz" or not can_tag(path):
        return False
    work = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tag")
    try:
        shutil.copyfile(path, work)
        add_comicinfo(work, xml)
        work.replace(path)
    finally:
        work.unlink(missing_ok=True)
    return True
