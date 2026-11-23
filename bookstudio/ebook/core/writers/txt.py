"""Escritor de TXT: títulos e parágrafos separados por linha em branco."""

from __future__ import annotations

from pathlib import Path

from ..model import Book
from ..xhtml import chapter_blocks


def write_txt(book: Book, out: Path) -> None:
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(book.title.strip() + "\n")
        if book.authors:
            f.write(", ".join(book.authors) + "\n")
        for ch in book.content_chapters:  # um capítulo por vez na memória
            blocks = chapter_blocks(ch.data)
            if not blocks:
                continue
            f.write("\n\n")
            for kind, text in blocks:
                if kind == "h":
                    f.write("\n" + text.upper() + "\n\n")
                else:
                    f.write(text + "\n\n")
