"""Leitor de TXT: detecta a codificação e divide em capítulos por heurística."""

from __future__ import annotations

import re
from pathlib import Path

from charset_normalizer import from_bytes

from ..model import Book, Chapter, EbookError
from ..xhtml import make_chapter

CHAPTER_RE = re.compile(
    r"^\s*(cap[íi]tulo|chapter|chapitre|kapitel|capitolo|cap\.|parte|part|livro|book|"
    r"pr[óo]logo|prologue|prolog|ep[íi]logo|epilogue|epilog|introdu[çc][ãa]o|introduction|"
    r"глава|часть|rozdzia[łl]|kapitola|hoofdstuk|luku|kapittel|cap[íi]tol|fejezet|b[öo]l[üu]m)\b.{0,70}$",
    re.IGNORECASE,
)
ROMAN_RE = re.compile(r"^\s*[IVXLC]{1,8}\.?\s*$")
NUMBER_RE = re.compile(r"^\s*\d{1,3}\.?\s*$")
CJK_CHAPTER_RE = re.compile(r"^\s*第[0-9一二三四五六七八九十百千零〇]+[章回节話话]")
GUTENBERG_START = re.compile(r"^\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG.*$", re.IGNORECASE | re.M)
GUTENBERG_END = re.compile(r"^\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG.*$", re.IGNORECASE | re.M)

WORDS_PER_PART = 4000


def decode_text(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw).best()
    if best is None:
        return raw.decode("cp1252", errors="replace")
    # Entre as codificações latinas o detector às vezes confunde cp1252 com cp1250
    # (vira "Joăo"); em livros ocidentais cp1252 é de longe a mais comum.
    latin = {"cp1250", "cp1252", "latin_1", "iso8859_2", "iso8859_15", "cp1257", "iso8859_13", "mac_roman"}
    if best.encoding in latin:
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError:
            pass
    return str(best)


def _paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"\n\s*\n", text):
        # parágrafos separados por linha em branco (texto quebrado em linhas fixas)
        paras = [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    else:
        paras = [ln.strip() for ln in text.split("\n")]
    return [p for p in paras if p]


def _is_heading(p: str) -> bool:
    if len(p) > 80:
        return False
    return bool(CHAPTER_RE.match(p) or ROMAN_RE.match(p) or NUMBER_RE.match(p) or CJK_CHAPTER_RE.match(p))


def split_chapters(paras: list[str]) -> list[tuple[str, list[tuple[str, str]]]]:
    heads = [i for i, p in enumerate(paras) if _is_heading(p)]
    chapters: list[tuple[str, list[tuple[str, str]]]] = []
    if len(heads) >= 2:
        if heads[0] > 0:
            chapters.append(("Início", [("p", p) for p in paras[: heads[0]]]))
        for k, start in enumerate(heads):
            end = heads[k + 1] if k + 1 < len(heads) else len(paras)
            title = paras[start]
            blocks = [("h", title)] + [("p", p) for p in paras[start + 1 : end]]
            chapters.append((title, blocks))
        return chapters

    # sem capítulos reconhecíveis: partes de ~4000 palavras, sem cortar parágrafos
    part, words, n = [], 0, 1
    for p in paras:
        part.append(("p", p))
        words += len(p.split())
        if words >= WORDS_PER_PART:
            chapters.append((f"Parte {n}", part))
            part, words, n = [], 0, n + 1
    if part:
        chapters.append((f"Parte {n}", part))
    return chapters


def read_txt(path: Path) -> Book:
    raw = path.read_bytes()
    if not raw.strip():
        raise EbookError("O arquivo de texto está vazio.")
    text = decode_text(raw)
    start, end = GUTENBERG_START.search(text), GUTENBERG_END.search(text)
    if start and end and start.end() < end.start():
        text = text[start.end() : end.start()]
    paras = _paragraphs(text)
    chapters = []
    for i, (title, blocks) in enumerate(split_chapters(paras), 1):
        chapters.append(Chapter(path=f"OEBPS/cap{i:04d}.xhtml", data=make_chapter(title, blocks), title=title))
    return Book(title=path.stem, chapters=chapters)
