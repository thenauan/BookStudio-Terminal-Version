import asyncio
import zipfile

import pytest

from bookstudio.ebook.core import formats
from bookstudio.ebook.core.readers.epub import read_epub
from bookstudio.ebook.storage.cache import Store
from bookstudio.ebook.translate.base import BudgetExceeded
from bookstudio.ebook.translate.fake import FakeTranslator
from bookstudio.ebook.translate.pipeline import estimate, translate_book


def _run(book, tr, store):
    return asyncio.run(translate_book(book, tr, store, "en", "pt-BR"))


def test_translates_keeps_markup_and_resources(tmp_path, sample_epub):
    store = Store(tmp_path / "c.db")
    book = read_epub(sample_epub)
    report = _run(book, FakeTranslator(), store)
    assert report.untranslated == 0 and report.translated == report.total_units
    out = tmp_path / "out.epub"
    formats.write_book(book, out, tmp_path)
    with zipfile.ZipFile(out) as z:
        c1 = z.read("OPS/c1.xhtml").decode()
        assert "<em>BEST</em>" in c1 and '<a href="#n1">WORST</a>' in c1
        assert 'src="img/x.png"' in c1 and 'lang="pt-BR"' in c1
        assert z.read("OPS/img/x.png") == b"\x89PNG fake"
        assert "CHAPTER ONE" in z.read("OPS/nav.xhtml").decode()
        assert "CHAPTER ONE" in z.read("OPS/toc.ncx").decode()
        assert "<dc:language>pt-BR</dc:language>" in z.read("OPS/content.opf").decode()


def test_resume_uses_cache_after_budget_pause(tmp_path, sample_epub):
    store = Store(tmp_path / "c.db")

    class Limited(FakeTranslator):
        batch_units = 1  # um trecho por pedido

        async def translate_batch(self, items, context=""):
            if self.calls >= 3:
                raise BudgetExceeded("teto")
            return await super().translate_batch(items, context)

    book = read_epub(sample_epub)
    with pytest.raises(BudgetExceeded):
        _run(book, Limited(parallel=1), store)
    assert store.count() == 3

    tr = FakeTranslator(parallel=1)
    tr.batch_units = 1
    book = read_epub(sample_epub)
    est = estimate(book, tr, store, "en", "pt-BR")
    # títulos repetidos (capítulo, nav, ncx) também aproveitam o cache
    assert est.pending_units <= est.total_units - 3
    report = _run(book, tr, store)
    assert report.from_cache >= 3 and report.untranslated == 0
    assert tr.calls <= est.pending_units


def test_missing_paragraphs_are_retried(tmp_path, sample_epub):
    store = Store(tmp_path / "c.db")
    tr = FakeTranslator(fail_every=2)  # a cada 2 pedidos, um parágrafo some da resposta
    report = _run(read_epub(sample_epub), tr, store)
    assert report.untranslated == 0


def test_google_batch_split(monkeypatch):
    from bookstudio.ebook.translate import google

    tr = google.GoogleFreeTranslator("en", "pt", parallel=1)
    monkeypatch.setattr(google, "PAUSE", 0)
    calls = []

    def fake_translate(text, source="en", target="pt"):
        calls.append(text)
        return text.replace("Hello", "Olá").replace("[[§2]]", "[[ § 2 ]]")

    monkeypatch.setattr(google, "_direto", fake_translate)  # pedido direto (POST) ao Google
    out = asyncio.run(tr.translate_batch({"1": "Hello one", "2": "Hello two"}))
    assert out == {"1": "Olá one", "2": "Olá two"} and len(calls) == 1

    long = "Hello world. " * 400
    out = asyncio.run(tr.translate_batch({"1": long}))
    assert out["1"].count("Olá") == 400 and all(len(c) <= google.MAX_CHARS for c in calls[1:])


def test_google_reserva_em_pedacos(monkeypatch):
    """Pedido direto fora do ar: o deep-translator recebe pedaços que cabem na URL, cortados entre parágrafos."""
    from bookstudio.ebook.translate import google

    tr = google.GoogleFreeTranslator("en", "pt", parallel=1)
    monkeypatch.setattr(google, "PAUSE", 0)
    monkeypatch.setattr(google, "_direto", lambda *a: (_ for _ in ()).throw(OSError("fora do ar")))
    pedacos = []

    class Falso:
        def translate(self, text):
            pedacos.append(text)
            return text.replace("Hello", "Olá")

    tr._client = Falso()
    itens = {str(i): "Hello there, this is paragraph number %d of the book." % i for i in range(1, 61)}
    out = asyncio.run(tr.translate_batch(itens))
    assert all(v.startswith("Olá") for v in out.values()) and len(out) == 60
    assert len(pedacos) > 1 and all(len(p) <= google.MAX_CHARS_URL for p in pedacos)
