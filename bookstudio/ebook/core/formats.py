"""Registro de formatos, Calibre e leitura/escrita com gravação atômica."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .model import Book, CalibreMissingError, EbookError
from .readers.epub import read_epub
from .readers.pdf import read_pdf
from .readers.txt import read_txt
from .writers.epub import write_epub
from .writers.pdf import write_pdf_basic
from .writers.txt import write_txt

FORMAT_NAMES = {
    "epub": "EPUB",
    "pdf": "PDF",
    "txt": "TXT (texto puro)",
    "mobi": "MOBI (Kindle antigo)",
    "azw3": "AZW3 (Kindle)",
    "azw": "AZW (Kindle)",
    "docx": "DOCX (Word)",
    "fb2": "FB2",
    "rtf": "RTF",
    "odt": "ODT (LibreOffice)",
}
NATIVE_READ = {"epub", "txt", "pdf"}
NATIVE_WRITE = {"epub", "txt", "pdf"}
CALIBRE_READ = {"mobi", "azw", "azw3", "docx", "fb2", "rtf", "odt"}
CALIBRE_WRITE = {"mobi", "azw3", "docx", "fb2", "rtf", "odt", "pdf"}
WRITE_ORDER = ["epub", "pdf", "txt", "azw3", "mobi", "docx", "fb2", "rtf", "odt"]
CALIBRE_TIMEOUT = 1800  # segundos

_calibre_cache: list[str | None] = []


def find_calibre() -> str | None:
    """Caminho do ``ebook-convert`` ou ``None``."""

    if _calibre_cache:
        return _calibre_cache[0]
    found = shutil.which("ebook-convert")
    if not found:
        candidates = []
        if sys.platform == "win32":
            for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files"):
                if base:
                    candidates.append(Path(base) / "Calibre2" / "ebook-convert.exe")
                    candidates.append(Path(base) / "Calibre" / "ebook-convert.exe")
        elif sys.platform == "darwin":
            candidates.append(Path("/Applications/calibre.app/Contents/MacOS/ebook-convert"))
        else:
            candidates.append(Path("/opt/calibre/ebook-convert"))
        found = next((str(c) for c in candidates if c.exists()), None)
    _calibre_cache.append(found)
    return found


def ext_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def can_read(fmt: str) -> bool:
    return fmt in NATIVE_READ or (fmt in CALIBRE_READ and find_calibre() is not None)


def can_write(fmt: str) -> bool:
    return fmt in NATIVE_WRITE or (fmt in CALIBRE_WRITE and find_calibre() is not None)


def readable_formats() -> set[str]:
    return NATIVE_READ | CALIBRE_READ


def run_calibre(src: Path, dst: Path) -> None:
    exe = find_calibre()
    if not exe:
        raise CalibreMissingError(
            "Este formato precisa do Calibre. Instale em https://calibre-ebook.com e rode o instalar.bat de novo."
        )
    try:
        proc = subprocess.run(
            [exe, str(src), str(dst)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CALIBRE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise EbookError("O Calibre demorou demais e a conversão foi cancelada.") from e
    if proc.returncode != 0 or not dst.exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        msg = " ".join(tail)
        if "DRM" in msg:
            raise EbookError("O arquivo tem DRM (proteção contra cópia) e não pode ser convertido.")
        raise EbookError(f"O Calibre não conseguiu converter o arquivo. {msg}".strip())


def read_book(path: Path, workdir: Path) -> Book:
    """Lê qualquer formato suportado. ``workdir`` guarda arquivos temporários
    (como o EPUB gerado pelo Calibre) e precisa existir até a escrita."""

    fmt = ext_of(path)
    if fmt == "epub":
        return read_epub(path)
    if fmt == "txt":
        return read_txt(path)
    if fmt == "pdf":
        return read_pdf(path)
    if fmt in CALIBRE_READ:
        tmp = workdir / f"entrada-{uuid.uuid4().hex[:8]}.epub"
        run_calibre(path, tmp)
        book = read_epub(tmp)
        if book.title == tmp.stem:
            book.title = path.stem
        return book
    raise EbookError(f"Formato .{fmt} não suportado.")


def _replace_with_retry(src: Path, dst: Path) -> None:
    # No Windows, antivírus às vezes trava arquivos recém-criados por um instante.
    for attempt in range(6):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.5 * (attempt + 1))


META_ONLY = {"epub", "pdf", "mobi", "azw", "azw3", "docx", "fb2", "rtf", "odt"}


def can_edit_metadata(fmt: str) -> bool:
    return fmt in ("epub", "pdf") or (fmt in META_ONLY and find_calibre() is not None)


def _ebook_meta() -> str | None:
    exe = find_calibre()
    if not exe:
        return None
    p = Path(exe)
    cand = p.with_name("ebook-meta" + p.suffix)
    return str(cand) if cand.exists() else shutil.which("ebook-meta")


def save_metadata(book: Book, src: Path, out: Path, workdir: Path) -> None:
    """Grava só os metadados editados, sem reconverter o conteúdo (nada se perde).
    ``out`` pode ser o próprio ``src``: a troca é atômica."""
    fmt = ext_of(src)
    if fmt == "epub":
        write_book(book, out, workdir)  # copia o EPUB e só reescreve o content.opf
        return
    tmp = out.with_name(f".{out.stem}.{uuid.uuid4().hex[:8]}.tmp.{fmt}")
    try:
        if fmt == "pdf":
            import pymupdf

            doc = pymupdf.open(src)
            try:
                meta = dict(doc.metadata or {})
                if "title" in book.edited:
                    meta["title"] = book.title
                if "authors" in book.edited:
                    meta["author"] = ", ".join(book.authors)
                if "subjects" in book.edited:
                    meta["keywords"] = ", ".join(book.subjects)
                if "description" in book.edited:
                    meta["subject"] = book.description.split("\n")[0][:300]
                doc.set_metadata(meta)
                doc.save(tmp, garbage=1, deflate=True)
            finally:
                doc.close()
        else:
            exe = _ebook_meta()
            if not exe:
                raise CalibreMissingError("Editar metadados deste formato precisa do Calibre.")
            shutil.copy2(src, tmp)
            args = [exe, str(tmp)]
            e = book.edited
            if "title" in e:
                args += ["--title", book.title]
            if "authors" in e:
                args += ["--authors", " & ".join(book.authors)]
            if "publisher" in e:
                args += ["--publisher", book.publisher]
            if "year" in e:
                args += ["--date", f"{book.year}-01-01" if book.year else ""]
            if "subjects" in e:
                args += ["--tags", ", ".join(book.subjects)]
            if "series" in e or "series_index" in e:
                args += ["--series", book.series]
                if book.series_index:
                    args += ["--index", book.series_index]
            if "isbn" in e:
                args += ["--isbn", book.isbn]
            if "language" in e:
                args += ["--language", book.language]
            if "description" in e:
                args += ["--comments", book.description]
            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=300)
            if proc.returncode != 0:
                tail = " ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-2:])
                raise EbookError(f"O Calibre não conseguiu gravar os metadados. {tail}".strip())
        _replace_with_retry(tmp, out)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_book(book: Book, out: Path, workdir: Path) -> None:
    """Escreve no formato da extensão de ``out``. A escrita é atômica: grava num
    arquivo temporário na mesma pasta e só renomeia no fim."""

    fmt = ext_of(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.{uuid.uuid4().hex[:8]}.tmp.{fmt}")
    try:
        if fmt == "epub":
            write_epub(book, tmp)
        elif fmt == "txt":
            write_txt(book, tmp)
        elif fmt in CALIBRE_WRITE and find_calibre():
            epub = workdir / f"saida-{uuid.uuid4().hex[:8]}.epub"
            write_epub(book, epub)
            run_calibre(epub, tmp)
        elif fmt == "pdf":
            write_pdf_basic(book, tmp)
        elif fmt in CALIBRE_WRITE:
            run_calibre(Path("x.epub"), tmp)  # dispara a mensagem de Calibre ausente
        else:
            raise EbookError(f"Não sei escrever o formato .{fmt}.")
        _replace_with_retry(tmp, out)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
