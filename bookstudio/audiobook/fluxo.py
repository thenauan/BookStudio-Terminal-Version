"""Criar audiobook: livros -> informações, voz e texto sincronizado -> formato -> pasta -> resumo -> conversão.

Usado pelo menu (🎧 Criar audiobook), pela linha de comando (bookstudio livro.epub) e pelos fluxos encadeados
(traduzir e criar audiobook: o livro chega já traduzido, sem a etapa de tradução do motor).
"""
import os
import re
import sys
import time

from bookstudio import i18n, trabalhos, ui
from bookstudio.arquivos import escolher_arquivos, escolher_pasta, pasta_padrao, EXTENSOES
from bookstudio.audiobook.escolhas import (configurar_livros, decidir_modo_leve, escolher_destino_texto, escolher_formato)
from bookstudio.audiobook.livros import conferir_metadados, ler_livros
from bookstudio.audiobook.motor import converter, escolher_apos_falha, salvar_detalhes_falhas
from bookstudio.audiobook.opcoes import (DESTINOS_TEXTO, FORMATOS, FORMATOS_CLI, FORMATOS_COM_TEXTO, FORMATOS_SEM_FFMPEG,
                                         QUALIDADES, SEPARACOES, TOTAL_ETAPAS, nome_formato)
from bookstudio.config import salvar_config
from bookstudio.i18n import t
from bookstudio.idiomas import VOZES, nome_idioma_frase, nome_voz
from bookstudio.sistema import abrir, janela_em_primeiro_plano, verificar_dependencias


def _validos(caminhos):
    validos = []
    for c in caminhos:
        if not os.path.isfile(c):
            ui.falha(t("file_not_found", path=c))
        elif not c.lower().endswith(EXTENSOES):
            ui.falha(t("unsupported_format", name=os.path.basename(c)))
        else:
            validos.append(c)
    return validos


def criar_audiobook(args, config, caminhos=None, idioma=None, sem_traducao=False, retomar=None):
    """Devolve 0 (todos prontos), 1 (nada para converter), 2 (algum falhou ou foi pausado/cancelado).
    `idioma`: idioma já conhecido dos livros (livro traduzido pelo BookStudio); `sem_traducao`: não oferece traduzir.
    `retomar`: trabalho pausado ({"livros", "formato", ...}) a continuar de onde parou."""
    sem_perguntas = args.yes
    tem_ffmpeg = verificar_dependencias()
    modo_leve = decidir_modo_leve(args.leve, config)
    if args.leve is not None:
        salvar_config(config)  # --lite / --no-lite vale também para as próximas vezes

    if retomar:
        return _converter_e_relatar(args, config, retomar["livros"], retomar["formato"], retomar["pasta"],
                                    retomar["qualidade"], modo_leve, retomar["destino_texto"], retomar.get("id"))

    # 1) Livros
    ui.etapa(1, TOTAL_ETAPAS, t("step_books"), "livros")
    caminhos = list(caminhos or args.arquivos or []) or escolher_arquivos(config)
    while True:
        validos = _validos(caminhos)
        if validos:
            break
        ui.falha(t("no_ebooks"))
        if args.arquivos or sem_perguntas or not sys.stdin.isatty():
            return 1
        caminhos = escolher_arquivos(config)  # nada válido escolhido: escolhe de novo
    livros = ler_livros(validos)
    if not livros:
        ui.falha(t("none_readable"))
        return 1
    if idioma:
        for l in livros:
            l["idioma"] = idioma

    # 2) Informações, tradução, texto sincronizado e voz de cada livro
    ui.etapa(2, TOTAL_ETAPAS, t("step_setup"), "voz")
    configurar_livros(livros, {"sem_perguntas": sem_perguntas, "voz": args.voice,
                               "traduzir": None if sem_traducao else args.translate, "sem_traducao": sem_traducao,
                               "texto": args.texto, "separacao": args.sync_detail, "config": config})

    # 3) Formato (sem FFmpeg, só MP3)
    ui.etapa(3, TOTAL_ETAPAS, t("step_format"), "formato")
    sugerido = config.get("formato") if config.get("formato") in (f[0] for f in FORMATOS) else "m4b"
    if not tem_ffmpeg and sugerido not in FORMATOS_SEM_FFMPEG:
        sugerido = "single"
    if args.format:
        formato = FORMATOS_CLI[args.format]
        if not tem_ffmpeg and formato not in FORMATOS_SEM_FFMPEG:
            ui.alerta(t("format_needs_ffmpeg", format=nome_formato(formato)))
            formato = "single"
    elif sem_perguntas:
        formato = sugerido
    else:
        formato = escolher_formato(tem_ffmpeg, sugerido)
    qualidade = args.quality or (config.get("qualidade") if config.get("qualidade") in QUALIDADES else "voice")
    if args.format or sem_perguntas:
        ui.ok(nome_formato(formato))

    # Onde guardar o texto sincronizado (depende do formato: nem todo arquivo tem lugar para ele)
    destino_texto = "both"
    if any(l["texto"] for l in livros):
        salvo = config.get("destino_texto") if config.get("destino_texto") in (d[0] for d in DESTINOS_TEXTO) else "both"
        if formato not in FORMATOS_COM_TEXTO:
            destino_texto = "file"
            ui.dica(t("sync_file_only", format=nome_formato(formato)))
        elif args.sync_save:
            destino_texto = args.sync_save
        elif sem_perguntas:
            destino_texto = salvo
        else:
            destino_texto = escolher_destino_texto(salvo)

    # 4) Pasta
    ui.etapa(4, TOTAL_ETAPAS, t("step_folder"), "pasta")
    pasta_salva = config.get("pasta") if config.get("pasta") and os.path.isdir(config["pasta"]) else None
    if args.saida:
        pasta = os.path.normpath(os.path.expanduser(args.saida))
    elif sem_perguntas:
        pasta = pasta_salva or pasta_padrao()
    else:
        pasta = escolher_pasta(pasta_salva)
    os.makedirs(pasta, exist_ok=True)
    ui.ok(t("saving_to", folder=pasta))

    # 5) Resumo
    ui.etapa(5, TOTAL_ETAPAS, t("step_summary"), "converter")
    for l in livros:
        voz = nome_voz(next(v for v in VOZES if v["c"] == l["voz"]))
        detalhes = [t("summary_voice", name=voz[0], details=voz[1]),
                    t("translate_to_lower", language=nome_idioma_frase(l["traduzir"])) if l["traduzir"] else t("without_translation"),
                    t("with_sync") + f" ({t(next(n for c, n, _ in SEPARACOES if c == l['separacao'])).lower()}; "
                    f"{t(next(n for c, n, _ in DESTINOS_TEXTO if c == destino_texto)).lower()})" if l["texto"] else t("without_sync")]
        ui.cartao(l["titulo"], [(t("field_author"), l["autor"]), ("", " · ".join(detalhes))])
    ui.dica(t("summary_format_folder", format=nome_formato(formato), folder=pasta))
    if not sem_perguntas and not ui.sim_nao(t("start_q"), padrao=True):
        ui.dica(t("not_started"))
        return 0

    # As escolhas viram o padrão da próxima vez (voz por idioma de narração)
    vozes_salvas = dict(config.get("vozes") or {})
    for l in livros:
        vozes_salvas[l["voz"].split("-")[0]] = l["voz"]
    config.update({"vozes": vozes_salvas, "formato": formato, "qualidade": qualidade, "pasta": pasta,
                   "texto": livros[-1]["texto"], "separacao": livros[-1]["separacao"]})
    if any(l["texto"] for l in livros) and formato in FORMATOS_COM_TEXTO:
        config["destino_texto"] = destino_texto
    salvar_config(config)
    return _converter_e_relatar(args, config, livros, formato, pasta, qualidade, modo_leve, destino_texto)


def _sem_capa(livro):
    """Livro sem a capa (que pode ter megabytes) para guardar no trabalho pausado; ela é lida de novo ao retomar."""
    return {k: v for k, v in livro.items() if k != "capa"}


def _converter_e_relatar(args, config, livros, formato, pasta, qualidade, modo_leve, destino_texto, id_pausado=None):
    sem_perguntas = args.yes
    if any("capa" not in l for l in livros):  # trabalho retomado: a capa volta do e-book
        capas = {l["caminho"]: l.get("capa") for l in ler_livros([l["caminho"] for l in livros if os.path.isfile(l["caminho"])])}
        for l in livros:
            l.setdefault("capa", capas.get(l["caminho"]))

    # 6) Conversão
    ui.etapa(6, TOTAL_ETAPAS, t("step_converting"), "converter")
    ui.dica(t("ctrl_c_hint_pause"))
    if not modo_leve and janela_em_primeiro_plano() is not None:
        ui.dica(t("foreground_hint"))
    print()
    inicio = time.time()
    concluidos, pendentes = [], livros
    while True:
        resultados, interrompido = converter(pendentes, formato, pasta, qualidade, modo_leve, destino_texto)
        print()
        falhas = []
        for l in pendentes:
            r = resultados[l["id"]]
            if r["ok"]:
                concluidos.append(l)
                audio = next((a for a in r["arquivos"] if not re.search(r"\.(lrc|srt|vtt)$|\.sync\.json$", a, re.I)), None)
                ui.ok(f"{ui.negrito(l['titulo'])}  {ui.suave(audio or pasta)}")
                if audio:
                    conferir_metadados(audio, l, formato, destino_texto)
                if r.get("erro_texto"):
                    ui.alerta(t("sync_failed", r["erro_texto"]))
            else:
                falhas.append(l)
                if not interrompido:
                    ui.falha(f"{l['titulo']}: {r['erro']}")
        if interrompido == "pause":
            id_pausado = trabalhos.pausar("audiobook", ", ".join(l["titulo"] for l in falhas)[:80], {
                "livros": [_sem_capa(l) for l in falhas], "formato": formato, "pasta": pasta, "qualidade": qualidade,
                "destino_texto": destino_texto}, id_pausado)
            ui.painel([t("paused_saved"), t("paused_resume_hint")], titulo=t("paused"), cor="yellow")
            return 2
        if interrompido == "cancel":
            if id_pausado:
                trabalhos.remover(id_pausado)
            ui.alerta(t("conversion_cancelled"))
            return 2
        registro = salvar_detalhes_falhas([(l["titulo"], resultados[l["id"]]["erro"], resultados[l["id"]]["detalhes"]) for l in falhas])
        if registro:
            ui.dica(t("error_details_saved", path=registro))
        if not falhas or sem_perguntas or not sys.stdin.isatty():
            break
        if not escolher_apos_falha(falhas, resultados, config):
            break
        pendentes = falhas
        print()
    if id_pausado:
        trabalhos.remover(id_pausado)
    minutos = (time.time() - inicio) / 60
    if concluidos and formato == "wav":
        ui.dica(t("wav_limits"))
    print()
    if concluidos:
        ui.painel([t("n_ready", done=len(concluidos), total=len(livros), minutes=f"{minutos:.1f}"), pasta],
                  titulo=f"{ui.ICONE['ok']} {t('done')}", cor="green")
        if not sem_perguntas and sys.stdin.isatty() and ui.sim_nao(t("open_folder_q"), padrao=True):
            abrir(pasta)
    else:
        ui.falha(t("none_created"))
    return 0 if len(concluidos) == len(livros) else 2
