"""audiobook.livros: parte do BookStudio (separado de audiobook_cli.py)."""
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
from bookstudio.audiobook.opcoes import FORMATOS_COM_TEXTO
from bookstudio.idiomas import codigo_idioma, nome_idioma


# ---------------------------------------------------------------------------------------------
# Leitura dos livros
# ---------------------------------------------------------------------------------------------
def id_do_arquivo(caminho):
    """Nome da pasta de trabalho do livro pelo arquivo de origem (caminho e tamanho), e não pelo título:
    editar o título não perde o que já foi narrado."""
    try:
        tamanho = os.path.getsize(caminho)
    except OSError:
        tamanho = 0
    chave = f"{os.path.normcase(os.path.abspath(caminho))}|{tamanho}"
    return "livro_" + hashlib.sha1(chave.encode("utf-8")).hexdigest()[:16]


def ler_livros(caminhos):
    from extract_metadata import extract_metadata
    livros, prontos = [], [0]
    trava = threading.Lock()

    def ler(caminho):
        try:
            dados = extract_metadata(caminho, os.path.basename(caminho))
        except Exception as e:
            dados = {"unreadable": t("cannot_read_file", error=e)}
        with trava:
            prontos[0] += 1
            sys.stdout.write(f"\r\033[2K  {ui.destaque(ui.ICONE['info'])} " + t("reading_books", done=prontos[0], total=len(caminhos)))
            sys.stdout.flush()
        return caminho, dados

    with ThreadPoolExecutor(max_workers=min(4, len(caminhos))) as ex:
        resultados = list(ex.map(ler, caminhos))
    sys.stdout.write("\r\033[2K")
    for caminho, d in resultados:
        nome = os.path.basename(caminho)
        if d.get("unreadable"):
            ui.falha(f"{nome}: {i18n.do_motor(d['unreadable'])}")
            continue
        texto_inicio = ""
        for cap in d.get("chapters") or []:
            conteudo = (cap.get("content") or "").strip()
            if len(conteudo) > 200:
                texto_inicio = conteudo
                break
        autor = d.get("author") or ""
        livros.append({
            "id": uuid.uuid4().hex[:10], "caminho": caminho, "arquivo": nome, "cache_id": id_do_arquivo(caminho),
            "titulo": d.get("title") or os.path.splitext(nome)[0],
            "autor": t("unknown_author") if autor in ("", "Desconhecido") else autor,
            "genero": d.get("genre") or "", "subgenero": d.get("subgenre") or "", "ano": d.get("year") or "",
            "idioma": codigo_idioma(d.get("language")), "capitulos": len(d.get("chapters") or []),
            "duracao": d.get("durationEst") or "", "capa": d.get("cover"), "trecho": texto_inicio,
        })
        ui.ok(f"{nome} {ui.suave('· ' + t('n_chapters', n=len(d.get('chapters') or [])))}")
    return livros


# ---------------------------------------------------------------------------------------------
# Escolhas de cada livro
# ---------------------------------------------------------------------------------------------
def revisar_informacoes(livro):
    ui.cartao(livro["titulo"], [
        (t("field_author"), livro["autor"]), (t("field_genre"), livro["genero"]), (t("field_subgenre"), livro["subgenero"]),
        (t("field_year"), livro["ano"]),
        (t("field_language"), nome_idioma(livro["idioma"]) if livro["idioma"] else t("not_recognized")),
        (t("field_chapters"), str(livro["capitulos"])), (t("field_duration"), livro["duracao"]), (t("field_file"), livro["arquivo"]),
    ])
    if not ui.sim_nao(t("edit_details"), padrao=False):
        return
    livro["titulo"] = ui.perguntar(t("field_title"), livro["titulo"])
    livro["autor"] = ui.perguntar(t("field_author"), livro["autor"])
    livro["genero"] = ", ".join(g.strip() for g in ui.perguntar(t("prompt_genres"), livro["genero"]).split(",") if g.strip())
    livro["subgenero"] = ", ".join(g.strip() for g in ui.perguntar(t("prompt_subgenres"), livro["subgenero"]).split(",") if g.strip())
    while True:
        ano = ui.perguntar(t("field_year"), livro["ano"])
        if not ano or re.fullmatch(r"\d{4}", ano):
            livro["ano"] = ano
            break
        ui.alerta(t("year_invalid"))
    while True:
        idioma = ui.perguntar(t("prompt_book_language"), nome_idioma(livro["idioma"]) if livro["idioma"] else "")
        codigo = codigo_idioma(idioma)
        if codigo or not idioma:
            livro["idioma"] = codigo
            break
        ui.alerta(t("language_invalid"))
    ui.ok(t("details_updated"))


def metadados_gravados(caminho):
    """O que está de fato gravado no arquivo final, lido de volta dele."""
    import mutagen
    audio = mutagen.File(caminho)
    tags = audio.tags if audio is not None and audio.tags else {}
    chaves = set(tags.keys())
    ext = os.path.splitext(caminho)[1].lower()
    if ext in (".mp3", ".wav"):
        def tem(prefixo):
            return any(k.startswith(prefixo) for k in chaves)
        return {"title": tem("TIT2"), "author": tem("TPE1"), "genre": tem("TCON"), "year": tem("TDRC"), "cover": tem("APIC"),
                "chapters": tem("CHAP"), "text": tem("USLT") or tem("SYLT"), "narrator": tem("TCOM")}
    if ext in (".m4b", ".m4a"):
        return {"title": "\xa9nam" in chaves, "author": "\xa9ART" in chaves, "genre": "\xa9gen" in chaves, "year": "\xa9day" in chaves,
                "cover": "covr" in chaves, "chapters": bool(getattr(audio, "chapters", None)), "text": "\xa9lyr" in chaves,
                "narrator": "\xa9wrt" in chaves}
    chaves = {k.lower() for k in chaves}  # FLAC e Opus
    return {"title": "title" in chaves, "author": "artist" in chaves, "genre": "genre" in chaves, "year": "date" in chaves,
            "cover": bool(getattr(audio, "pictures", None)) or "metadata_block_picture" in chaves,
            "chapters": "chapter001" in chaves, "text": "lyrics" in chaves, "narrator": "narrator" in chaves}


def conferir_metadados(arquivo, livro, formato, destino_texto):
    """Lê o arquivo pronto e mostra o que entrou nele; se faltar algo que deveria estar lá, avisa."""
    try:
        gravado = metadados_gravados(arquivo)
    except Exception:
        return
    esperado = ["title", "author", "narrator", "cover"]
    esperado += ["genre"] if livro["genero"] else []
    esperado += ["year"] if livro["ano"] else []
    esperado += ["chapters"] if formato != "multiple" and livro["capitulos"] > 1 else []
    if livro["texto"] and formato in FORMATOS_COM_TEXTO and destino_texto != "file":
        esperado.append("text")
    ok = [t("meta_" + c) for c in esperado if gravado.get(c)]
    faltando = [t("meta_" + c) for c in esperado if not gravado.get(c)]
    if ok:
        ui.dica("    " + t("meta_saved", items=", ".join(ok)))
    if faltando:
        ui.alerta(t("meta_missing", items=", ".join(faltando)))
