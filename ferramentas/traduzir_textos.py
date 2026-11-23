"""Grava os textos de ferramentas/textos_bookstudio.py em bookstudio/dados/i18n: português e inglês como estão,
os outros idiomas traduzidos automaticamente do inglês pelo Google (as {variáveis} ficam protegidas).
Só traduz o que falta (ou tudo, com --todos)."""
import json
import pathlib
import re
import sys
import time

AQUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))
from textos_bookstudio import TEXTOS  # noqa: E402
from bookstudio.ebook.translate.google import _direto  # noqa: E402

PASTA = AQUI.parent / "bookstudio" / "dados" / "i18n"
GOOGLE = {"zh": "zh-CN"}
todos = "--todos" in sys.argv
# --chaves a,b,c: traduz de novo só essas (textos que mudaram)
refazer = set(sys.argv[sys.argv.index("--chaves") + 1].split(",")) if "--chaves" in sys.argv else set()


def proteger(s):
    marcas = re.findall(r"\{[^}]*\}", s)
    for i, m in enumerate(marcas):
        s = s.replace(m, f"[[{i}]]", 1)
    return s, marcas


def restaurar(s, marcas):
    for i, m in enumerate(marcas):
        s = re.sub(rf"\[\[\s*{i}\s*\]\]", lambda _: m, s)
    return s


for arq in sorted(PASTA.glob("*.json")):
    lang = arq.stem
    dados = json.loads(arq.read_text(encoding="utf-8"))
    pendentes = {k: v for k, v in TEXTOS.items() if todos or k not in dados or k in refazer}
    if lang in ("pt", "en"):
        for k, (pt, en) in pendentes.items():
            dados[k] = pt if lang == "pt" else en
    else:
        chaves = list(pendentes)
        for i in range(0, len(chaves), 40):  # lotes de 40 textos, separados por linhas numeradas
            lote = chaves[i:i + 40]
            prot = [proteger(TEXTOS[k][1]) for k in lote]
            entrada = "\n".join(f"<{n}> {p}" for n, (p, _) in enumerate(prot))
            for tentativa in range(5):
                try:
                    saida = _direto(entrada, "en", GOOGLE.get(lang, lang))
                    break
                except Exception as e:  # noqa: BLE001
                    print(lang, "nova tentativa", e)
                    time.sleep(3 * (tentativa + 1))
            partes = dict(re.findall(r"<\s*(\d+)\s*>\s*(.*)", saida))
            for n, k in enumerate(lote):
                trad = partes.get(str(n))
                texto = restaurar(trad.strip(), prot[n][1]) if trad else TEXTOS[k][1]
                if len(re.findall(r"\{[^}]*\}", texto)) != len(prot[n][1]):
                    texto = TEXTOS[k][1]  # variável perdida na tradução: fica o inglês
                dados[k] = texto
            time.sleep(0.5)
    arq.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{lang}: {len(pendentes)} textos")
