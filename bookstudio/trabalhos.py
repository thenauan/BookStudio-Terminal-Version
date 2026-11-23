"""Trabalhos pausados (audiobooks e textos sincronizados), para continuar de onde pararam.

Ficam num arquivo ao lado da configuração. O que já foi narrado fica no cache do motor, então retomar não
narra de novo. As traduções têm o próprio registro (o cache de traduções do módulo de e-books).
"""
import json
import os
import time
import uuid

from bookstudio.config import caminho_config


def _arquivo():
    return os.path.join(os.path.dirname(caminho_config()), "trabalhos.json")


def _ler():
    try:
        with open(_arquivo(), encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, list) else []
    except (OSError, ValueError):
        return []


def _gravar(lista):
    try:
        os.makedirs(os.path.dirname(_arquivo()), exist_ok=True)
        tmp = _arquivo() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _arquivo())
    except OSError:
        pass


def pausar(tipo, titulo, dados, ident=None):
    """Guarda um trabalho pausado. Devolve o identificador (o mesmo, se ele já estava guardado)."""
    lista = [j for j in _ler() if j.get("id") != ident]
    ident = ident or uuid.uuid4().hex[:10]
    lista.append({"id": ident, "tipo": tipo, "titulo": titulo, "quando": time.time(), "dados": dados})
    _gravar(lista)
    return ident


def pendentes():
    return sorted(_ler(), key=lambda j: j.get("quando", 0), reverse=True)


def remover(ident):
    _gravar([j for j in _ler() if j.get("id") != ident])
