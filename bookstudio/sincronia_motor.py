"""Trabalho pesado do texto sincronizado e do e-book gerado do áudio, num processo à parte (pode ser pausado ou
cancelado sem travar o app). Fala com o app por linhas JSON: {"type": "progress"|"stage"|"done"|"error", ...}.

Modos ("modo" no pedido):
  audio   só o áudio: a IA (faster-whisper) transcreve, com o tempo de cada frase e palavra
  ambos   áudio + e-book: o texto do livro é alinhado à fala (o mais preciso)
  texto   só o texto: os tempos são distribuídos pela duração de cada capítulo do áudio (aproximado, sem IA)
  ebook   gerar um e-book a partir do áudio (transcrição pela IA)
Uso: python -m bookstudio.sincronia_motor pedido.json
"""
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from bookstudio import caminhos  # noqa: F401 - põe o motor no sys.path


def emitir(**evento):
    print(json.dumps(evento, ensure_ascii=False), flush=True)


def capitulos_do_audio(audio):
    """[(título, início, fim)] dos capítulos gravados no arquivo (M4B/MP4, MP3 com CHAP); [] se não houver."""
    try:
        import mutagen
        from mutagen.mp4 import MP4
        if audio.lower().endswith((".m4b", ".m4a", ".mp4")):
            m = MP4(audio)
            caps = list(m.chapters or [])
            dur = float(m.info.length)
            return [(c.title, float(c.start), float(caps[i + 1].start) if i + 1 < len(caps) else dur) for i, c in enumerate(caps)]
        arq = mutagen.File(audio)
        tags = getattr(arq, "tags", None)
        chaps = sorted([f for k, f in (tags.items() if tags else []) if k.startswith("CHAP")], key=lambda c: c.start_time)
        return [(str(c.sub_frames.get("TIT2", "")) or f"{i + 1}", c.start_time / 1000, c.end_time / 1000) for i, c in enumerate(chaps)]
    except Exception:  # noqa: BLE001
        return []


def duracao(audio):
    try:
        import mutagen
        return float(mutagen.File(audio).info.length)
    except Exception:  # noqa: BLE001
        return 0.0


def transcrever(audio, idioma, modelo):
    """Transcreve mostrando o progresso (até onde do áudio já foi)."""
    import sync_audiobook as sa
    if not sa.HAS_FASTER_WHISPER:
        raise RuntimeError("missing_whisper")
    from faster_whisper import WhisperModel
    dispositivo, tipo = sa._detect_compute_environment(None)
    emitir(type="stage", code="sy_loading_model", args=[modelo])
    modelo_w = WhisperModel(modelo, device=dispositivo, compute_type=tipo, cpu_threads=max(1, (os.cpu_count() or 2) - 1))
    emitir(type="stage", code="sy_transcribing")
    # beam_size=1 (busca gulosa): ~2-3x mais rápido que o padrão 5, quase sem perda em fala clara de audiobook;
    # vad_filter pula os silêncios (pausas entre capítulos), e os tempos continuam os do áudio original
    segmentos_gen, info = modelo_w.transcribe(audio, language=idioma or None, word_timestamps=True, beam_size=1,
                                              vad_filter=True, condition_on_previous_text=False)
    total = float(getattr(info, "duration", 0) or duracao(audio))
    segmentos, palavras = [], []
    for seg in segmentos_gen:
        texto = seg.text.strip()
        if texto:
            segmentos.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": texto})
        for w in getattr(seg, "words", None) or []:
            if w.word.strip():
                palavras.append({"start": round(float(w.start), 2), "end": round(float(w.end), 2), "text": w.word.strip(),
                                 "word": w.word.strip(), "score": float(getattr(w, "probability", 1.0))})
        emitir(type="progress", pct=min(99.0, float(seg.end) / total * 100) if total else 0, done=round(float(seg.end)),
               total=round(total))
    return segmentos, palavras, getattr(info, "language", idioma)


def texto_estimado(audio, ebook):
    """Só com o texto: as frases de cada capítulo do livro ocupam o capítulo do áudio, proporcionalmente ao tamanho."""
    from book_structure import estruturar_livro
    caps_livro = [c for c in estruturar_livro(ebook) if c["conteudo"].strip()]
    caps_audio = capitulos_do_audio(audio)
    total = duracao(audio)
    if len(caps_audio) != len(caps_livro):  # não dá para casar capítulo a capítulo: o livro inteiro no áudio inteiro
        caps_livro = [{"titulo": "", "conteudo": "\n\n".join(c["conteudo"] for c in caps_livro)}]
        caps_audio = [("", 0.0, total)]
    segmentos = []
    for (_, ini, fim), cap in zip(caps_audio, caps_livro):
        frases = [f.strip() for f in re.split(r"(?<=[.!?…])\s+|\n+", cap["conteudo"]) if f.strip()]
        soma = sum(len(f) for f in frases) or 1
        t0 = ini
        for f in frases:
            d = (fim - ini) * len(f) / soma
            segmentos.append({"start": round(t0, 2), "end": round(t0 + d, 2), "text": f})
            t0 += d
        emitir(type="progress", pct=min(99.0, fim / total * 100) if total else 0)
    return segmentos


def ebook_do_audio(pedido, segmentos, idioma):
    """Monta o e-book: um capítulo por capítulo do áudio (ou um só), parágrafos nas pausas longas da fala."""
    from bookstudio.ebook.core import formats
    from bookstudio.ebook.core.model import Book, Chapter
    caps = capitulos_do_audio(pedido["audio"]) or [(pedido.get("title") or "", 0.0, float("inf"))]
    capitulos = []
    for n, (titulo, ini, fim) in enumerate(caps, 1):
        dele = [s for s in segmentos if ini <= s["start"] < fim]
        paragrafos, atual, ultimo = [], [], None
        for s in dele:
            if atual and (s["start"] - ultimo > 1.2 or len(atual) >= 6):
                paragrafos.append(" ".join(atual))
                atual = []
            atual.append(s["text"])
            ultimo = s["end"]
        if atual:
            paragrafos.append(" ".join(atual))
        corpo = "".join(f"<p>{html.escape(p)}</p>" for p in paragrafos)
        titulo_cap = titulo or (pedido.get("title") if len(caps) == 1 else str(n))
        xhtml = (f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{html.escape(titulo_cap)}</title>'
                 f'</head><body><h1>{html.escape(titulo_cap)}</h1>{corpo}</body></html>')
        capitulos.append(Chapter(path=f"cap{n:03d}.xhtml", data=xhtml.encode("utf-8"), title=titulo_cap))
    livro = Book(title=pedido.get("title") or Path(pedido["audio"]).stem, authors=[a for a in [pedido.get("author")] if a],
                 language=idioma or "", chapters=capitulos)
    with tempfile.TemporaryDirectory(prefix="bookstudio-") as tmp:
        formats.write_book(livro, Path(pedido["out"]), Path(tmp))
    return pedido["out"]


def main():
    pedido = json.load(open(sys.argv[1], encoding="utf-8"))
    modo, audio = pedido["modo"], pedido["audio"]
    try:
        from sync_text_tools import refinar_sincronia, save_sync_text
        if modo == "ebook":
            segmentos, _, idioma = transcrever(audio, pedido.get("language"), pedido.get("model", "small"))
            emitir(type="stage", code="sy_writing_ebook")
            emitir(type="done", paths=[ebook_do_audio(pedido, segmentos, idioma)])
            return
        if modo == "audio":
            segmentos, palavras, idioma = transcrever(audio, pedido.get("language"), pedido.get("model", "small"))
            dados = {"segments": segmentos, "words": palavras, "sync_source": "audio_transcription_whisper", "language": idioma}
        elif modo == "ambos":
            import sync_audiobook as sa
            _, palavras, idioma = transcrever(audio, pedido.get("language"), pedido.get("model", "small"))
            emitir(type="stage", code="sy_aligning")
            dados = sa.synchronize_audiobook(audio, pedido["ebook"], language=idioma or "pt", precomputed_audio_words=palavras,
                                             log_func=None)
            dados.setdefault("segments", dados.get("sentences") or [])
        else:  # texto
            emitir(type="stage", code="sy_estimating")
            dados = {"segments": texto_estimado(audio, pedido["ebook"]), "sync_source": "text_estimated"}
        separacao = pedido.get("granularity", "both")
        dados["segments"], palavras = refinar_sincronia(dados.get("segments") or [], dados.get("words") or [])
        if palavras and separacao != "sentences":
            dados["words"] = palavras
        else:
            dados.pop("words", None)
        if separacao == "words" and dados.get("words"):
            dados["segments"] = []
        emitir(type="stage", code="sy_saving")
        r = save_sync_text(dados, audio, pedido["out_dir"], pedido.get("title") or Path(audio).stem, pedido.get("author", ""),
                           pedido.get("destination", "both"))
        emitir(type="done", paths=r.get("files") or [], embedded=bool(r.get("embedded")), fallback=bool(r.get("fallbackToLibrary")))
    except Exception as e:  # noqa: BLE001
        import traceback
        emitir(type="error", code="missing_whisper" if str(e) == "missing_whisper" else None,
               error=f"{type(e).__name__}: {e}", details=traceback.format_exc()[-3000:])


if __name__ == "__main__":
    main()
