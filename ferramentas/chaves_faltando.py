"""Lista as chaves de tradução usadas no código (t("chave")) que faltam em bookstudio/dados/i18n/en.json."""
import json
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "bookstudio"
DINAMICAS = [f"eb_meta_{c}" for c in ("title", "authors", "series", "series_index", "publisher", "year", "subjects", "isbn",
                                       "language", "description")] + \
            [f"eb_meta_{c}_hint" for c in ("authors", "series", "series_index", "year", "subjects")] + \
            ["paused_audiobook", "paused_sincronia"]
usadas = set(DINAMICAS)
for p in RAIZ.rglob("*.py"):
    usadas |= set(re.findall(r"""\bt\(\s*["']([a-z][a-z0-9_]+)["']""", p.read_text(encoding="utf-8")))
en = json.loads((RAIZ / "dados" / "i18n" / "en.json").read_text(encoding="utf-8"))
faltam = sorted(usadas - set(en))
print(len(faltam))
print(" ".join(faltam))
