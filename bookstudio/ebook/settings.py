"""Configurações, pastas do app e chaves de API.

Chaves nunca vão para arquivo: ficam no cofre do sistema (``keyring``, que no
Windows é o Gerenciador de Credenciais) ou em variáveis de ambiente.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_log_dir

from . import APP_NAME

CONFIG_DIR = Path(user_config_dir(APP_NAME, appauthor=False))
CACHE_DIR = Path(user_cache_dir(APP_NAME, appauthor=False))
LOG_DIR = Path(user_log_dir(APP_NAME, appauthor=False))
CONFIG_FILE = CONFIG_DIR / "traducao.json"
PROVIDERS_FILE = Path(__file__).parent / "data" / "provedores.json"
KEYRING_SERVICE = "BookStudio"
KEYRING_ANTIGO = "ebooktr"  # chaves salvas pelo Ebook Studio continuam valendo
CONFIG_ANTIGA = Path(user_config_dir("ebooktr", appauthor=False)) / "config.json"

log = logging.getLogger(__name__)


@dataclass
class Settings:
    engine: str = "llm"  # "llm" ou "google"
    provider: str = "gemini"
    model: str = ""
    api_base: str = ""
    target_lang: str = "pt-BR"
    recent_targets: list[str] = field(default_factory=list)
    llm_parallel: int = 4
    google_parallel: int = 2
    max_cost_usd: float = 5.0  # teto por livro; 0 = sem teto

    @classmethod
    def load(cls) -> "Settings":
        try:
            arquivo = CONFIG_FILE if CONFIG_FILE.exists() or not CONFIG_ANTIGA.exists() else CONFIG_ANTIGA
            data = json.loads(arquivo.read_text(encoding="utf-8"))
            known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**known)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError) as e:
            log.warning("config inválida, usando padrões: %s", e)
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)

    def remember_target(self, code: str) -> None:
        self.target_lang = code
        self.recent_targets = [code] + [c for c in self.recent_targets if c != code][:4]


def load_providers() -> dict:
    data = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
    return data["provedores"]


# ----------------------------------------------------------------------- chaves


def get_saved_key(provider: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, provider) or keyring.get_password(KEYRING_ANTIGO, provider)
    except Exception as e:  # noqa: BLE001 - sem cofre disponível
        log.info("keyring indisponível: %s", e)
        return None


def save_key(provider: str, key: str) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, provider, key)
        return True
    except Exception as e:  # noqa: BLE001
        log.info("não foi possível salvar no keyring: %s", e)
        return False


def delete_key(provider: str) -> bool:
    try:
        import keyring

        apagou = False
        for servico in (KEYRING_SERVICE, KEYRING_ANTIGO):
            try:
                keyring.delete_password(servico, provider)
                apagou = True
            except Exception:  # noqa: BLE001 - não havia chave neste
                pass
        return apagou
    except Exception:  # noqa: BLE001
        return False


def mask(key: str) -> str:
    return "…" + key[-4:] if len(key) > 8 else "…"


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "bookstudio.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler]
    for noisy in ("LiteLLM", "litellm", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return path

