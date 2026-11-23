"""Leitor de PDF com PyMuPDF.

PDF guarda posições na página, não parágrafos. Aqui reconstruímos o texto corrido:
removemos cabeçalhos/rodapés repetidos e números de página, juntamos palavras
hifenizadas e parágrafos quebrados entre páginas, e usamos o sumário do PDF (ou
títulos com fonte maior) para separar os capítulos. A página é processada e
liberada uma por vez, para não acumular memória.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from ..model import Book, Chapter, DRMError, EbookError, ScannedPDFError
from ..xhtml import make_chapter

PAGE_NUM_RE = re.compile(r"^\s*(\d{1,4}|[ivxlcdm]{1,7})\s*$", re.IGNORECASE)
END_PUNCT = tuple('.!?:;"”»)’…—')
PAGES_PER_PART = 15


@dataclass
class _Block:
    page: int
    text: str
    size: float
    first: bool  # primeira linha da página
    last: bool  # última linha da página


def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def _join_lines(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln
        elif out:
            out += " " + ln
        else:
            out = ln
    return out


def read_pdf(path: Path) -> Book:
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001 - PyMuPDF lança vários tipos
        raise EbookError("Não consegui abrir o PDF (arquivo corrompido?).") from e
    with doc:
        if doc.needs_pass:
            raise DRMError("O PDF é protegido por senha.")
        n_pages = doc.page_count
        meta = doc.metadata or {}
        toc = [(lvl, title.strip(), page) for lvl, title, page in doc.get_toc(simple=True) if title.strip()]

        blocks: list[_Block] = []
        edge_lines: Counter = Counter()
        total_chars = 0
        for pno in range(n_pages):
            page = doc.load_page(pno)
            data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
            page_blocks = []
            for b in data.get("blocks", []):
                lines, sizes = [], []
                for line in b.get("lines", []):
                    txt = "".join(s.get("text", "") for s in line.get("spans", []))
                    if txt.strip():
                        lines.append(txt)
                        sizes.extend(s.get("size", 0) for s in line.get("spans", []) if s.get("text", "").strip())
                if lines:
                    page_blocks.append((lines, max(sizes) if sizes else 0))
            for i, (lines, size) in enumerate(page_blocks):
                text = _join_lines(lines)
                total_chars += len(text)
                blk = _Block(pno, text, size, i == 0, i == len(page_blocks) - 1)
                blocks.append(blk)
                if blk.first or blk.last:
                    edge_lines[_norm(text)] += 1
            del page, data  # libera a página

    if total_chars < 50 * max(1, n_pages) and total_chars < 2000:
        raise ScannedPDFError("O PDF parece escaneado (só imagens): não há texto para extrair.")

    # remove cabeçalhos/rodapés repetidos e números de página
    repeated = {k for k, v in edge_lines.items() if n_pages >= 4 and v >= max(3, n_pages * 0.3)}
    clean = [
        b for b in blocks
        if not PAGE_NUM_RE.match(b.text) and not ((b.first or b.last) and _norm(b.text) in repeated)
    ]
    if not clean:
        raise EbookError("Não encontrei texto aproveitável no PDF.")

    sizes = [b.size for b in clean for _ in range(max(1, len(b.text) // 50))]
    body = statistics.median(sizes) if sizes else 10.0

    # junta parágrafos quebrados na virada de página
    merged: list[_Block] = []
    for b in clean:
        if (
            merged
            and b.page != merged[-1].page
            and abs(b.size - merged[-1].size) < 0.5
            and not merged[-1].text.endswith(END_PUNCT)
            and b.text[:1].islower()
        ):
            prev = merged[-1]
            joiner = "" if prev.text.endswith("-") else " "
            prev.text = (prev.text[:-1] if joiner == "" else prev.text) + joiner + b.text
            prev.page = b.page
        else:
            merged.append(_Block(b.page, b.text, b.size, b.first, b.last))

    def kind(b: _Block) -> str:
        return "h" if b.size >= body * 1.25 and len(b.text) < 120 else "p"

    # capítulos: sumário do PDF > títulos grandes > partes de N páginas
    starts: list[tuple[int, str]] = []
    top = [(t, p - 1) for lvl, t, p in toc if lvl == 1 and p >= 1]
    if len(top) >= 2:
        starts = [(p, t) for t, p in top]
    else:
        big = [(b.page, b.text) for b in merged if b.size >= body * 1.4 and len(b.text) < 100]
        if len(big) >= 2:
            starts = big

    chapters_blocks: list[tuple[str, list[tuple[str, str]]]] = []
    if starts:
        bounds = sorted({p for p, _ in starts})
        titles = {}
        for p, t in starts:
            titles.setdefault(p, t)
        groups: dict[int, list[_Block]] = {p: [] for p in bounds}
        pre: list[_Block] = []
        for b in merged:
            owner = None
            for p in bounds:
                if b.page >= p:
                    owner = p
                else:
                    break
            (groups[owner] if owner is not None else pre).append(b)
        if pre:
            chapters_blocks.append(("Início", [(kind(b), b.text) for b in pre]))
        for p in bounds:
            if groups[p]:
                chapters_blocks.append((titles[p], [(kind(b), b.text) for b in groups[p]]))
    else:
        part: list[_Block] = []
        n = 1
        for b in merged:
            if part and b.page // PAGES_PER_PART != part[0].page // PAGES_PER_PART:
                chapters_blocks.append((f"Parte {n}", [(kind(x), x.text) for x in part]))
                part, n = [], n + 1
            part.append(b)
        if part:
            chapters_blocks.append((f"Parte {n}", [(kind(x), x.text) for x in part]))

    chapters = [
        Chapter(path=f"OEBPS/cap{i:04d}.xhtml", data=make_chapter(t, blks), title=t)
        for i, (t, blks) in enumerate(chapters_blocks, 1)
    ]
    book = Book(
        title=(meta.get("title") or "").strip() or path.stem,
        authors=[meta["author"].strip()] if (meta.get("author") or "").strip() else [],
        chapters=chapters,
    )
    book.warnings.append("PDF lido como texto corrido: imagens e layout de página não são mantidos.")
    return book
