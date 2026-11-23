"""Texto sincronizado para um audiobook que você já tem (só o áudio, áudio + e-book ou só o texto) e e-book gerado a
partir do áudio. O trabalho pesado roda em bookstudio/sincronia_motor.py, num processo à parte."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from bookstudio import trabalhos, ui
from bookstudio.audiobook.escolhas import escolher_destino_texto, escolher_separacao
from bookstudio.i18n import t

EXT_AUDIO = (".m4b", ".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav", ".mp4", ".aac")
MODOS = ("audio", "ambos", "texto")


def _modelo(config):
    """Modelo do faster-whisper: small (bom e rápido o bastante); base no modo leve; BOOKSTUDIO_WHISPER troca."""
    return os.environ.get("BOOKSTUDIO_WHISPER") or ("base" if config.get("leve") else "small")


def _tem_whisper():
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def _instalar_whisper():
    """O faster-whisper é opcional (pesado): oferece instalar na hora."""
    ui.alerta(t("sy_needs_whisper"))
    if not ui.sim_nao(t("sy_install_whisper_q"), padrao=True):
        return False
    r = subprocess.run([sys.executable, "-m", "pip", "install", "faster-whisper"])
    if r.returncode == 0 and _tem_whisper():
        ui.ok(t("sy_whisper_installed"))
        return True
    ui.falha(t("sy_whisper_failed"))
    ui.dica(f"  {sys.executable} -m pip install faster-whisper")
    return False


def escolher_audio():
    from bookstudio.ebook.fluxos import _dialogo, caminho_limpo
    while True:
        como = ui.menu(t("sy_choose_audio"), [(f"{ui.ICONE['pasta']} {t('eb_browse')}", "browse"), (t("eb_type_path"), "type")])
        p = _dialogo(False, tipos=[(t("sy_audio_files"), " ".join(f"*{e}" for e in EXT_AUDIO)), (t("filetype_all"), "*.*")]) \
            if como == "browse" else caminho_limpo(ui.perguntar(t("prompt_file")))
        if p is None:
            ui.alerta(t("eb_no_dialog"))
        elif not p.is_file():
            ui.falha(t("file_not_found", path=p))
        elif p.suffix.lower() not in EXT_AUDIO:
            ui.falha(t("unsupported_format", name=p.name))
        else:
            return p


def _titulo_autor(audio):
    try:
        import mutagen
        tags = mutagen.File(str(audio), easy=True) or {}
        return (tags.get("title") or [audio.stem])[0], (tags.get("artist") or [""])[0]
    except Exception:  # noqa: BLE001
        return audio.stem, ""


def rodar(pedido, titulo, id_pausado=None):
    """Roda o motor de sincronia com o painel de progresso. Devolve o evento final ("done"/"error") ou
    {"type": "paused"} / {"type": "cancelled"}."""
    arq = os.path.join(tempfile.gettempdir(), f"bookstudio_sync_{uuid.uuid4().hex[:8]}.json")
    json.dump(pedido, open(arq, "w", encoding="utf-8"), ensure_ascii=False)
    grupo = {"creationflags": 0x00000200} if os.name == "nt" else {"start_new_session": True}
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen([sys.executable, "-u", "-m", "bookstudio.sincronia_motor", arq], cwd=raiz, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"), **grupo)
    erros = []
    threading.Thread(target=lambda: erros.extend(proc.stderr.readlines()), daemon=True).start()
    painel = ui.PainelProgresso([{"id": "s", "titulo": titulo}])
    final = {}

    def ler():
        for linha in proc.stdout:
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            if ev.get("type") == "progress":
                feito = f"{ui.tempo(ev['done'])} / {ui.tempo(ev['total'])} {t('sy_of_audio')}" if ev.get("total") else ""
                painel.atualizar("s", pct=float(ev.get("pct") or 0), partes=feito)
            elif ev.get("type") == "stage":
                painel.atualizar("s", etapa=t(ev["code"], *(ev.get("args") or [])))
            elif ev.get("type") in ("done", "error"):
                final.update(ev)

    leitor = threading.Thread(target=ler, daemon=True)
    leitor.start()
    painel.atualizar("s", pct=0, etapa=t("eb_starting"))
    painel.desenhar()
    pedido_int = threading.Event()
    padrao = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: pedido_int.set())
    acao = None
    try:
        while leitor.is_alive():
            leitor.join(0.3)
            painel.desenhar_se_preciso()
            if pedido_int.is_set():
                from bookstudio.audiobook.motor import encerrar_motor, perguntar_interrupcao
                escolha = perguntar_interrupcao(painel, padrao)
                signal.signal(signal.SIGINT, lambda *_: pedido_int.set())
                pedido_int.clear()
                if escolha in ("pause", "cancel"):
                    acao = escolha
                    encerrar_motor(proc)
                    break
        proc.wait()
    finally:
        signal.signal(signal.SIGINT, padrao)
        try:
            os.remove(arq)
        except OSError:
            pass
    if acao == "pause":
        trabalhos.pausar("sincronia", titulo, {"pedido": pedido}, id_pausado)
        painel.atualizar("s", fim="erro", etapa=t("paused"))
        painel.retomar()
        ui.painel([t("sy_paused_saved"), t("paused_resume_hint")], titulo=t("paused"), cor="yellow")
        return {"type": "paused"}
    if acao == "cancel":
        painel.atualizar("s", fim="erro", etapa=t("cancelled"))
        painel.retomar()
        return {"type": "cancelled"}
    if not final:
        from bookstudio.audiobook.motor import motivo_da_queda
        final = {"type": "error", "error": motivo_da_queda(erros, proc.returncode), "details": "".join(erros[-40:])}
    painel.atualizar("s", pct=100 if final["type"] == "done" else None, fim="ok" if final["type"] == "done" else "erro",
                     etapa=None if final["type"] == "done" else (final.get("error") or "")[:200])
    painel.retomar()
    if id_pausado and final["type"] == "done":
        trabalhos.remover(id_pausado)
    return final


def _relatar(final, pedido):
    if final["type"] == "done":
        linhas = list(final.get("paths") or [])
        if final.get("fallback"):
            linhas.append(t("sy_saved_as_file"))
        ui.painel(linhas or [pedido.get("out_dir", "")], titulo=f"{ui.ICONE['ok']} {t('done')}", cor="green")
        return True
    if final["type"] == "error":
        if final.get("code") == "missing_whisper":
            ui.falha(t("sy_needs_whisper"))
        else:
            ui.falha(final.get("error") or t("conversion_failed"))
            from bookstudio.audiobook.motor import salvar_detalhes_falhas
            registro = salvar_detalhes_falhas([(pedido.get("title", ""), final.get("error"), final.get("details"))])
            if registro:
                ui.dica(t("error_details_saved", path=registro))
    return False


def gerar_texto(config, modo=None, audio=None, ebook=None):
    """Texto sincronizado para um audiobook que já existe. `modo`: "audio", "ambos" ou "texto" (pergunta se None)."""
    if modo is None:  # primeiro o modo: as três formas ficam à vista antes de escolher os arquivos
        icone = (lambda e: f"{e} ") if ui.EMOJI else (lambda e: "")
        modo = ui.menu(t("sy_how_q"), [(icone("📖🎧") + t("sy_mode_both"), "ambos", t("sy_mode_both_desc")),
                                       (icone("🎧") + t("sy_mode_audio"), "audio", t("sy_mode_audio_desc")),
                                       (icone("📖") + t("sy_mode_text"), "texto", t("sy_mode_text_desc"))])
    audio = Path(audio) if audio else escolher_audio()
    if modo in ("audio", "ambos"):
        ui.alerta(t("sy_ai_warning"))
        if not _tem_whisper() and not _instalar_whisper():
            return None
    if modo in ("ambos", "texto") and ebook is None:
        from bookstudio.ebook.fluxos import escolher_ebook
        ebook = escolher_ebook(t("sy_choose_ebook"))
    separacao = escolher_separacao(config.get("separacao") or "both")
    ext = audio.suffix.lower()
    destino = "file" if ext not in (".m4b", ".m4a", ".mp3", ".opus", ".flac", ".mp4") else escolher_destino_texto(
        config.get("destino_texto") or "both")
    titulo, autor = _titulo_autor(audio)
    pedido = {"modo": modo, "audio": str(audio), "ebook": str(ebook) if ebook else None, "out_dir": str(audio.parent),
              "title": titulo, "author": autor, "granularity": separacao,
              "destination": {"both": "both", "metadata": "metadata", "file": "library"}[destino],
              "model": _modelo(config)}
    ui.secao(t("sy_generating"), "texto")
    ui.dica(t("ctrl_c_hint_pause"))
    final = rodar(pedido, titulo)
    return _relatar(final, pedido)


def gerar_ebook(config):
    """E-book a partir de um audiobook: a IA transcreve o áudio (pode conter erros)."""
    from bookstudio.ebook.core import formats
    from bookstudio.ebook.fluxos import escolher_saida
    audio = escolher_audio()
    ui.alerta(t("sy_ebook_ai_warning"))
    if not _tem_whisper() and not _instalar_whisper():
        return None
    opcoes = [(formats.FORMAT_NAMES[f], f) for f in formats.WRITE_ORDER if formats.can_write(f)]
    fmt = ui.menu(t("eb_output_format"), opcoes, padrao="epub")
    saida = escolher_saida(audio, fmt, "")
    titulo, autor = _titulo_autor(audio)
    pedido = {"modo": "ebook", "audio": str(audio), "out": str(saida), "title": titulo, "author": autor,
              "model": _modelo(config)}
    ui.secao(t("sy_transcribing"), "texto")
    ui.dica(t("ctrl_c_hint_pause"))
    final = rodar(pedido, titulo)
    return Path(saida) if _relatar(final, pedido) else None


def retomar(trabalho):
    pedido = trabalho["dados"]["pedido"]
    ui.dica(t("sy_resume_restarts"))
    final = rodar(pedido, trabalho.get("titulo") or "", trabalho["id"])
    return _relatar(final, pedido)
