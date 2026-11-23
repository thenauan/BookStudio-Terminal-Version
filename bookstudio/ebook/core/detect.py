"""Detecção de idioma com lingua, sobre amostras espalhadas pelo livro."""

from __future__ import annotations

from functools import lru_cache

from .. import languages
from .model import Book
from .xhtml import chapter_blocks

SAMPLE_COUNT = 8
SAMPLE_CHARS = 900


@lru_cache(maxsize=1)
def _detector():
    from lingua import IsoCode639_1, Language, LanguageDetectorBuilder

    langs = {Language.from_iso_code_639_1(getattr(IsoCode639_1, lang.iso1)) for lang in languages.SOURCE_LANGS}
    return LanguageDetectorBuilder.from_languages(*langs).build()


def sample_text(book: Book, max_chars: int = SAMPLE_COUNT * SAMPLE_CHARS) -> str:
    chapters = book.content_chapters
    if len(chapters) > 4:  # pula a primeira (rosto, créditos, sumário) se houver bastante
        chapters = chapters[1:]
    if not chapters:
        return ""
    step = max(1, len(chapters) // SAMPLE_COUNT)
    picked = chapters[::step][:SAMPLE_COUNT]
    parts: list[str] = []
    per = max_chars // max(1, len(picked))
    for ch in picked:
        buf = ""
        for kind, text in chapter_blocks(ch.data):
            if kind == "p" and len(text) > 40:
                buf += text + " "
                if len(buf) >= per:
                    break
        parts.append(buf[:per])
    return " ".join(p for p in parts if p).strip()


def detect_language(book: Book) -> tuple[languages.Lang | None, float]:
    """Retorna (idioma, confiança 0–1). Usa o metadado só como desempate."""

    text = sample_text(book)
    if len(text) < 20:
        meta = languages.get(book.language) if book.language else None
        return meta, 0.0
    values = _detector().compute_language_confidence_values(text)
    if not values:
        return None, 0.0
    best = values[0]
    lang = languages.source_from_iso1(best.language.iso_code_639_1.name)
    return lang, float(best.value)


def detect_text(text: str) -> str | None:
    """Código ISO 639-1 (maiúsculo) do idioma de um trecho, ou None."""

    if len(text) < 40:
        return None
    lang = _detector().detect_language_of(text)
    return lang.iso_code_639_1.name if lang else None
