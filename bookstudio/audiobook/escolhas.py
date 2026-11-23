"""audiobook.escolhas: parte do BookStudio (separado de audiobook_cli.py)."""
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
from bookstudio.audiobook.livros import revisar_informacoes
from bookstudio.audiobook.opcoes import DESTINOS_TEXTO, FORMATOS, FORMATOS_SEM_FFMPEG, SEPARACOES
from bookstudio.idiomas import DESTINOS_TRADUCAO, FRASE_AMOSTRA, IDIOMAS, VOZES, VOZ_PREFERIDA, _sem_acento, nome_idioma, nome_idioma_frase, nome_voz, vozes_do_idioma
from bookstudio.sistema import abrir, computador_mais_fraco, memoria_gb, na_bateria


def avisar_modo_leve(motivo):
    """Avisa que o modo leve está ligado, por quê, e o que muda em relação ao modo adaptável."""
    print()
    ui.alerta(motivo)
    ui.info(t("lite_what_changes"))
    for chave in ("lite_change_books", "lite_change_voice", "lite_change_cpu", "lite_change_result"):
        print("    " + ui.destaque(ui.ICONE["item"]) + " " + t(chave))
    ui.dica(t("lite_how_off"))


def decidir_modo_leve(pedido, config):
    """--lite / --no-lite (guardado para as próximas vezes); senão a escolha guardada; senão pelo computador:
    o modo adaptável é o padrão, e o leve só liga sozinho num computador mais fraco. Devolve se o leve fica ligado."""
    if pedido is not None:
        config["leve"] = pedido
    if isinstance(config.get("leve"), bool):
        if config["leve"]:
            avisar_modo_leve(t("lite_on_chosen"))
        return config["leve"]
    nucleos, ram, bateria = os.cpu_count() or 2, memoria_gb(), na_bateria()
    if not computador_mais_fraco(nucleos, ram, bateria):
        return False
    avisar_modo_leve(t("lite_on_auto", cores=nucleos, ram=f"{ram:.0f}" if ram is not None else "?",
                       battery=t("battery_suffix") if bateria else ""))
    return True


# ---------------------------------------------------------------------------------------------
# Amostra de voz (gerada na hora, com um trecho do próprio livro quando dá)
# ---------------------------------------------------------------------------------------------
def tocar_amostra(voz, idioma, trecho=""):
    import edge_tts
    if trecho:
        frase = re.split(r"(?<=[.!?…。！？])\s*", re.sub(r"\s+", " ", trecho))[:2]
        frase = " ".join(frase)[:260]
    else:
        frase = FRASE_AMOSTRA.get(idioma, "1, 2, 3.")
    destino = os.path.join(tempfile.gettempdir(), f"amostra_{voz}.mp3")
    ui.dica(f"{ui.ICONE['amostra']} " + t("generating_sample", voice=voz))
    try:
        asyncio.run(edge_tts.Communicate(frase, voz).save(destino))
        abrir(destino)
    except Exception as e:
        ui.alerta(t("sample_failed", error=type(e).__name__))


def escolher_traducao(livro):
    if not livro["idioma"]:
        return ""
    destinos = [c for c in DESTINOS_TRADUCAO if c != livro["idioma"]]
    opcoes = [(t("no_translation"), t("narrate_in", language=nome_idioma_frase(livro["idioma"])))] + \
             [(t("translate_to", language=nome_idioma_frase(c)), IDIOMAS[c]["nativo"]) for c in destinos]
    print()
    ui.info(f"{ui.ICONE['traduzir']} " + t("translation"))
    i = ui.escolher(t("prompt_option"), opcoes, padrao=0, colunas=1 if len(opcoes) <= 6 else 2)
    return "" if i == 0 else destinos[i - 1]


def escolher_idioma_narracao():
    codigos = sorted(IDIOMAS, key=lambda c: _sem_acento(nome_idioma(c)))
    ui.alerta(t("language_unknown_choose"))
    padrao = codigos.index(i18n.atual) if i18n.atual in codigos else 0
    i = ui.escolher(t("prompt_language"), [(nome_idioma(c), "") for c in codigos], padrao=padrao, colunas=3)
    return codigos[i]


def voz_padrao(idioma, config):
    """A última voz usada neste idioma; senão a sugerida do idioma; senão a primeira."""
    codigos = [v["c"] for v in vozes_do_idioma(idioma)]
    for voz in ((config.get("vozes") or {}).get(idioma), VOZ_PREFERIDA.get(idioma)):
        if voz in codigos:
            return voz
    return codigos[0] if codigos else None


def voz_automatica(idioma, voz_pedida, config):
    """Voz sem perguntar (--voice / --yes): a pedida, se narra este idioma; senão a padrão do idioma."""
    if not vozes_do_idioma(idioma):
        idioma = i18n.atual if vozes_do_idioma(i18n.atual) else "en"
    padrao = voz_padrao(idioma, config)
    if voz_pedida and voz_pedida not in (v["c"] for v in vozes_do_idioma(idioma)):
        ui.alerta(t("voice_ignored", voice=voz_pedida, language=nome_idioma_frase(idioma), default=padrao))
        return padrao
    return voz_pedida or padrao


def escolher_voz(idioma, trecho, sugerida=None):
    todas = vozes_do_idioma(idioma)
    if not todas:
        ui.alerta(t("no_voices", language=nome_idioma_frase(idioma)))
        return escolher_voz(escolher_idioma_narracao(), "", None)
    if sugerida not in (v["c"] for v in todas):
        sugerida = VOZ_PREFERIDA.get(idioma) if VOZ_PREFERIDA.get(idioma) in (v["c"] for v in todas) else todas[0]["c"]
    genero, regiao = None, None  # filtros: f/m (gênero), r XX (região), t (todas)
    while True:
        vozes = [v for v in todas if (not genero or v["g"] == genero) and (not regiao or regiao in v["l"].split("-"))]
        if not vozes:
            ui.alerta(t("no_voices_filter"))
            genero, regiao = None, None
            continue
        codigos = [v["c"] for v in vozes]
        padrao = codigos.index(sugerida) if sugerida in codigos else 0
        print()
        ui.info(f"{ui.ICONE['voz']} " + t("voice_heading", language=nome_idioma_frase(idioma)) + " " + ui.suave(t("n_voices", n=len(vozes))))
        extras = {"o": t("listen_hint")}
        if len(todas) > 6:  # filtros só quando há vozes que valha filtrar
            extras.update({"f": t("filter_female"), "m": t("filter_male"), "r": t("filter_region"), "t": t("filter_all")})
        r = ui.escolher(t("prompt_voice"), [nome_voz(v) for v in vozes], padrao=padrao, extras=extras,
                        colunas=1 if len(vozes) <= 12 else 2)
        if not isinstance(r, tuple):
            return codigos[r]
        tecla, argumento = r[0], r[1].strip()
        if tecla == "o":
            n = argumento or str(padrao + 1)
            if n.isdigit() and 1 <= int(n) <= len(vozes):
                sugerida = codigos[int(n) - 1]
                tocar_amostra(sugerida, idioma, trecho)
            else:
                ui.alerta(t("listen_usage", n=padrao + 1))
        elif tecla in ("f", "m"):
            genero = tecla.upper()
        elif tecla == "r":
            regiao = argumento.upper() or None
        elif tecla == "t":
            genero, regiao = None, None


def escolher_separacao(sugerida):
    padrao = next((i for i, s in enumerate(SEPARACOES) if s[0] == sugerida), 0)
    print()
    ui.info(f"{ui.ICONE['texto']} " + t("sync_detail_q"))
    return SEPARACOES[ui.escolher(t("prompt_option"), [(t(n), t(d)) for _, n, d in SEPARACOES], padrao=padrao)][0]


def escolher_destino_texto(sugerido):
    padrao = next((i for i, d in enumerate(DESTINOS_TEXTO) if d[0] == sugerido), 0)
    print()
    ui.info(f"{ui.ICONE['texto']} " + t("sync_where_q"))
    return DESTINOS_TEXTO[ui.escolher(t("prompt_option"), [(t(n), t(d)) for _, n, d in DESTINOS_TEXTO], padrao=padrao)][0]


def configurar_livros(livros, opcoes):
    """Informações, tradução, texto sincronizado e voz de cada livro. `opcoes`: o que veio da linha de comando
    (sem_perguntas, voz, traduzir, texto, separacao) e as últimas escolhas salvas (config)."""
    config = opcoes["config"]
    salva = config.get("separacao") if config.get("separacao") in (s[0] for s in SEPARACOES) else "both"
    separacao_padrao = opcoes["separacao"] or salva
    voz_pedida = opcoes["voz"]
    idioma_voz_pedida = next((v["l"].split("-")[0] for v in VOZES if v["c"] == voz_pedida), None)
    texto_padrao = config.get("texto", True) if opcoes["texto"] is None else opcoes["texto"]
    escolhas_por_idioma = {}  # idioma -> (traduzir, texto, separacao, voz), quando o usuário quer repetir
    for n, livro in enumerate(livros, 1):
        if opcoes["sem_perguntas"]:
            destino = opcoes["traduzir"]
            livro["traduzir"] = destino if destino and destino != livro["idioma"] else ""
            livro["texto"] = texto_padrao
            livro["separacao"] = separacao_padrao
            idioma_narracao = livro["traduzir"] or livro["idioma"] or idioma_voz_pedida or i18n.atual
            livro["voz"] = voz_automatica(idioma_narracao, voz_pedida, config)
            continue
        print()
        ui.info(ui.negrito(t("book_n_of", n=n, total=len(livros))))
        revisar_informacoes(livro)
        repetir = escolhas_por_idioma.get(livro["idioma"]) if livro["idioma"] else None
        if repetir:
            livro["traduzir"], livro["texto"], livro["separacao"], livro["voz"] = repetir
            ui.ok(t("reusing_choices", language=nome_idioma_frase(livro["idioma"]),
                    translation=t("translate_to_lower", language=nome_idioma_frase(livro["traduzir"])) if livro["traduzir"] else t("without_translation"),
                    sync=t("with_sync") if livro["texto"] else t("without_sync"),
                    voice=nome_voz(next(v for v in VOZES if v["c"] == livro["voz"]))[0]))
            continue
        if opcoes.get("sem_traducao"):  # livro já traduzido pelo BookStudio: narra como está
            livro["traduzir"] = ""
        elif opcoes["traduzir"]:
            livro["traduzir"] = opcoes["traduzir"] if opcoes["traduzir"] != livro["idioma"] else ""
        else:
            livro["traduzir"] = escolher_traducao(livro)
        if opcoes["texto"] is None:
            print()
            livro["texto"] = ui.sim_nao(f"{ui.ICONE['texto']} " + t("ask_sync"), padrao=texto_padrao)
        else:
            livro["texto"] = opcoes["texto"]
        livro["separacao"] = separacao_padrao
        if livro["texto"] and not opcoes["separacao"]:
            livro["separacao"] = escolher_separacao(separacao_padrao)
        idioma_narracao = livro["traduzir"] or livro["idioma"] or idioma_voz_pedida or escolher_idioma_narracao()
        if voz_pedida:
            livro["voz"] = voz_automatica(idioma_narracao, voz_pedida, config)
        else:
            livro["voz"] = escolher_voz(idioma_narracao, "" if livro["traduzir"] else livro["trecho"], voz_padrao(idioma_narracao, config))
        restantes = [l for l in livros[n:] if l["idioma"] == livro["idioma"]]
        if livro["idioma"] and restantes and ui.sim_nao(
                t("reuse_choices_q", n=len(restantes), language=nome_idioma_frase(livro["idioma"])), padrao=True):
            escolhas_por_idioma[livro["idioma"]] = (livro["traduzir"], livro["texto"], livro["separacao"], livro["voz"])


def escolher_formato(tem_ffmpeg, sugerido):
    opcoes = [f for f in FORMATOS if tem_ffmpeg or f[0] in FORMATOS_SEM_FFMPEG]
    padrao = next((i for i, f in enumerate(opcoes) if f[0] == sugerido), 0)
    i = ui.escolher(t("prompt_format"), [(t(nome), t(desc)) for _, nome, desc in opcoes], padrao=padrao)
    return opcoes[i][0]
