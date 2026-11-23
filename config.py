"""
Configuração central. Tudo o que muda entre o PC e a NAS vem de variáveis de ambiente:

  LIBRARY_DIR  pasta onde ficam os comics (na NAS: a partilha montada, ex.: /comics)
  DATA_DIR     pasta da base de dados do histórico (fora da partilha de rede)
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_library_env = os.environ.get("LIBRARY_DIR", "").strip()
_data_env = os.environ.get("DATA_DIR", "").strip()

LIBRARY_DIR = Path(_library_env).expanduser() if _library_env else BASE_DIR / "Downloads"
DATA_DIR = Path(_data_env).expanduser() if _data_env else BASE_DIR / "data"
HISTORY_DB = DATA_DIR / "history.db"

MARVEL_DIR_NAME = "Marvel"

# Só a pasta por omissão é criada automaticamente. Uma LIBRARY_DIR definida pelo utilizador
# tem de existir: se a NAS estiver desligada ou o volume Docker não estiver montado, criar a
# pasta escreveria os ficheiros no sítio errado em silêncio.
_LIBRARY_IS_DEFAULT = not _library_env


def check_library_ready(library_dir: Path = LIBRARY_DIR) -> tuple[bool, str]:
    """Confirma que o destino existe e aceita escrita. Devolve (ok, mensagem_de_erro)."""
    try:
        if not library_dir.is_dir():
            if not (_LIBRARY_IS_DEFAULT and library_dir == LIBRARY_DIR):
                return False, (
                    f"A pasta de destino não existe ou está inacessível: {library_dir}\n"
                    "A NAS está ligada e a partilha montada?"
                )
            library_dir.mkdir(parents=True, exist_ok=True)

        probe = library_dir / f".write_test_{os.getpid()}"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        return False, f"Sem permissão de escrita em {library_dir}: {exc}"

    return True, ""
