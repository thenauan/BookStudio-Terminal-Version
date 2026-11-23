"""Converter o formato de um audiobook pronto (M4B ⇄ MP3 ⇄ Opus ⇄ FLAC ⇄ WAV ⇄ um MP3 por capítulo), mantendo
capítulos, capa, metadados e o texto sincronizado. O trabalho roda em bookstudio/conversao_audio_motor.py."""
from pathlib import Path

from bookstudio import ui
from bookstudio.audiobook.opcoes import FORMATOS, nome_formato
from bookstudio.i18n import t
from bookstudio.sincronia import _relatar, escolher_audio, rodar
from bookstudio.sistema import verificar_dependencias

EXT_FORMATO = {".m4b": "m4b", ".m4a": "m4b", ".mp4": "m4b", ".mp3": "single", ".opus": "opus", ".ogg": "opus",
               ".flac": "flac", ".wav": "wav"}
EXT_SAIDA = {"m4b": ".m4b", "single": ".mp3", "opus": ".opus", "flac": ".flac", "wav": ".wav"}


def _resumo(audio):
    from bookstudio.conversao_audio_motor import ler_origem
    try:
        info = ler_origem(str(audio))
    except Exception:  # noqa: BLE001 - arquivo sem tags legíveis: segue com o nome do arquivo
        return audio.stem
    ui.cartao(info["titulo"], [(t("field_author"), info["autor"]), (t("ca_narrator"), info["narrador"]),
                               (t("field_chapters"), str(len(info["capitulos"]))),
                               (t("ca_duration"), ui.tempo(info["duracao"]) if info["duracao"] else ""),
                               (t("ca_cover"), t("yes_word") if info["capa"] else t("no_word")),
                               (t("ca_synced_text"), t("yes_word") if info["letras"] else t("no_word"))])
    return info["titulo"]


def converter_audiobook(config):
    """Escolhe o audiobook, o formato e a qualidade, e converte."""
    if not verificar_dependencias():  # sem FFmpeg não dá para converter áudio
        ui.falha(t("ca_needs_ffmpeg"))
        return None
    audio = escolher_audio()
    titulo = _resumo(audio)
    atual = EXT_FORMATO.get(audio.suffix.lower())
    opcoes = [(nome_formato(f), f, t(desc)) for f, _, desc in FORMATOS if f != atual]
    fmt = ui.menu(t("ca_target_format"), opcoes, padrao="single" if atual != "single" else "m4b")
    qualidade = "high"
    if fmt in ("m4b", "single", "multiple", "opus"):
        qualidade = ui.menu(t("ca_quality_q"), [(t("ca_q_voice"), "voice", t("ca_q_voice_desc")), (t("ca_q_standard"), "standard"),
                                               (t("ca_q_high"), "high", t("ca_q_high_desc")), (t("ca_q_max"), "max")], padrao="high")
    if fmt == "multiple":
        saida = audio.with_name(audio.stem)
        if saida.exists() and any(saida.iterdir()) and not ui.sim_nao(t("ca_folder_exists_q", name=saida.name), padrao=False):
            return None
    else:
        from bookstudio.ebook.fluxos import escolher_saida
        saida = escolher_saida(audio, EXT_SAIDA[fmt].lstrip("."), "")
    pedido = {"audio": str(audio), "out": str(saida), "format": fmt, "quality": qualidade, "title": titulo}
    ui.secao(t("ca_converting", format=nome_formato(fmt)), "formato")
    ui.dica(t("ctrl_c_hint_pause"))
    final = rodar(pedido, titulo, modulo="bookstudio.conversao_audio_motor", tipo="conversao_audio")
    if final.get("type") == "done" and "ca_text_beside" in (final.get("warnings") or []):
        ui.dica(t("ca_text_beside"))
    return Path(saida) if _relatar(final, pedido) else None
