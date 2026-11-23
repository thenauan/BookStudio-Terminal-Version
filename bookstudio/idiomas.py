"""idiomas: parte do BookStudio (separado de audiobook_cli.py)."""
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


# Idiomas para os quais dá para traduzir (os da interface, todos com vozes; o motor traduz para o idioma da voz)
DESTINOS_TRADUCAO = i18n.IDIOMAS


# Voz sugerida por idioma (a primeira da lista quando o idioma não está aqui)
VOZ_PREFERIDA = {"pt": "pt-BR-AntonioNeural", "en": "en-US-AndrewNeural", "es": "es-ES-AlvaroNeural",
                 "ar": "ar-SA-HamedNeural", "ru": "ru-RU-SvetlanaNeural", "fr": "fr-FR-HenriNeural",
                 "de": "de-DE-ConradNeural", "it": "it-IT-DiegoNeural", "tr": "tr-TR-AhmetNeural",
                 "hi": "hi-IN-MadhurNeural", "zh": "zh-CN-YunxiNeural", "ur": "ur-PK-AsadNeural"}


LOCALE_PREFERIDO = {"pt": "pt-BR", "en": "en-US", "es": "es-ES", "ar": "ar-SA", "zh": "zh-CN", "fr": "fr-FR",
                    "it": "it-IT", "ru": "ru-RU", "tr": "tr-TR", "hi": "hi-IN", "ur": "ur-PK", "de": "de-DE"}


FRASE_AMOSTRA = {
    "pt": "Olá! Esta é a minha voz. Que tal ouvirmos juntos uma boa história?",
    "en": "Hello! This is my voice. Shall we listen to a good story together?",
    "es": "¡Hola! Esta es mi voz. ¿Escuchamos juntos una buena historia?",
    "ar": "مرحبًا! هذا هو صوتي. هل نستمع معًا إلى قصة جميلة؟",
    "ru": "Привет! Это мой голос. Давайте послушаем хорошую историю вместе.",
    "fr": "Bonjour ! Voici ma voix. Et si nous écoutions ensemble une belle histoire ?",
    "it": "Ciao! Questa è la mia voce. Ascoltiamo insieme una bella storia?",
    "tr": "Merhaba! Bu benim sesim. Birlikte güzel bir hikâye dinleyelim mi?",
    "hi": "नमस्ते! यह मेरी आवाज़ है। क्या हम साथ में एक अच्छी कहानी सुनें?",
    "zh": "你好！这是我的声音。我们一起听一个好故事吧？",
    "ur": "السلام علیکم! یہ میری آواز ہے۔ کیا ہم مل کر ایک اچھی کہانی سنیں؟",
    "de": "Hallo! Das ist meine Stimme. Wollen wir zusammen eine gute Geschichte hören?",
}


with open(os.path.join(BASE, "dados", "vozes.json"), encoding="utf-8") as f:
    VOZES = json.load(f)  # [{c: ShortName, l: Locale, g: F|M}]


with open(os.path.join(BASE, "dados", "idiomas.json"), encoding="utf-8") as f:
    _DADOS_IDIOMAS = json.load(f)


IDIOMAS = _DADOS_IDIOMAS["idiomas"]   # código -> {nativo, en, pt, es...}


REGIOES = _DADOS_IDIOMAS["regioes"]   # BR -> {en: Brazil, pt: Brasil...}


# ---------------------------------------------------------------------------------------------
# Idiomas e vozes
# ---------------------------------------------------------------------------------------------
def _sem_acento(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c)).lower().strip()


_POR_NOME = {}


for _cod, _n in IDIOMAS.items():
    for _nome in _n.values():
        _POR_NOME.setdefault(_sem_acento(_nome), _cod)


def codigo_idioma(nome):
    """'Português (Brasil)', 'English', 'Grego', 'el', 'sv-SE' -> código de duas letras ('' se não reconhecer)."""
    n = _sem_acento(re.sub(r"\s*\(.*\)$", "", str(nome or "")))
    if not n:
        return ""
    m = re.fullmatch(r"([a-z]{2,3})(?:[-_][a-z0-9]+)*", n)
    if m and m.group(1) in IDIOMAS:
        return m.group(1)
    return _POR_NOME.get(n, "")


def nome_idioma(codigo):
    """Nome do idioma na língua da interface ('Portuguese', 'Português', 'Португальский'...)."""
    n = IDIOMAS.get(codigo, {})
    return n.get(i18n.atual) or n.get("nativo") or codigo or "?"


def nome_idioma_frase(codigo):
    """Nome do idioma no meio de uma frase: minúsculo nas línguas em que é assim ('narrar em português')."""
    nome = nome_idioma(codigo)
    return nome.lower() if i18n.atual in ("pt", "es", "fr", "it", "ru") else nome


def nome_regiao(regiao):
    n = REGIOES.get(regiao)
    return (n.get(i18n.atual) or n.get("en") or regiao) if isinstance(n, dict) else (n or regiao)


def nome_voz(v):
    nome = v["c"][len(v["l"]) + 1:].replace("Neural", "").replace("Multilingual", f" ({t('multilingual')})").strip()
    regiao = next((p for p in v["l"].split("-") if re.fullmatch(r"[A-Z]{2}", p)), "")
    genero = t("female") if v["g"] == "F" else t("male")
    onde = nome_regiao(regiao) if regiao else ""
    return nome, f"{genero} · {nome_idioma(v['l'].split('-')[0])}" + (f" ({onde})" if onde else "")


def vozes_do_idioma(codigo):
    lista = [v for v in VOZES if v["l"].split("-")[0] == codigo]
    pref = LOCALE_PREFERIDO.get(codigo)
    return sorted(lista, key=lambda v: (v["l"] != pref, v["l"]))
