"""Converte um audiobook pronto para outro formato de áudio (M4B, MP3, MP3 por capítulo, Opus, FLAC, WAV), num
processo à parte. Mantém capítulos, capa, título, autor, narrador, gênero, ano e o texto sincronizado (letras).
Usa a montagem do motor: o áudio é separado nos capítulos (FLAC sem perda) e montado de novo no formato pedido.
Uso: python -m bookstudio.conversao_audio_motor pedido.json   (fala com o app por linhas JSON)
"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

from bookstudio import caminhos  # noqa: F401 - põe o motor no sys.path


def emitir(**evento):
    print(json.dumps(evento, ensure_ascii=False), flush=True)


def ler_origem(caminho):
    """Capítulos, tags, capa (data URL), letras (LRC) e canais do audiobook de origem."""
    import mutagen
    from bookstudio.sincronia_motor import capitulos_do_audio, duracao
    arq = mutagen.File(caminho)
    facil = mutagen.File(caminho, easy=True) or {}

    def tag(*nomes):
        for n in nomes:
            v = facil.get(n) if hasattr(facil, "get") else None
            if v:
                return str(v[0])
        return ""
    info = {"titulo": tag("title", "album") or os.path.splitext(os.path.basename(caminho))[0], "autor": tag("artist", "albumartist"),
            "genero": tag("genre"), "ano": tag("date")[:4], "narrador": tag("composer", "performer"), "capa": None, "letras": "",
            "canais": int(getattr(arq.info, "channels", 1) or 1), "duracao": duracao(caminho)}
    tags = getattr(arq, "tags", None) or {}
    chaves = list(tags.keys()) if hasattr(tags, "keys") else []
    capa = None
    if "covr" in chaves:  # MP4/M4B
        c = tags["covr"][0]
        capa = (bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg")
        info["narrador"] = info["narrador"] or str((tags.get("\xa9wrt") or [""])[0])
        info["letras"] = str((tags.get("\xa9lyr") or [""])[0])
    for k in chaves:
        if k.startswith("APIC") and not capa:
            capa = (tags[k].data, tags[k].mime or "image/jpeg")
        if k.startswith("USLT") and not info["letras"]:
            info["letras"] = tags[k].text
        if k.startswith("TCOM") and not info["narrador"]:
            info["narrador"] = str(tags[k].text[0])
    if not capa and getattr(arq, "pictures", None):  # FLAC
        capa = (arq.pictures[0].data, arq.pictures[0].mime or "image/jpeg")
    if not capa and "metadata_block_picture" in [k.lower() for k in chaves]:  # Opus
        from mutagen.flac import Picture
        valor = next(tags[k] for k in chaves if k.lower() == "metadata_block_picture")[0]
        p = Picture(base64.b64decode(valor))
        capa = (p.data, p.mime or "image/jpeg")
    if not info["letras"]:
        info["letras"] = next((str(tags[k][0]) for k in chaves if k.lower() in ("lyrics", "unsyncedlyrics")), "")
    if capa:
        info["capa"] = f"data:{capa[1]};base64," + base64.b64encode(capa[0]).decode()
    caps = capitulos_do_audio(caminho)
    if not caps:  # sem capítulos gravados: o livro inteiro é um capítulo
        caps = [(info["titulo"], 0.0, info["duracao"])]
    info["capitulos"] = [{"title": str(t or f"{n}"), "start": float(a), "end": float(b)} for n, (t, a, b) in enumerate(caps, 1)]
    return info


def _sync_do_lrc(lrc):
    """Frases (início em segundos) a partir de um LRC, para o SYLT do MP3; as marcas <tempo> das palavras saem do texto."""
    itens = []
    for linha in lrc.splitlines():
        m = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)", linha)
        if m:
            texto = re.sub(r"<\d+:\d+(?:\.\d+)?>", "", m.group(3)).strip()
            if texto:
                itens.append({"start": int(m.group(1)) * 60 + float(m.group(2)), "text": texto})
    return {"segments": itens}


def main():
    pedido = json.load(open(sys.argv[1], encoding="utf-8"))
    try:
        import audio_output as ao
        origem, saida, fmt, q = pedido["audio"], pedido["out"], pedido["format"], pedido.get("quality", "high")
        emitir(type="stage", code="ca_reading")
        info = ler_origem(origem)
        canais = 2 if info["canais"] >= 2 and pedido.get("keep_stereo", True) else 1
        codec_original = ao.argumentos_codec

        def codec_com_canais(f, qq):  # o motor grava em mono (voz sintética); aqui vale o número de canais da origem
            args = codec_original(f, qq)
            return [str(canais) if i > 0 and args[i - 1] == "-ac" else a for i, a in enumerate(args)]
        ao.argumentos_codec = codec_com_canais

        with tempfile.TemporaryDirectory(prefix="bookstudio-audio-") as tmp:
            caps = info["capitulos"]
            feitos = {"n": 0}

            def cortar(i):
                c = caps[i]
                destino = os.path.join(tmp, f"cap_{i + 1:03d}.flac")
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{c['start']:.3f}", "-to", f"{c['end']:.3f}", "-i", origem,
                                "-map", "0:a:0", "-ac", str(canais), "-c:a", "flac", destino],
                               stdin=subprocess.DEVNULL, capture_output=True, check=True,
                               creationflags=0x08000000 if os.name == "nt" else 0)
                feitos["n"] += 1
                emitir(type="progress", pct=feitos["n"] / len(caps) * 40, done=feitos["n"], total=len(caps), unit="ca_chapters_split")
                return destino
            emitir(type="stage", code="ca_splitting")
            with ThreadPoolExecutor(max_workers=max(1, min(8, (os.cpu_count() or 2) - 1))) as ex:
                arquivos = list(ex.map(cortar, range(len(caps))))
            capitulos = []
            for n, (c, arq) in enumerate(zip(caps, arquivos), 1):
                capitulos.append({"id": f"ch{n}", "title": c["title"], "start_time": int(round(c["start"] * 1000)),
                                  "end_time": int(round(c["end"] * 1000)), "file": arq})
            emitir(type="stage", code="ca_encoding")
            gerados = []
            if fmt == "multiple":
                os.makedirs(saida, exist_ok=True)
                from mutagen.easyid3 import EasyID3
                from mutagen.id3 import APIC, ID3
                dados_capa = ao.capa_bytes(info["capa"]) if info["capa"] else (None, None)
                for n, c in enumerate(capitulos, 1):
                    nome = re.sub(r'[\\/:*?"<>|]', "", c["title"]).strip()[:80] or str(n)
                    destino = os.path.join(saida, f"{n:02d} - {nome}.mp3")
                    ao.codificar_capitulo(c["file"], destino, "multiple", q)
                    t = EasyID3()
                    t.update({"title": c["title"], "album": info["titulo"], "artist": info["autor"] or "", "tracknumber": f"{n}/{len(capitulos)}"})
                    if info["genero"]:
                        t["genre"] = info["genero"]
                    t.save(destino)
                    if dados_capa and dados_capa[0]:
                        id3 = ID3(destino)
                        id3.add(APIC(encoding=3, mime=dados_capa[1], type=3, desc="Cover", data=dados_capa[0]))
                        id3.save(destino)
                    gerados.append(destino)
                    emitir(type="progress", pct=40 + n / len(capitulos) * 58, done=n, total=len(capitulos), unit="ca_chapters_encoded")
            else:
                def progresso(frac):
                    emitir(type="progress", pct=40 + float(frac) * 58)
                ao.gerar_arquivo_unico(saida, fmt, q, capitulos, info["titulo"], info["autor"], info["genero"], info["narrador"],
                                       info["capa"], tmp, progresso, max(1, min(8, (os.cpu_count() or 2) - 1)))
                if fmt == "single":
                    from converter import aplicar_tags_arquivo_unico
                    aplicar_tags_arquivo_unico(saida, info["titulo"], info["autor"], info["capa"], capitulos, info["genero"], info["narrador"])
                gerados.append(saida)
            for arq in gerados:
                if info["ano"]:
                    ao.aplicar_ano(arq, info["ano"])
                try:
                    ao.aplicar_extras(arq, info["autor"], info["narrador"], "", "")
                except Exception:  # noqa: BLE001 - extras são opcionais
                    pass
            # Texto sincronizado: dentro do arquivo novo quando o formato guarda; senão (e sempre que já havia um .lrc), ao lado
            avisos = []
            lrc = info["letras"]
            irmao = os.path.splitext(origem)[0] + ".lrc"
            if not lrc and os.path.isfile(irmao):
                lrc = open(irmao, encoding="utf-8", errors="replace").read()
            if lrc:
                from sync_text_tools import embed_in_audio
                dentro = fmt not in ("wav", "multiple") and embed_in_audio(gerados[0], _sync_do_lrc(lrc), lrc)
                if not dentro or os.path.isfile(irmao):
                    base = saida if fmt == "multiple" else os.path.splitext(saida)[0]
                    destino_lrc = os.path.join(base, f"{info['titulo']}.lrc") if fmt == "multiple" else base + ".lrc"
                    open(destino_lrc, "w", encoding="utf-8").write(lrc)
                    gerados.append(destino_lrc)
                    if not dentro:
                        avisos.append("ca_text_beside")
        emitir(type="done", paths=gerados, warnings=avisos)
    except Exception as e:  # noqa: BLE001
        import traceback
        emitir(type="error", error=f"{type(e).__name__}: {e}", details=traceback.format_exc()[-3000:])


if __name__ == "__main__":
    main()
