"""arquivos: parte do BookStudio (separado de audiobook_cli.py)."""
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
from bookstudio.config import salvar_config
from bookstudio.idiomas import IDIOMAS
from bookstudio.sistema import _tk


EXTENSOES = (".epub", ".pdf", ".mobi", ".azw", ".azw3", ".prc", ".txt")


TECLA_SELETOR = "o"  # reabre o seletor de arquivos/pasta depois de fechá-lo sem escolher nada


TECLA_IDIOMA = "l"  # na pergunta dos arquivos, troca o idioma da interface


def escolher_idioma_interface(config):
    """Troca o idioma da interface e guarda a escolha; "idioma do sistema" apaga a escolha guardada."""
    codigos = list(i18n.IDIOMAS)
    opcoes = [(t("language_system"), IDIOMAS[i18n.idioma_do_sistema()]["nativo"])] + [(IDIOMAS[c]["nativo"], "") for c in codigos]
    salvo = config.get("idioma")
    i = ui.escolher(t("prompt_interface_language"), opcoes, padrao=codigos.index(salvo) + 1 if salvo in codigos else 0)
    if i:
        config["idioma"] = codigos[i - 1]
    else:
        config.pop("idioma", None)
    salvar_config(config)
    i18n.configurar(config.get("idioma"))
    ui.ok(t("language_changed", language=IDIOMAS[i18n.atual]["nativo"]))


def escolher_arquivos(config):
    """Seletor do sistema; se ele for fechado sem escolher nada, dá para abri-lo de novo (Enter ou o)
    ou digitar/arrastar os arquivos. Sem seletor (sem tkinter, SSH), só digitando."""
    while True:
        raiz, dialogo = _tk()
        if raiz:
            ui.dica(t("opening_file_picker"))
            caminhos = dialogo.askopenfilenames(
                parent=raiz, title=t("file_picker_title"),
                filetypes=[(t("filetype_ebooks"), " ".join(f"*{e}" for e in EXTENSOES)), (t("filetype_all"), "*.*")])
            raiz.destroy()
            if caminhos:
                return list(caminhos)
            ui.alerta(t("no_file_chosen"))
            ui.dica(t("files_hint_picker"))
        else:
            ui.dica(t("type_or_drag"))
        ui.dica(t("files_hint_language", key=TECLA_IDIOMA))
        caminhos = []
        while True:
            linha = ui.perguntar(t("prompt_file")).strip()
            if raiz and linha.lower() == TECLA_SELETOR:
                break  # abre o seletor de novo
            if linha.lower() == TECLA_IDIOMA and not caminhos:
                escolher_idioma_interface(config)
                break  # volta à escolha dos arquivos, já no idioma novo
            if not linha:
                if caminhos:
                    return caminhos
                if raiz:
                    break  # nada digitado ainda: Enter abre o seletor de novo
                continue
            caminhos.extend(caminhos_digitados(linha))


def caminhos_digitados(linha):
    """Caminhos como o terminal os cola ao arrastar arquivos: entre aspas ("C:\\Meus livros\\a.epub"
    no Windows, '/Users/eu/a b.epub' no macOS) ou com espaços escapados (/home/eu/a\\ b.epub no Linux)."""
    if os.path.exists(os.path.expanduser(linha.strip("\"'"))):
        return [os.path.expanduser(linha.strip("\"'"))]
    try:
        partes = shlex.split(linha, posix=os.name != "nt")
    except ValueError:
        partes = [linha]
    return [os.path.expanduser(p.strip("\"'")) for p in partes]


def _pasta_documentos():
    casa = os.path.expanduser("~")
    if sys.platform.startswith("linux"):
        try:  # pasta Documentos do usuário, no idioma do sistema (XDG)
            r = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=3)
            pasta = r.stdout.strip()
            if pasta and pasta != casa and os.path.isdir(pasta):
                return pasta
        except Exception:
            pass
    for nome in ("Documents", "Documentos"):
        if os.path.isdir(os.path.join(casa, nome)):
            return os.path.join(casa, nome)
    return casa


def pasta_padrao():
    return os.path.join(_pasta_documentos(), "Audiobook Generator")


def escolher_pasta(sugerida=None):
    """Seletor de pastas; se ele for fechado sem escolher nada: o abre de novo, Enter usa a pasta padrão,
    ou dá para digitar/arrastar uma pasta."""
    padrao = sugerida or pasta_padrao()
    while True:
        raiz, dialogo = _tk()
        if not raiz:
            pasta = ui.perguntar(t("prompt_folder"), padrao)
            return os.path.normpath((caminhos_digitados(pasta) or [padrao])[0])
        ui.dica(t("opening_folder_picker"))
        os.makedirs(padrao, exist_ok=True)
        pasta = dialogo.askdirectory(parent=raiz, title=t("folder_picker_title"), initialdir=padrao, mustexist=False)
        raiz.destroy()
        if pasta:
            return os.path.normpath(pasta)
        ui.alerta(t("no_folder_picked"))
        ui.dica(t("folder_hint_picker"))
        resposta = ui.perguntar(t("prompt_folder"), padrao).strip()
        if resposta.lower() == TECLA_SELETOR:
            continue  # abre o seletor de novo
        return os.path.normpath((caminhos_digitados(resposta) or [padrao])[0])
