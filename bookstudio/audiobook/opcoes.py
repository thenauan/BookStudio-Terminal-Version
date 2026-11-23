"""audiobook.opcoes: parte do BookStudio (separado de audiobook_cli.py)."""
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



TOTAL_ETAPAS = 6


# (formato do motor, chave do nome, chave da descrição); o nome sem chave de tradução é o próprio formato
FORMATOS = [("m4b", "fmt_m4b_name", "fmt_m4b_desc"), ("single", "fmt_mp3_name", "fmt_mp3_desc"),
            ("multiple", "fmt_multi_name", "fmt_multi_desc"), ("opus", "Opus", "fmt_opus_desc"),
            ("flac", "FLAC", "fmt_flac_desc"), ("wav", "WAV", "fmt_wav_desc")]


FORMATOS_SEM_FFMPEG = ("single", "multiple")  # sem FFmpeg, o MP3 sai juntando a voz como veio


# --format na linha de comando -> formato do motor
FORMATOS_CLI = {"m4b": "m4b", "mp3": "single", "mp3-chapters": "multiple", "opus": "opus", "flac": "flac", "wav": "wav"}


# "voice": do tamanho da voz de origem (metade de "high", mesmo som); as outras sobem a taxa
QUALIDADES = ("voice", "standard", "high", "max")


# Texto sincronizado: o que gerar (frases, palavras ou as duas) e onde guardar
SEPARACOES = [("both", "sync_both", "sync_both_desc"), ("sentences", "sync_sentences", "sync_sentences_desc"),
              ("words", "sync_words", "sync_words_desc")]


DESTINOS_TEXTO = [("both", "where_both", "where_both_desc"), ("metadata", "where_metadata", "where_metadata_desc"),
                  ("file", "where_file", "where_file_desc")]


# Formatos com lugar padrão para o texto dentro do arquivo. No WAV não existe (só um bloco ID3 que poucos leem)
# e no MP3 por capítulo o livro não é um arquivo só: nesses, o texto vai sempre em arquivo separado
FORMATOS_COM_TEXTO = ("m4b", "single", "opus", "flac")


def nome_formato(formato):
    return t(next(nome for f, nome, _ in FORMATOS if f == formato))
