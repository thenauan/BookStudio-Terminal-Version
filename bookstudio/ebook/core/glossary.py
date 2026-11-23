"""Glossário automático de nomes próprios (personagens, lugares).

Heurística barata, sem chamar a IA: palavras com inicial maiúscula que aparecem
várias vezes no meio da frase e quase nunca em minúsculas.
"""

from __future__ import annotations

import re
from collections import Counter

from .. import languages
from .model import Book
from .xhtml import chapter_blocks

WORD_RE = re.compile(r"(?<![.!?…:\"“«]\s)(?<!^)\b([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]{2,})\b")
LOWER_RE = re.compile(r"\b[a-zà-öø-ÿ][a-zà-öø-ÿ'’-]{2,}\b")
MIN_COUNT = 3
MAX_TERMS = 60


def build_glossary(book: Book, source_code: str) -> list[str]:
    if source_code in languages.NO_CASE:
        return []
    caps: Counter = Counter()
    lower: Counter = Counter()
    for ch in book.content_chapters:
        for _, text in chapter_blocks(ch.data):
            for sentence in re.split(r"(?<=[.!?…])\s+", text):
                caps.update(WORD_RE.findall(sentence))
                lower.update(LOWER_RE.findall(sentence))
    terms = [
        (w, n) for w, n in caps.items()
        if n >= MIN_COUNT and lower.get(w.lower(), 0) <= n * 0.2
    ]
    terms.sort(key=lambda x: -x[1])
    return [w for w, _ in terms[:MAX_TERMS]]
