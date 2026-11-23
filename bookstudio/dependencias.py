"""Bibliotecas do BookStudio: confere ao abrir e oferece instalar as que faltam (quem atualizou pelo .zip ou pelo
git a partir da versão 1.x ainda não tem as do módulo de e-books). Sem nada além da biblioteca padrão, porque
roda antes de tudo. As pesadas e opcionais (litellm para IA, faster-whisper para transcrever) são instaladas na
hora em que a função é usada."""
import importlib.util
import os
import subprocess
import sys

NECESSARIAS = {
    "edge_tts": "edge-tts", "bs4": "beautifulsoup4", "lxml": "lxml", "pypdf": "pypdf", "pdfplumber": "pdfplumber",
    "mobi": "mobi", "langdetect": "langdetect", "mutagen": "mutagen", "deep_translator": "deep-translator",
    "PIL": "pillow", "rich": "rich", "questionary": "questionary", "pymupdf": "pymupdf",
    "lingua": "lingua-language-detector", "charset_normalizer": "charset-normalizer", "platformdirs": "platformdirs",
    "keyring": "keyring", "httpx": "httpx",
}
OPCIONAIS = {"litellm": "litellm", "faster_whisper": "faster-whisper"}


def faltando(modulos=NECESSARIAS):
    return [pacote for modulo, pacote in modulos.items() if importlib.util.find_spec(modulo) is None]


def instalar(pacotes):
    """pip install no mesmo Python do app; se a pasta do sistema não aceitar, tenta na pasta do usuário."""
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if subprocess.run(base + pacotes).returncode == 0:
        return True
    return subprocess.run(base + ["--user"] + pacotes).returncode == 0


def garantir(perguntar=True):
    """Instala o que falta (perguntando antes, se for um terminal). Devolve True se está tudo lá."""
    pacotes = faltando()
    if not pacotes:
        return True
    from bookstudio.i18n import t
    print("\n  " + t("deps_missing", libs=", ".join(pacotes)))
    if perguntar and sys.stdin.isatty():
        try:
            r = input("  " + t("deps_install_q") + " [S/n] ").strip().lower()
        except EOFError:
            r = ""
        if r and r[0] in ("n",):
            print("  " + t("deps_manual", command=f"{os.path.basename(sys.executable)} -m pip install " + " ".join(pacotes)))
            return False
    ok = instalar(pacotes)
    if ok:
        importlib.invalidate_caches()
        print("  " + t("deps_installed"))
    else:
        print("  " + t("deps_manual", command=f"{sys.executable} -m pip install " + " ".join(pacotes)))
    return ok and not faltando()


def opcional(modulo, perguntar):
    """Instala uma opcional (litellm, faster-whisper) quando a função que precisa dela é escolhida.
    `perguntar`: função que devolve True para instalar."""
    if importlib.util.find_spec(modulo) is not None:
        return True
    if not perguntar():
        return False
    ok = instalar([OPCIONAIS[modulo]])
    importlib.invalidate_caches()
    return ok and importlib.util.find_spec(modulo) is not None
