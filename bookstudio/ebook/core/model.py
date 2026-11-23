"""Modelo interno do livro.

Todo formato é lido para um ``Book``: uma lista de capítulos em XHTML. A tradução
trabalha só sobre esse modelo, e os escritores geram qualquer formato a partir dele.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class EbookError(Exception):
    """Erro com mensagem amigável para mostrar ao usuário."""


class DRMError(EbookError):
    pass


class ScannedPDFError(EbookError):
    pass


class CalibreMissingError(EbookError):
    pass


@dataclass
class Chapter:
    """Um documento do livro.

    ``kind``:
      - ``"content"``: capítulo XHTML do texto principal;
      - ``"nav"``: sumário EPUB3 (nav.xhtml);
      - ``"ncx"``: sumário EPUB2 (toc.ncx).
    """

    path: str
    data: bytes
    title: str = ""
    kind: str = "content"


@dataclass
class Book:
    title: str = "Sem título"
    authors: list[str] = field(default_factory=list)
    language: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    # Quando o livro veio de um EPUB (direto ou via Calibre), guardamos o arquivo
    # para copiar imagens, estilos e metadados intactos ao escrever.
    source_epub: Path | None = None
    opf_path: str | None = None
    # Idioma de destino aplicado pela tradução (atualiza metadados na escrita).
    target_language: str | None = None
    warnings: list[str] = field(default_factory=list)
    # metadados extras (lidos do OPF quando existem; editáveis pelo usuário)
    publisher: str = ""
    year: str = ""
    description: str = ""
    subjects: list[str] = field(default_factory=list)
    series: str = ""
    series_index: str = ""
    isbn: str = ""
    #: campos de metadados que o usuário editou (só esses são regravados num EPUB copiado)
    edited: set[str] = field(default_factory=set)

    def metadata(self):
        from .metadata import Metadata

        return Metadata(title=self.title, authors=list(self.authors), language=self.language,
                        publisher=self.publisher, year=self.year, description=self.description,
                        subjects=list(self.subjects), series=self.series,
                        series_index=self.series_index, isbn=self.isbn)

    def set_metadata(self, values: dict, mark_edited: bool = True) -> None:
        for k, v in values.items():
            if k in ("title", "authors", "language", "publisher", "year", "description",
                     "subjects", "series", "series_index", "isbn"):
                setattr(self, k, list(v) if isinstance(v, list) else v)
                if mark_edited:
                    self.edited.add(k)

    def edited_metadata(self) -> dict:
        """Só os campos editados (para guardar no trabalho e reaplicar ao retomar)."""
        md = self.metadata().to_dict()
        return {k: md[k] for k in sorted(self.edited)}

    @property
    def content_chapters(self) -> list[Chapter]:
        return [c for c in self.chapters if c.kind == "content"]
