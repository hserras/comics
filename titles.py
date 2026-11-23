"""
Parser de títulos de edições e nomes de ficheiro previsíveis para a biblioteca.

Estrutura:  <Série>/<Série> #NNN (Ano).<ext>
Um título que não se reconhece com segurança vai para UNSORTED_DIR com o nome original, em
vez de arriscar misturar edições na pasta errada.
"""
import re
from dataclasses import dataclass

UNSORTED_DIR = "_Por classificar"

# "Série #016 (2017)", "Série #1", "Série #0.1 (of 5) (2020)"
_ISSUE_RE = re.compile(r"^(?P<series>.+?)\s*#\s*(?P<number>\d+)(?P<frac>\.\w+)?(?P<rest>.*)$")
_YEAR_RE = re.compile(r"\(\s*(?P<year>(?:19|20)\d{2})\s*\)")
_OF_RE = re.compile(r"\(\s*of\s+\d+\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedTitle:
    series: str
    number: str  # com zeros à esquerda (ordena bem em ordem alfabética), ex.: "016"
    year: str | None

    @property
    def stem(self) -> str:
        year = f" ({self.year})" if self.year else ""
        return f"{self.series} #{self.number}{year}"


def clean_name(name: str) -> str:
    """Nome seguro para pasta/ficheiro em Windows, Linux e NAS."""
    name = name.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    name = re.sub(r"\s*[–—]\s*", " - ", name)
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # O Windows não aceita nomes de pasta terminados em ponto ou espaço
    return name.rstrip(". ")


def parse_title(title: str) -> ParsedTitle | None:
    """Devolve None se o título não tiver série e número reconhecíveis com segurança."""
    match = _ISSUE_RE.match(title.strip())
    if not match:
        return None

    rest = match.group("rest")
    year_match = _YEAR_RE.search(rest)
    year = year_match.group("year") if year_match else None
    rest = _YEAR_RE.sub("", rest)
    rest = _OF_RE.sub("", rest)

    # Texto a mais depois do número (ex.: variantes) é ambíguo: não arriscar
    if rest.strip(" -–—.,"):
        return None

    series = clean_name(match.group("series"))
    if not series:
        return None

    number = f"{int(match.group('number')):03d}{match.group('frac') or ''}"
    return ParsedTitle(series=series, number=number, year=year)


def plan_names(title: str) -> tuple[str, str]:
    """(pasta, nome do ficheiro sem extensão) para a edição, relativos à pasta Marvel."""
    parsed = parse_title(title)
    if parsed is None:
        return UNSORTED_DIR, clean_name(title) or "comic_download"
    return parsed.series, parsed.stem
