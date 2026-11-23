"""config: parte do BookStudio (separado de audiobook_cli.py)."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from bookstudio import atualizacao, i18n, ui
from bookstudio.i18n import t
from bookstudio.caminhos import BASE, MOTOR
from bookstudio import VERSAO



# ---------------------------------------------------------------------------------------------
# Últimas escolhas (voz por idioma, formato, qualidade, pasta, texto sincronizado), sugeridas na próxima vez
# ---------------------------------------------------------------------------------------------
def caminho_config():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "BookStudio", "config.json")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/BookStudio/config.json")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "bookstudio", "config.json")


def ler_config():
    try:
        with open(caminho_config(), encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def salvar_config(config):
    try:
        os.makedirs(os.path.dirname(caminho_config()), exist_ok=True)
        with open(caminho_config(), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=1)
    except OSError:
        pass  # sem onde guardar: só não sugere as escolhas na próxima vez
