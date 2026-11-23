"""Escritor de PDF sem Calibre, com o Story do PyMuPDF.

Gera um PDF de leitura simples (A5, texto corrido, cada capítulo em nova página).
Imagens não entram. Com o Calibre instalado, o app prefere o PDF do Calibre.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

import pymupdf

from ..model import Book
from ..xhtml import chapter_blocks, first_heading

CSS = """
body { font-family: serif; font-size: 11pt; line-height: 1.45; }
h2 { font-family: sans-serif; font-size: 16pt; margin-top: 18pt; margin-bottom: 12pt; }
p { margin: 0 0 6pt 0; text-align: justify; }
"""


def write_pdf_basic(book: Book, out: Path) -> None:
    rtl = (book.target_language or "").split("-")[0] in {"ar", "he", "fa"}
    page = pymupdf.paper_rect("a5")
    where = page + (48, 54, -48, -54)
    writer = pymupdf.DocumentWriter(str(out))
    try:
        # folha de rosto
        cover = f"<h2>{escape(book.title)}</h2>"
        if book.authors:
            cover += f"<p>{escape(', '.join(book.authors))}</p>"
        chapters_html = [cover]
        titles = [""]
        for ch in book.content_chapters:
            parts = []
            for kind, text in chapter_blocks(ch.data):
                text_html = escape(text).replace("\n", "<br/>")
                parts.append(f"<h2>{text_html}</h2>" if kind == "h" else f"<p>{text_html}</p>")
            if parts:
                chapters_html.append("".join(parts))
                titles.append(first_heading(ch.data) or ch.title)
        toc, pages = [], 0
        for body, title in zip(chapters_html, titles, strict=True):
            if title:
                toc.append([1, title, pages + 1])
            attrs = ' dir="rtl"' if rtl else ""
            story = pymupdf.Story(html=f"<body{attrs}>{body}</body>", user_css=CSS)
            more = 1
            while more:
                dev = writer.begin_page(page)
                more, _ = story.place(where)
                story.draw(dev)
                writer.end_page()
                pages += 1
    finally:
        writer.close()
    doc = pymupdf.open(out)
    try:
        if toc:  # sumário (marcadores) para leitores de PDF e para reabrir o arquivo depois
            doc.set_toc(toc)
        subject = book.description.split("\n")[0][:300] if book.description else ""
        if book.series:
            serie = f"{book.series} #{book.series_index}" if book.series_index else book.series
            subject = f"{serie} — {subject}" if subject else serie
        doc.set_metadata({**(doc.metadata or {}), "title": book.title, "author": ", ".join(book.authors),
                          "subject": subject, "keywords": ", ".join(book.subjects)})
        doc.saveIncr()
    finally:
        doc.close()
