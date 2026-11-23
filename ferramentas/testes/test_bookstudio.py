"""Testes do BookStudio: menus com setas (teclas simuladas), estimativa de tempo e tradução em faixas paralelas."""
import asyncio

import pytest


def _teclado(monkeypatch, teclas):
    """Faz os menus do questionary lerem as teclas dadas, sem terminal de verdade."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    import questionary

    from bookstudio import ui

    monkeypatch.setattr(ui, "interativo", lambda: True)
    entradas = []
    for nome in ("select", "text", "confirm"):
        original = getattr(questionary, nome)

        def envolver(*a, _orig=original, **k):
            ctx = create_pipe_input()
            entrada = ctx.__enter__()
            entradas.append(ctx)
            entrada.send_text(teclas.pop(0))
            return _orig(*a, input=entrada, output=DummyOutput(), **k)
        monkeypatch.setattr(questionary, nome, envolver)
    return entradas


def test_menus_com_setas(monkeypatch):
    from bookstudio import i18n, ui
    i18n.configurar("pt")
    seta_baixo, enter = "\x1b[B", "\r"
    _teclado(monkeypatch, [seta_baixo + enter, "texto novo" + enter, "n", seta_baixo + seta_baixo + enter, "3" + enter])
    assert ui.menu("Escolha", [("Um", 1), ("Dois", 2, "detalhe"), ("Três", 3)]) == 2
    assert ui.perguntar("Nome", "") == "texto novo"
    assert ui.sim_nao("Seguir?", padrao=True) is False
    # escolher() com uma ação extra que pede argumento ("<n>  ouvir a voz n")
    assert ui.escolher("Voz", [("A", ""), ("B", "")], extras={"o": "<n>  ouvir a voz n"}) == ("o", "3")


def test_menu_esc_cancela(monkeypatch):
    from bookstudio import i18n, ui
    i18n.configurar("pt")
    _teclado(monkeypatch, ["\x03"])  # Ctrl+C no menu
    with pytest.raises(ui.Cancelado):
        ui.menu("Escolha", [("Um", 1), ("Dois", 2)])


def test_estimativa_de_tempo_anda_em_tempo_real():
    from bookstudio.ui import EstimativaTempo
    e = EstimativaTempo()
    for s in range(0, 61, 5):  # 1% por segundo
        e.registrar(float(s), agora=1000.0 + s)
    restante = e.agora_restante(1060.0)
    assert 30 <= restante <= 50  # ~40 s (60% feito a 1%/s)
    assert abs(e.agora_restante(1070.0) - (restante - 10)) < 0.01  # desce de segundo em segundo entre os dados


def test_traducao_em_faixas_paralelas():
    """Capítulo enorme: os lotes vão em faixas paralelas, e todo trecho é traduzido no lugar certo."""
    from bookstudio.ebook.core.model import Book, Chapter
    from bookstudio.ebook.core import xhtml
    from bookstudio.ebook.storage.cache import Store
    from bookstudio.ebook.translate.fake import FakeTranslator
    from bookstudio.ebook.translate.pipeline import translate_book

    pars = "".join(f"<p>Paragraph number {i} of a very long chapter.</p>" for i in range(400))
    corpo = f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head><body>{pars}</body></html>'
    livro = Book(title="T", chapters=[Chapter(path="c.xhtml", data=corpo.encode("utf-8"))])
    tr = FakeTranslator("en", "pt")
    tr.batch_units = 5  # 80 lotes: várias faixas
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(pathlib.Path(tmp) / "c.sqlite3")
        rel = asyncio.run(translate_book(livro, tr, store, "en", "pt"))
        store.close()
    assert rel.translated == 400 and rel.untranslated == 0
    textos = [u.plain for u in xhtml.collect_units(xhtml.parse(livro.chapters[0].data))]
    assert len(textos) == 400 and all(str(i) in textos[i] for i in range(400))
