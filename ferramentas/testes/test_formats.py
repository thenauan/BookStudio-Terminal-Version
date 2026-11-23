import zipfile

import pytest

from bookstudio.ebook.core import formats
from bookstudio.ebook.core.model import DRMError, EbookError
from bookstudio.ebook.core.readers.epub import read_epub
from bookstudio.ebook.core.xhtml import chapter_blocks

TXT = "Meu Livro\n\nCapítulo 1\n\nAção e coração na estação.\nLinha quebrada.\n\nCapítulo 2\n\nOutro parágrafo aqui.\n"


def test_txt_to_epub_to_txt(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes(TXT.encode("cp1252"))
    book = formats.read_book(src, tmp_path)
    assert [c.title for c in book.chapters] == ["Início", "Capítulo 1", "Capítulo 2"]
    out = tmp_path / "a.epub"
    formats.write_book(book, out, tmp_path)
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    book2 = formats.read_book(out, tmp_path)
    txt = tmp_path / "b.txt"
    formats.write_book(book2, txt, tmp_path)
    content = txt.read_text(encoding="utf-8")
    assert "Ação e coração na estação. Linha quebrada." in content
    assert "CAPÍTULO 2" in content


def test_epub_reader(sample_epub):
    book = read_epub(sample_epub)
    assert book.title == "My Book" and book.authors == ["Jane Doe"] and book.language == "en"
    kinds = [(c.path, c.kind) for c in book.chapters]
    assert ("OPS/c1.xhtml", "content") in kinds and ("OPS/nav.xhtml", "nav") in kinds
    assert ("OPS/toc.ncx", "ncx") in kinds
    assert chapter_blocks(book.chapters[0].data)[0] == ("h", "Chapter One")


def test_pdf_roundtrip(tmp_path):
    src = tmp_path / "a.txt"
    body = "\n\n".join(["Um parágrafo razoavelmente longo com acentuação: João, ação, coração."] * 40)
    src.write_text(f"Capítulo 1\n\n{body}\n\nCapítulo 2\n\n{body}\n", encoding="utf-8")
    book = formats.read_book(src, tmp_path)
    pdf = tmp_path / "a.pdf"
    formats.write_book(book, pdf, tmp_path)
    back = formats.read_book(pdf, tmp_path)
    titles = [c.title for c in back.chapters]
    assert "Capítulo 1" in titles and "Capítulo 2" in titles
    text = " ".join(t for c in back.chapters for _, t in chapter_blocks(c.data))
    assert "João, ação, coração." in text


def test_drm_is_detected(tmp_path, sample_epub):
    drm = tmp_path / "drm.epub"
    with zipfile.ZipFile(sample_epub) as zin, zipfile.ZipFile(drm, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info))
        zout.writestr("META-INF/encryption.xml", """<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></enc:EncryptedData></encryption>""")
    with pytest.raises(DRMError):
        read_epub(drm)


def test_atomic_write_keeps_old_file_on_error(tmp_path, sample_epub, monkeypatch):
    book = read_epub(sample_epub)
    out = tmp_path / "out.epub"
    out.write_bytes(b"old")

    def boom(*a, **k):
        raise EbookError("falhou")

    monkeypatch.setattr(formats, "write_epub", boom)
    with pytest.raises(EbookError):
        formats.write_book(book, out, tmp_path)
    assert out.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir() if ".tmp." in p.name] == []


def test_missing_calibre_message(tmp_path, monkeypatch):
    monkeypatch.setattr(formats, "find_calibre", lambda: None)
    assert not formats.can_read("mobi") and not formats.can_write("azw3")
    assert formats.can_write("pdf")  # PDF simples sem Calibre
