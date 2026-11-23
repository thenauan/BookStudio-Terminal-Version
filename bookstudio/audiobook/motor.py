"""audiobook.motor: parte do BookStudio (separado de audiobook_cli.py)."""
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
from bookstudio.audiobook.escolhas import voz_automatica
from bookstudio.config import caminho_config
from bookstudio.idiomas import IDIOMAS, VOZES, nome_voz
from bookstudio.sistema import janela_em_primeiro_plano


# ---------------------------------------------------------------------------------------------
# Conversão (o mesmo motor do app: converter.py, com os eventos de progresso em JSON)
# ---------------------------------------------------------------------------------------------
NOME_ETAPA = {"translating": "engine_translating", "editing": "engine_preparing", "syncing": "engine_sync"}


def converter(livros, formato, pasta, qualidade="voice", leve=False, destino_texto="both"):
    """Modo adaptável (conexões de voz e processador se ajustam sozinhos) ou, em computadores mais fracos,
    modo leve (um livro por vez, 4 conexões, uma etapa de áudio por vez e prioridade mínima)."""
    destino = {"both": "both", "metadata": "metadata", "file": "library"}[destino_texto]  # nomes do motor
    config = {
        "outputFolder": pasta, "file_format": formato, "fileType": formato, "audio_quality": qualidade,
        # Texto sincronizado com as frases e, dentro de cada frase, as palavras (cada uma com seu tempo)
        "sync_text_destination": destino,
        "lite": leve, "adaptive": not leve,
        "files": [{
            "id": l["id"], "path": os.path.abspath(l["caminho"]), "filename": l["arquivo"], "title": l["titulo"],
            "cache_id": l["cache_id"],
            "author": l["autor"], "genre": l["genero"], "subgenre": l["subgenero"], "year": l["ano"],
            # O motor recebe o nome do idioma em português, como no app
            "language": IDIOMAS.get(l["idioma"], {}).get("pt", "") if l["idioma"] else "", "voice": l["voz"],
            "translate": bool(l["traduzir"]), "fileType": formato, "file_format": formato, "cover": l["capa"],
            "generic_cover": True,  # e-book sem capa: o motor gera a capa genérica do app
            "narrator": nome_voz(next(v for v in VOZES if v["c"] == l["voz"]))[0],
            "chapters": [], "generate_sync_text": l["texto"], "sync_text_destination": destino,
            # Frases, palavras ou as duas (cada uma com seu tempo)
            "sync_text_granularity": {"both": "both", "sentences": "sentences", "words": "words"}[l["separacao"]],
        } for l in livros],
    }
    # Janela em primeiro plano: a conversão tem prioridade; minimizada, fica mais leve (só onde dá para saber)
    plano = None if leve else janela_em_primeiro_plano()
    if plano is not None:
        config["foreground"] = plano
    cfg = os.path.join(tempfile.gettempdir(), f"audiobook_cli_{uuid.uuid4().hex[:8]}.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False)

    painel = ui.PainelProgresso([{"id": l["id"], "titulo": l["titulo"]} for l in livros])
    resultados = {l["id"]: {"ok": False, "erro": None, "detalhes": None, "arquivos": []} for l in livros}
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # Grupo de processos próprio (o motor e o FFmpeg que ele abre): o Ctrl+C fica só com este app,
    # que pergunta antes de cancelar, e o cancelamento encerra o grupo inteiro
    if os.name == "nt":
        grupo = {"creationflags": 0x00000200}  # CREATE_NEW_PROCESS_GROUP
    else:
        grupo = {"start_new_session": True}
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(MOTOR, "converter.py"), cfg], cwd=MOTOR,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=env, **grupo)
    erros_motor = []
    threading.Thread(target=lambda: erros_motor.extend(proc.stderr.readlines()), daemon=True).start()

    def ler_eventos():
        ultimo_desenho = 0.0
        for linha in proc.stdout:
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            fid, tipo = ev.get("fileId"), ev.get("type")
            if tipo == "progress":
                # Uma porcentagem só (todas as etapas embutidas); ao lado, a contagem da etapa atual
                st = ev.get("status")
                unidade = i18n.do_motor(ev.get("partsUnit"), ev.get("unitCode"), ev.get("unitArgs")) or t("parts")
                partes = f"{ev['partsDone']}/{ev['partsTotal']} {unidade}" if ev.get("partsTotal") else ""
                etapa = i18n.do_motor((ev.get("message") or "").rstrip(".…"), ev.get("code"), ev.get("args")) \
                    or t(NOME_ETAPA.get(st, "engine_narrating"))
                painel.atualizar(fid, pct=float(ev.get("progress") or 0), etapa=etapa, partes=partes)
            elif tipo == "speed":
                bps = ev.get("bytesPerSec") or 0
                vel = (f"{bps / 1048576:.1f} MB/s" if bps >= 1048576 else f"{bps // 1024} KB/s") if bps else ""
                if vel and ev.get("connections"):  # quantas conexões o ajuste automático está usando agora
                    vel += " · " + t("n_connections", n=ev["connections"])
                painel.atualizar(fid, vel=vel, tamanho=ui.tamanho(ev["bytes"]) if ev.get("bytes") else None)
            elif tipo == "done":
                painel.atualizar(fid, pct=100.0, fim="ok")
                resultados[fid].update(ok=True, arquivos=ev.get("paths") or [], erro_texto=ev.get("syncTextError"))
            elif tipo == "error":
                erro = i18n.do_motor(ev.get("error"), ev.get("code"), ev.get("args")) or t("conversion_failed")
                painel.atualizar(fid, fim="erro", etapa=erro)
                resultados[fid].update(erro=erro, detalhes=ev.get("details"), parte=ev.get("failedPart"))
            if time.time() - ultimo_desenho > 0.2 or tipo in ("done", "error"):
                painel.desenhar()
                ultimo_desenho = time.time()

    leitor = threading.Thread(target=ler_eventos, daemon=True)
    leitor.start()
    painel.desenhar()
    cancelado = False
    # O Ctrl+C só anota o pedido (uma exceção poderia escapar entre duas linhas e largar o motor rodando);
    # o laço abaixo vê o pedido e pergunta antes de cancelar
    pedido = threading.Event()
    padrao_sigint = signal.getsignal(signal.SIGINT)

    def anotar_pedido(*_):
        pedido.set()

    signal.signal(signal.SIGINT, anotar_pedido)
    ultima_olhada = time.time()
    try:
        while leitor.is_alive():
            leitor.join(0.3)
            painel.desenhar_se_preciso()  # tempo restante e velocidade andam a cada segundo, mesmo sem eventos
            if plano is not None and time.time() - ultima_olhada >= 2:  # a janela mudou de plano: avisa o motor
                ultima_olhada = time.time()
                agora = janela_em_primeiro_plano()
                if agora is not None and agora != plano:
                    plano = agora
                    try:
                        proc.stdin.write(json.dumps({"foreground": plano}) + "\n")
                        proc.stdin.flush()
                    except (OSError, ValueError):
                        pass
            if pedido.is_set():
                acao = perguntar_interrupcao(painel, padrao_sigint)
                if acao in ("pause", "cancel"):
                    cancelado = acao
                    encerrar_motor(proc)
                    break
                signal.signal(signal.SIGINT, anotar_pedido)
                pedido.clear()
        proc.wait()
        leitor.join(5)
    finally:
        signal.signal(signal.SIGINT, padrao_sigint)
    if cancelado:
        for fid, r in resultados.items():
            if not r["ok"] and not r["erro"]:
                painel.atualizar(fid, fim="erro", etapa=t("paused") if cancelado == "pause" else t("cancelled"))
    painel.retomar()
    try:
        os.remove(cfg)
    except OSError:
        pass
    for fid, r in resultados.items():
        if not r["ok"] and not r["erro"]:
            r["erro"] = (t("paused") if cancelado == "pause" else t("conversion_cancelled")) if cancelado \
                else motivo_da_queda(erros_motor, proc.returncode)
            r["detalhes"] = "".join(erros_motor[-40:]).strip() or None
    return resultados, cancelado


def motivo_da_queda(linhas, codigo):
    """Por que o motor parou sem dizer nada, a partir das últimas linhas de erro dele."""
    texto = "".join(linhas)
    falta = re.search(r"No module named '([^']+)'", texto)
    if falta:
        return t("err_missing_lib", falta.group(1).replace("_", "-"))
    if "MemoryError" in texto:
        return t("err_memory")
    if re.search(r"No space left|ENOSPC", texto):
        return t("err_disk_full")
    ultima = next((l.strip() for l in reversed(linhas) if l.strip()), "")
    return f"{t('ended_early')} ({ultima[:160] or codigo})"


def salvar_detalhes_falhas(falhas):
    """Guarda os detalhes técnicos das falhas num arquivo (no terminal fica só o motivo). Devolve o caminho."""
    texto = "\n\n".join(f"{titulo}\n{erro}\n\n{detalhes}" for titulo, erro, detalhes in falhas if detalhes)
    if not texto:
        return None
    caminho = os.path.join(os.path.dirname(caminho_config()), "last-error.log")
    try:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
        return caminho
    except OSError:
        return None


def escolher_apos_falha(falhas, resultados, config):
    """Pergunta o que fazer com os livros que falharam. Devolve se é para converter de novo."""
    # Sem saber o idioma do livro, não dá para escolher uma voz que leia o texto original
    sem_traducao = [l for l in falhas if resultados[l["id"]].get("parte") == "translation" and l["traduzir"] and l["idioma"]]
    acoes = [("retry", t("retry_option"), t("retry_desc"))]
    if sem_traducao:
        acoes.append(("skip", t("skip_translation_option"), t("skip_translation_desc")))
    acoes.append(("cancel", t("cancel_option"), t("cancel_desc")))
    print()
    acao = acoes[ui.escolher(t("failed_what_now"), [(a[1], a[2]) for a in acoes])][0]
    if acao == "skip":
        for l in sem_traducao:
            l["traduzir"] = ""
            l["voz"] = voz_automatica(l["idioma"], None, config)  # a voz era do idioma da tradução
            ui.dica(t("skip_translation_voice", title=l["titulo"], voice=nome_voz(next(v for v in VOZES if v["c"] == l["voz"]))[0]))
    return acao != "cancel"


def perguntar_interrupcao(painel, padrao_sigint):
    """Ctrl+C durante a conversão: continuar, pausar (para retomar depois, aproveitando o que já foi narrado) ou
    cancelar. O painel para de se redesenhar enquanto a pergunta está na tela. Outro Ctrl+C pausa (o mais seguro)."""
    painel.pausar()
    try:
        signal.signal(signal.SIGINT, padrao_sigint)  # durante a pergunta, o Ctrl+C volta a interromper
        print()
        acao = ui.menu(t("interrupt_q"), [(t("interrupt_continue"), "continue"), (t("interrupt_pause"), "pause", t("interrupt_pause_desc")),
                                          (t("interrupt_cancel"), "cancel", t("interrupt_cancel_desc"))], padrao="continue")
    except (KeyboardInterrupt, ui.Cancelado):
        print()
        acao = "pause"
    if acao == "continue":
        print()
        painel.retomar()
    else:
        ui.dica(t("pausing") if acao == "pause" else t("cancelling"))
    return acao


def encerrar_motor(proc):
    """Encerra o motor e tudo o que ele abriu (FFmpeg), sem deixar processos para trás."""
    try:
        proc.stdin.close()  # o motor para sozinho quando a entrada fecha
    except Exception:
        pass
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()
