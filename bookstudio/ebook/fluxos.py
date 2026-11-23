"""E-books no BookStudio: converter o formato, traduzir (no mesmo formato ou em outro), editar os metadados,
configurar a tradução e continuar traduções pausadas.

Cada fluxo devolve o arquivo gravado (ou None), para outros fluxos continuarem dele: traduzir e criar audiobook,
traduzir e gerar texto sincronizado.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from bookstudio import ui
from bookstudio.ebook import languages
from bookstudio.ebook.core import formats
from bookstudio.ebook.core.model import Book, EbookError
from bookstudio.ebook.core.xhtml import chapter_blocks
from bookstudio.ebook.settings import (LOG_DIR, Settings, delete_key, get_saved_key, load_providers, mask, save_key)
from bookstudio.ebook.storage.cache import Store
from bookstudio.ebook.translate.base import BudgetExceeded, FatalTranslateError, Translator
from bookstudio.i18n import t
from bookstudio.idiomas import IDIOMAS, nome_idioma, nome_regiao

log = logging.getLogger(__name__)

CONVERTER, TRADUZIR, METADADOS = "converter", "traduzir", "metadados"


# ------------------------------------------------------------------- idiomas
def nome_lang(codigo: str) -> str:
    """Nome do idioma (e da região) na língua da interface: "pt-BR" -> "Português (Brasil)"."""
    base, _, regiao = (codigo or "").partition("-")
    lang = languages.get(codigo)
    nome = nome_idioma(base.lower()) if base.lower() in IDIOMAS else (lang.name if lang else codigo)
    return f"{nome} ({nome_regiao(regiao)})" if regiao else nome


def rotulo_lang(lang: languages.Lang) -> str:
    return f"{lang.flag} {nome_lang(lang.code)}" if ui.EMOJI else nome_lang(lang.code)


# ------------------------------------------------------------------- arquivos
def caminho_limpo(bruto: str) -> Path:
    """Aceita caminhos colados ou arrastados (com aspas ou o ``& '...'`` do PowerShell)."""
    s = re.sub(r"^&\s*", "", (bruto or "").strip()).strip().strip('"').strip("'").strip()
    return Path(os.path.expandvars(os.path.expanduser(s)))


def _dialogo(salvar: bool, inicial: Path | None = None, fmt: str | None = None, tipos=None) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        raiz = tk.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        if salvar:
            ftipos = [(formats.FORMAT_NAMES.get(fmt, fmt), f"*.{fmt}")] if fmt else []
            nome = filedialog.asksaveasfilename(
                title=t("eb_where_save"), initialdir=str(inicial.parent) if inicial else None,
                initialfile=inicial.name if inicial else None, defaultextension=f".{fmt}" if fmt else "",
                filetypes=ftipos + [(t("filetype_all"), "*.*")])
        else:
            exts = " ".join(f"*.{e}" for e in sorted(formats.readable_formats()))
            nome = filedialog.askopenfilename(title=t("eb_choose_file"),
                                              filetypes=tipos or [(t("filetype_ebooks"), exts), (t("filetype_all"), "*.*")])
        raiz.destroy()
        return Path(nome) if nome else None
    except Exception as e:  # noqa: BLE001 - sem tela gráfica
        log.info("diálogo indisponível: %s", e)
        return None


def _erro_entrada(p: Path) -> str | None:
    if not p.is_file():
        return t("file_not_found", path=p)
    fmt = formats.ext_of(p)
    if fmt not in formats.readable_formats():
        return t("eb_format_unsupported", format=fmt)
    if not formats.can_read(fmt):
        return t("eb_needs_calibre_read", format=fmt)
    return None


def escolher_ebook(titulo: str | None = None) -> Path:
    """Um e-book: seletor do sistema ou caminho digitado/arrastado. Esc volta (ui.Cancelado)."""
    while True:
        como = ui.menu(titulo or t("eb_choose_file"), [(f"{ui.ICONE['pasta']} {t('eb_browse')}", "browse"),
                                                       (f"✏️ {t('eb_type_path')}" if ui.EMOJI else t("eb_type_path"), "type")])
        if como == "browse":
            p = _dialogo(False)
            if p is None:
                ui.alerta(t("eb_no_dialog"))
                continue
        else:
            p = caminho_limpo(ui.perguntar(t("prompt_file")))
        erro = _erro_entrada(p)
        if erro:
            ui.falha(erro)
            continue
        return p


def escolher_saida(src: Path, fmt: str, sufixo: str, sobrescrever_ok: bool = False) -> Path:
    padrao = src.with_name(f"{src.stem}{sufixo}.{fmt}")
    if sobrescrever_ok and formats.ext_of(src) == fmt:
        if ui.menu(t("eb_where_save"), [(t("eb_over_original", name=src.name), "same"), (t("eb_as_copy"), "copy")]) == "same":
            return src
    while True:
        como = ui.menu(t("eb_where_save"), [(t("eb_save_at", path=padrao), "default"), (t("eb_type_other"), "type"),
                                            (t("eb_browse"), "browse")])
        if como == "default":
            saida = padrao
        elif como == "browse":
            p = _dialogo(True, padrao, fmt)
            if p is None:
                ui.alerta(t("eb_no_dialog"))
                continue
            saida = p
        else:
            saida = caminho_limpo(ui.perguntar(t("eb_output_path"), str(padrao)))
            if saida.is_dir():
                saida = saida / padrao.name
        if saida.suffix.lower() != f".{fmt}":
            saida = saida.with_suffix(f".{fmt}")
        try:
            if saida.resolve() == src.resolve():
                ui.falha(t("eb_output_is_source"))
                continue
        except OSError:
            pass
        if not saida.parent.exists():
            ui.falha(t("eb_folder_missing"))
            continue
        if saida.exists() and not ui.sim_nao(t("eb_replace_q", name=saida.name), padrao=False):
            continue
        return saida


# ------------------------------------------------------------------- idiomas do livro
def escolher_origem(book: Book) -> str:
    from bookstudio.ebook.core.detect import detect_language

    lang, conf = ui.aguarde(t("eb_detecting_language"), detect_language, book)
    if lang:
        quanto = f"{conf:.0%}" if conf else t("eb_by_metadata")
        ui.info(t("eb_language_detected", language=nome_lang(lang.code), confidence=quanto))
        if conf >= 0.6:
            if ui.menu(t("eb_language_right_q"), [(t("eb_yes_it_is", language=nome_lang(lang.code)), True),
                                                  (t("eb_change"), False)]):
                return lang.code
        else:
            ui.alerta(t("eb_low_confidence"))
    opcoes = [(rotulo_lang(lg), lg.code) for lg in languages.SOURCE_LANGS]
    return ui.menu(t("eb_source_language"), opcoes, padrao=lang.code if lang else None, busca=True)


def escolher_destino(settings: Settings, origem: str, rotulo: str | None = None) -> str:
    recentes = [c for c in settings.recent_targets if c in languages.BY_CODE]
    ordem = [languages.BY_CODE[c] for c in recentes] + [lg for lg in languages.TARGET_LANGS if lg.code not in recentes]
    opcoes = [(rotulo_lang(lg), lg.code) for lg in ordem if lg.target]
    padrao = settings.target_lang if settings.target_lang in languages.BY_CODE else None
    while True:
        codigo = ui.menu(rotulo or t("eb_translate_to"), opcoes, padrao=padrao, busca=True)
        if languages.same_language(codigo, origem) and not ui.sim_nao(t("eb_same_language_q"), padrao=False):
            continue
        return codigo


# ------------------------------------------------------------------- motores de tradução
def _modelos_ollama(api_base: str) -> list[str]:
    import httpx

    try:
        r = httpx.get(api_base.rstrip("/") + "/api/tags", timeout=3)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def _chave(provedor: str, prov: dict) -> tuple[str | None, bool]:
    """(chave, foi digitada agora)."""
    if prov.get("sem_chave"):
        return None, False
    env = prov.get("env")
    if env and os.environ.get(env):
        ui.info(t("eb_key_from_env", var=env))
        return os.environ[env], False
    salva = get_saved_key(provedor)
    if salva and ui.menu(t("eb_api_key"), [(t("eb_use_saved_key", masked=mask(salva)), True), (t("eb_paste_other_key"), False)]):
        return salva, False
    if prov.get("site_chave"):
        ui.dica(t("eb_get_key_at", url=prov["site_chave"]))
    opcional = provedor == "custom"
    rotulo = t("eb_paste_key_optional") if opcional else t("eb_paste_key")
    if ui.interativo():
        import questionary

        chave = ui._ask(questionary.password(rotulo, style=ui.ESTILO, qmark="›",
                                             validate=(lambda s: True) if opcional else (lambda s: bool(s.strip()) or t("eb_key_empty")))).strip()
    else:
        chave = ui.perguntar(rotulo).strip()
    return chave or None, bool(chave)


def escolher_ia(settings: Settings, origem: str, destino: str) -> Translator:
    from bookstudio import dependencias
    if not dependencias.opcional("litellm", lambda: (ui.alerta(t("eb_needs_litellm")), ui.sim_nao(t("sy_install_whisper_q"), padrao=True))[1]):
        raise ui.Cancelado
    from bookstudio.ebook.translate.llm import LLMTranslator

    provedores = load_providers()
    while True:
        pid = ui.menu(t("eb_ai_provider"), [(p["nome"], k) for k, p in provedores.items()],
                      padrao=settings.provider if settings.provider in provedores else None)
        prov = provedores[pid]
        api_base = prov.get("api_base", "")
        if prov.get("pede_url"):
            api_base = ui.perguntar(t("eb_api_base_prompt"), settings.api_base or "")
        if pid == "ollama":
            nomes = ui.aguarde(t("eb_looking_ollama"), _modelos_ollama, api_base)
            if not nomes:
                ui.alerta(t("eb_no_ollama_models"))
            modelos = [{"id": f"ollama/{n}", "nome": n} for n in nomes]
        else:
            modelos = prov.get("modelos", [])
        opcoes = [(m["nome"] + (f"  ⭐ {t('eb_recommended')}" if m.get("recomendado") and ui.EMOJI else
                                 f"  * {t('eb_recommended')}" if m.get("recomendado") else ""), m["id"]) for m in modelos]
        opcoes += [(t("eb_type_model"), "__type__"), (t("back"), "__back__")]
        modelo = ui.menu(t("eb_model"), opcoes, padrao=settings.model if any(m["id"] == settings.model for m in modelos) else None)
        if modelo == "__back__":
            continue
        if modelo == "__type__":
            prefixo = prov.get("prefixo") or (f"{pid}/" if pid != "custom" else "openai/")
            modelo = ui.perguntar(t("eb_model_name_prompt", example=f"{prefixo}..."), prefixo).strip()
        chave, digitada = _chave(pid, prov)
        tr = LLMTranslator(origem, destino, modelo, chave, api_base or None, parallel=settings.llm_parallel, provider=pid)
        try:
            ui.aguarde(t("eb_testing_ai"), tr.test)
        except FatalTranslateError as e:
            ui.falha(str(e))
            if ui.sim_nao(t("eb_try_other_q"), padrao=True):
                continue
            raise ui.Cancelado from e
        ui.ok(t("eb_connection_ok", model=modelo))
        if digitada and chave and ui.sim_nao(t("eb_save_key_q"), padrao=True):
            if save_key(pid, chave):
                ui.ok(t("eb_key_saved"))
            else:
                ui.alerta(t("eb_key_not_saved"))
        settings.engine, settings.provider, settings.model, settings.api_base = "llm", pid, modelo, api_base or ""
        settings.save()
        tr.max_cost_usd = settings.max_cost_usd
        return tr


def google(settings: Settings, origem: str, destino: str) -> Translator:
    from bookstudio.ebook.translate.google import GoogleFreeTranslator

    src, tgt = languages.get(origem), languages.get(destino)
    return GoogleFreeTranslator(src.google if src else "auto", tgt.google if tgt else destino, parallel=settings.google_parallel)


def _motor_teste_ligado() -> bool:
    return bool(os.environ.get("BOOKSTUDIO_MOTOR_TESTE") or os.environ.get("EBOOKTR_MOTOR_TESTE"))


def escolher_motor(settings: Settings, origem: str, destino: str) -> Translator:
    opcoes = [(f"🤖 {t('eb_engine_ai')}" if ui.EMOJI else t("eb_engine_ai"), "llm", t("eb_engine_ai_desc")),
              (f"🌐 {t('eb_engine_google')}" if ui.EMOJI else t("eb_engine_google"), "google", t("eb_engine_google_desc"))]
    if _motor_teste_ligado():
        opcoes.append((t("eb_engine_test"), "fake"))
    motor = ui.menu(t("eb_how_translate"), opcoes, padrao=settings.engine)
    if motor == "fake":
        from bookstudio.ebook.translate.fake import FakeTranslator

        return FakeTranslator(origem, destino)
    if motor == "google":
        settings.engine = "google"
        settings.save()
        return google(settings, origem, destino)
    return escolher_ia(settings, origem, destino)


# ------------------------------------------------------------------- livro e metadados
def resumo_livro(book: Book, caminho: Path) -> None:
    palavras = sum(len(x.split()) for ch in book.content_chapters for _, x in chapter_blocks(ch.data))
    linhas = [(t("field_author"), ", ".join(book.authors)) if book.authors else None,
              (t("eb_format"), formats.FORMAT_NAMES.get(formats.ext_of(caminho), caminho.suffix)),
              (t("field_chapters"), str(len(book.content_chapters))),
              (t("eb_words"), f"{palavras:,}".replace(",", "."))]
    ui.cartao(book.title or caminho.name, [l for l in linhas if l])
    for aviso in book.warnings:
        ui.alerta(aviso)


CAMPOS_META = ["title", "authors", "series", "series_index", "publisher", "year", "subjects", "isbn", "language", "description"]


def _valor_meta(md: dict, campo: str) -> str:
    v = md.get(campo)
    if campo == "authors":
        return "; ".join(v or [])
    if campo == "subjects":
        return ", ".join(v or [])
    if campo == "language" and v:
        return f"{nome_lang(v)} ({v})"
    return v or ""


def _isbn_ok(bruto: str) -> bool:
    s = re.sub(r"[^0-9Xx]", "", bruto).upper()
    if len(s) == 13 and s.isdigit():
        return sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s)) % 10 == 0
    if len(s) == 10 and s[:9].isdigit() and (s[9].isdigit() or s[9] == "X"):
        return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s)) % 11 == 0
    return False


def _perguntar_campo(campo: str, atual):
    rotulo = t(f"eb_meta_{campo}")
    if campo == "language":
        opcoes = [(t("eb_type_language_code"), "__type__")] + [(rotulo_lang(lg), lg.code) for lg in languages.LANGS]
        codigo = ui.menu(rotulo, opcoes, busca=True)
        if codigo == "__type__":
            codigo = ui.perguntar(t("eb_language_code_prompt"), atual or "").strip()
            if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", codigo):
                ui.alerta(t("eb_language_code_invalid"))
                return atual
        return codigo
    mostrado = _valor_meta({campo: atual}, campo) if campo in ("authors", "subjects") else (atual or "")
    dica = t(f"eb_meta_{campo}_hint") if campo in ("authors", "series", "series_index", "year", "subjects") else ""
    while True:
        valor = ui.perguntar(f"{rotulo}{f' ({dica})' if dica else ''}:", mostrado) if campo != "description" else \
            ui.perguntar(t("eb_meta_description_prompt"), mostrado)
        v = valor.strip()
        if campo == "year" and v and not re.fullmatch(r"\d{4}", v):
            ui.alerta(t("eb_year_invalid"))
            continue
        if campo == "series_index" and v and not re.fullmatch(r"\d+([.,]\d+)?", v):
            ui.alerta(t("eb_number_invalid"))
            continue
        if campo == "isbn" and v and not _isbn_ok(v):
            ui.alerta(t("eb_isbn_invalid"))
            continue
        if campo == "title" and not v:
            ui.alerta(t("eb_title_empty"))
            continue
        break
    if campo == "series_index":
        v = v.replace(",", ".")
    if campo == "isbn":
        v = re.sub(r"[^0-9Xx]", "", v).upper()
    if campo != "description":
        v = " ".join(v.split())
    if campo == "authors":
        return [a.strip() for a in re.split(r";|\s&\s", v) if a.strip()]
    if campo == "subjects":
        vistos: list[str] = []
        for s in v.split(","):
            if s.strip() and s.strip().lower() not in (x.lower() for x in vistos):
                vistos.append(s.strip())
        return vistos
    return v


def editar_metadados(book: Book, traduzindo_para: str = "") -> bool:
    """Editor de metadados (título, autor, série, sinopse...). True se algo mudou."""
    original = book.metadata().to_dict()
    md = dict(original)
    mudou: set[str] = set()
    if traduzindo_para:
        ui.dica(t("eb_meta_translate_tip", language=nome_lang(traduzindo_para)))
    while True:
        ui.cartao(t("eb_metadata"), [(t(f"eb_meta_{c}") + (" ✎" if c in mudou else ""),
                                      (_valor_meta(md, c)[:160] + "…") if len(_valor_meta(md, c)) > 160 else _valor_meta(md, c))
                                     for c in CAMPOS_META])
        opcoes = [(f"{t(f'eb_meta_{c}')}: {(_valor_meta(md, c) or '—')[:50]}", c) for c in CAMPOS_META]
        opcoes += [(f"{ui.ICONE['ok']} {t('eb_done_editing')}" + (f" ({len(mudou)})" if mudou else ""), "__done__"),
                   (t("eb_discard_changes"), "__discard__")]
        campo = ui.menu(t("eb_which_field"), opcoes, padrao="__done__" if mudou else None)
        if campo == "__discard__":
            return False
        if campo == "__done__":
            break
        try:
            valor = _perguntar_campo(campo, md.get(campo))
        except ui.Cancelado:
            continue  # Esc num campo: volta para a lista
        md[campo] = valor
        (mudou.add if valor != original.get(campo) else mudou.discard)(campo)
    if not mudou:
        return False
    book.set_metadata({c: md[c] for c in mudou})
    ui.ok(t("eb_metadata_updated", fields=", ".join(t(f"eb_meta_{c}") for c in CAMPOS_META if c in mudou)))
    return True


def escolher_formato_saida(modo: str, fmt_origem: str) -> str:
    """Traduzir: o mesmo formato (padrão) ou outro; converter: qualquer outro formato."""
    fmt_igual = {"azw": "azw3"}.get(fmt_origem, fmt_origem)
    opcoes = []
    if modo in (TRADUZIR, METADADOS):
        if formats.can_write(fmt_igual):
            opcoes.append((t("eb_keep_format", format=formats.FORMAT_NAMES.get(fmt_igual, fmt_igual)), fmt_igual))
        elif modo == METADADOS:
            return "epub"
        else:
            ui.alerta(t("eb_no_calibre_epub", format=fmt_igual))
    for fmt in formats.WRITE_ORDER:
        if fmt == fmt_igual:
            continue
        nome = formats.FORMAT_NAMES[fmt]
        if formats.can_write(fmt):
            nota = f"  {t('eb_pdf_simple')}" if fmt == "pdf" and not formats.find_calibre() else ""
            opcoes.append((nome + nota, fmt))
        else:
            opcoes.append((f"{nome}  ({t('eb_needs_calibre')})", f"__x_{fmt}"))
    while True:
        fmt = ui.menu(t("eb_output_format"), opcoes)
        if not fmt.startswith("__x_"):
            return fmt
        ui.alerta(t("eb_needs_calibre_write"))


# ------------------------------------------------------------------- tradução
@dataclass
class Trabalho:
    modo: str
    src: Path
    out: Path
    origem: str = ""
    destino: str = ""


def mostrar_estimativa(tr: Translator, est, settings: Settings) -> None:
    from bookstudio.ebook.translate.llm import LLMTranslator, model_prices

    feitos = est.total_units - est.pending_units
    linhas = [(t("eb_units"), f"{est.total_units:,}".replace(",", ".") + (f"  ({t('eb_in_cache', n=f'{feitos:,}'.replace(',', '.'))})" if feitos else "")),
              (t("eb_to_translate"), t("eb_chars_in_requests", chars=f"{est.pending_chars:,}".replace(",", "."), requests=est.batches))]
    acima = False
    if isinstance(tr, LLMTranslator):
        minutos = max(1, round(est.batches * 12 / max(1, tr.limiter.max) / 60))
        precos = model_prices(tr.model)
        tokens_fixos = 450 + 4 * len(tr.glossary)
        entrada = est.pending_chars / 3.5 + est.batches * (tokens_fixos + 80)
        saida = est.pending_chars / 3.5 * 1.2
        if precos:
            custo = (entrada * precos[0] + saida * precos[1]) * 1.1  # +10% para novas tentativas
            linhas.append((t("eb_estimated_cost"), t("eb_cost_value", cost=f"{custo:.2f}")))
            acima = bool(settings.max_cost_usd and custo > settings.max_cost_usd)
        else:
            linhas.append((t("eb_estimated_cost"), t("eb_price_unknown")))
        if settings.max_cost_usd:
            linhas.append((t("eb_cost_ceiling"), t("eb_ceiling_value", value=f"{settings.max_cost_usd:.2f}")))
    else:
        minutos = max(1, round(est.batches * 3 / max(1, tr.limiter.max) / 60))
        linhas.append((t("eb_cost"), t("eb_free")))
    linhas.append((t("eb_time"), t("eb_about_minutes", n=minutos)))
    ui.cartao(t("step_summary"), linhas)
    if acima:
        ui.alerta(t("eb_over_ceiling"))
        if ui.sim_nao(t("eb_raise_ceiling_q"), padrao=False):
            tr.max_cost_usd = 0


def _milhares(n: float) -> str:
    return f"{n / 1_000_000:.1f} M" if n >= 1_000_000 else f"{n / 1000:.0f} mil" if n >= 1000 else f"{n:.0f}"


def traduzir_com_painel(book: Book, tr: Translator, store: Store, trabalho: Trabalho):
    """Traduz mostrando %, etapa, trechos, velocidade (caracteres por segundo), tamanho e tempo restante, que
    anda a cada segundo. Ctrl+C: continuar, pausar (o que já foi traduzido fica no cache) ou cancelar.
    Devolve (estado, relatório): estado "ok", "pause" ou "cancel"."""
    from bookstudio.ebook.translate.pipeline import count_units, translate_book
    from bookstudio.ebook.core import xhtml as _x

    total = max(1, count_units(book, tr))
    total_chars = sum(len(u.plain) for ch in book.chapters if ch.kind in ("content", "nav", "ncx")
                      for u in (_x.collect_units(_x.parse(ch.data)) if _x.parse(ch.data) is not None else []))
    painel = ui.PainelProgresso([{"id": "tr", "titulo": book.title or trabalho.src.name}])
    estado = {"feitos": 0, "chars": 0, "amostras": [], "report": None, "erro": None, "loop": None, "task": None}

    def ao_progresso(n: int, previa: str) -> None:
        estado["feitos"] += n
        estado["chars"] += len(previa or "") if previa else 0
        agora = time.time()
        estado["amostras"] = [(a, c) for a, c in estado["amostras"] if agora - a <= 20] + [(agora, estado["feitos"])]
        a0 = estado["amostras"][0]
        vel = (estado["feitos"] - a0[1]) / max(1e-6, agora - a0[0]) if len(estado["amostras"]) > 1 else 0
        media_chars = total_chars / total
        pct = min(99.9, estado["feitos"] / total * 100)
        custo = f" · US$ {tr.spent_usd:.3f}" if tr.spent_usd else ""
        painel.atualizar("tr", pct=pct, etapa=t("eb_translating_to", language=nome_lang(trabalho.destino)),
                         partes=t("eb_units_done", done=f"{estado['feitos']:,}".replace(",", "."), total=f"{total:,}".replace(",", ".")),
                         vel=(t("eb_chars_per_s", n=_milhares(vel * media_chars)) if vel else "") + custo,
                         tamanho=t("eb_chars_of", done=_milhares(estado["feitos"] * media_chars), total=_milhares(total_chars)))

    def alvo():
        loop = asyncio.new_event_loop()
        estado["loop"] = loop
        try:
            estado["task"] = loop.create_task(translate_book(book, tr, store, trabalho.origem, trabalho.destino, ao_progresso))
            estado["report"] = loop.run_until_complete(estado["task"])
        except asyncio.CancelledError:
            pass
        except BaseException as e:  # noqa: BLE001 - devolvido para a thread principal
            estado["erro"] = e
        finally:
            loop.close()

    painel.atualizar("tr", pct=0, etapa=t("eb_starting"))
    linha = threading.Thread(target=alvo, daemon=True)
    linha.start()
    painel.desenhar()
    pedido = threading.Event()
    padrao_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: pedido.set())
    acao = "ok"
    try:
        while linha.is_alive():
            linha.join(0.25)
            painel.desenhar_se_preciso()
            if pedido.is_set():
                from bookstudio.audiobook.motor import perguntar_interrupcao

                escolha = perguntar_interrupcao(painel, padrao_sigint)
                signal.signal(signal.SIGINT, lambda *_: pedido.set())
                pedido.clear()
                if escolha in ("pause", "cancel"):
                    acao = escolha
                    if estado["loop"] and estado["task"]:
                        estado["loop"].call_soon_threadsafe(estado["task"].cancel)
                    linha.join()
                    break
    finally:
        signal.signal(signal.SIGINT, padrao_sigint)
    if acao == "ok" and estado["erro"] is not None:
        painel.atualizar("tr", fim="erro", etapa=str(estado["erro"]).splitlines()[0][:200])
        painel.desenhar()
        raise estado["erro"]
    if acao == "ok":
        painel.atualizar("tr", pct=100, fim="ok")
    else:
        painel.atualizar("tr", fim="erro", etapa=t("paused") if acao == "pause" else t("cancelled"))
    painel.retomar()
    return acao, estado["report"]


def _mostrar_problemas(problemas: list[tuple[str, str]], limite: int = 15) -> None:
    ui.cartao(t("eb_left_original"), [(trecho[:60], motivo[:60]) for trecho, motivo in problemas[:limite]], cor="yellow")
    if len(problemas) > limite:
        ui.dica(t("eb_and_more", n=len(problemas) - limite))
    if any("filtro" in m or "filter" in m for _, m in problemas):
        ui.dica(t("eb_content_filter_tip"))


def erros_recentes(limite: int = 30) -> None:
    caminho = LOG_DIR / "bookstudio.log"
    try:
        linhas = caminho.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        ui.info(t("eb_no_log"))
        return
    inicio = max((n for n, ln in enumerate(linhas) if "iniciando BookStudio" in ln), default=0)
    erros = [ln for ln in linhas[inicio:] if " WARNING " in ln or " ERROR " in ln]
    if not erros:
        ui.ok(t("eb_no_errors"))
    else:
        ui.secao(t("eb_errors_session"))
        for ln in erros[-limite:]:
            partes = ln.split(" ", 4)
            (ui.falha if " ERROR " in ln else ui.alerta)(f"{partes[1][:8]}  {partes[4]}" if len(partes) == 5 else ln)
    ui.dica(t("eb_full_log", path=caminho))


def executar(trabalho: Trabalho, settings: Settings, store: Store, extra: dict | None = None,
             tr: Translator | None = None, book: Book | None = None, workdir: Path | None = None) -> Path | None:
    """Traduz (se for o caso) e grava, registrando o trabalho para poder continuar. Devolve o arquivo gravado."""
    params = {"mode": trabalho.modo, "src": str(trabalho.src), "out": str(trabalho.out),
              "source": trabalho.origem, "target": trabalho.destino, **(extra or {})}
    job_id = store.start_job(params)
    report = None
    try:
        if trabalho.modo == METADADOS:
            ui.aguarde(t("eb_saving_metadata", name=trabalho.out.name), formats.save_metadata, book, trabalho.src, trabalho.out, workdir)
        else:
            if trabalho.modo == TRADUZIR:
                ui.secao(t("eb_translating"), "traduzir")
                ui.dica(t("ctrl_c_hint_pause"))
                acao, report = traduzir_com_painel(book, tr, store, trabalho)
                if acao == "pause":
                    store.set_job_status(job_id, "paused")
                    ui.painel([t("eb_paused_saved"), t("paused_resume_hint")], titulo=t("paused"), cor="yellow")
                    return None
                if acao == "cancel":
                    store.set_job_status(job_id, "cancelled")
                    ui.alerta(t("eb_cancelled"))
                    return None
            elif book.language == "" and trabalho.origem:
                book.language = trabalho.origem
            ui.aguarde(t("eb_saving", name=trabalho.out.name), formats.write_book, book, trabalho.out, workdir)
    except KeyboardInterrupt:
        store.set_job_status(job_id, "paused")
        ui.alerta(t("eb_paused_saved"))
        return None
    except BudgetExceeded as e:
        store.set_job_status(job_id, "paused")
        ui.alerta(f"{e} {t('eb_budget_hint')}")
        return None
    except FatalTranslateError as e:
        store.set_job_status(job_id, "paused")
        ui.falha(str(e))
        ui.dica(t("eb_progress_saved"))
        if ui.interativo() and ui.sim_nao(t("eb_retry_now_q"), padrao=True):
            store.set_job_status(job_id, "resumed")
            return retomar(params, settings, store)
        return None
    except EbookError as e:
        store.set_job_status(job_id, "failed")
        ui.falha(str(e))
        return None
    store.set_job_status(job_id, "done")

    linhas = [str(trabalho.out)]
    if report is not None:
        linhas.append(t("eb_report_units", total=f"{report.total_units:,}".replace(",", "."),
                        new=f"{report.translated:,}".replace(",", "."), cache=f"{report.from_cache:,}".replace(",", ".")))
        if report.cost_usd:
            linhas.append(t("eb_spent", cost=f"{report.cost_usd:.3f}"))
        if report.plain_fallback:
            linhas.append(t("eb_lost_formatting", n=report.plain_fallback))
        if report.untranslated:
            linhas.append(t("eb_untranslated", n=report.untranslated))
    ui.painel(linhas, titulo=f"{ui.ICONE['ok']} {t('done')}", cor="green")
    if report is not None and report.untranslated:
        _mostrar_problemas(report.problems)
        if ui.interativo() and ui.sim_nao(t("eb_retry_untranslated_q", n=report.untranslated), padrao=True):
            return retomar(params, settings, store)  # o cache faz só o que falta
    return trabalho.out


def _glossario(tr: Translator, book: Book, origem: str) -> None:
    from bookstudio.ebook.translate.llm import LLMTranslator

    if isinstance(tr, LLMTranslator):
        from bookstudio.ebook.core.glossary import build_glossary

        termos = ui.aguarde(t("eb_building_glossary"), build_glossary, book, origem)
        tr.set_glossary(termos)
        if termos:
            ui.info(t("eb_glossary", n=len(termos), terms=", ".join(termos[:8]) + ("…" if len(termos) > 8 else "")))


def fluxo(modo: str, settings: Settings, store: Store, entrada: Path | None = None, formato: str | None = None,
          destino: str | None = None, rotulo_destino: str | None = None) -> Path | None:
    """Converter, traduzir ou editar metadados de um e-book. `formato`: formato de saída já decidido (os fluxos
    encadeados usam EPUB); `destino`: idioma de destino já decidido. Devolve o arquivo gravado."""
    src = entrada or escolher_ebook()
    fmt_in = formats.ext_of(src)
    with tempfile.TemporaryDirectory(prefix="bookstudio-") as tmp:
        workdir = Path(tmp)
        try:
            book = ui.aguarde(t("eb_reading_book"), formats.read_book, src, workdir)
        except EbookError as e:
            ui.falha(str(e))
            return None
        resumo_livro(book, src)
        trabalho = Trabalho(modo=modo, src=src, out=src)
        if modo == METADADOS:
            if not formats.can_edit_metadata(fmt_in):
                ui.falha(t("eb_no_metadata_txt") if fmt_in == "txt" else t("eb_no_metadata_calibre"))
                return None
            if not editar_metadados(book):
                ui.info(t("eb_nothing_changed"))
                return None
            trabalho.out = escolher_saida(src, fmt_in, f" ({t('eb_edited_suffix')})", sobrescrever_ok=True)
            return executar(trabalho, settings, store, {"meta": book.edited_metadata()}, None, book, workdir)
        tr = None
        extra: dict = {}
        if modo == TRADUZIR:
            trabalho.origem = escolher_origem(book)
            trabalho.destino = destino or escolher_destino(settings, trabalho.origem, rotulo_destino)
            settings.remember_target(trabalho.destino)
            settings.save()
        if ui.sim_nao(t("eb_edit_metadata_q"), padrao=False):
            editar_metadados(book, trabalho.destino)
        fmt_out = formato or escolher_formato_saida(modo, fmt_in)
        trabalho.out = escolher_saida(src, fmt_out, f" ({trabalho.destino})" if trabalho.destino else "")
        if modo == TRADUZIR:
            tr = escolher_motor(settings, trabalho.origem, trabalho.destino)
            from bookstudio.ebook.translate.fake import FakeTranslator

            extra = {"engine": "fake" if isinstance(tr, FakeTranslator) else settings.engine, "provider": settings.provider,
                     "model": settings.model, "api_base": settings.api_base}
            _glossario(tr, book, trabalho.origem)
            from bookstudio.ebook.translate.pipeline import estimate

            est = ui.aguarde(t("eb_estimating"), estimate, book, tr, store, trabalho.origem, trabalho.destino)
            mostrar_estimativa(tr, est, settings)
        if book.edited:
            extra["meta"] = book.edited_metadata()  # reaplicado se o trabalho for retomado
        if not ui.sim_nao(t("start_q"), padrao=True):
            return None
        return executar(trabalho, settings, store, extra, tr, book, workdir)


def retomar(params: dict, settings: Settings, store: Store) -> Path | None:
    """Continua um trabalho pausado (o cache faz só o que falta traduzir)."""
    src, out = Path(params["src"]), Path(params["out"])
    if not src.exists():
        ui.falha(t("eb_source_gone", path=src))
        return None
    with tempfile.TemporaryDirectory(prefix="bookstudio-") as tmp:
        workdir = Path(tmp)
        try:
            book = ui.aguarde(t("eb_reading_book"), formats.read_book, src, workdir)
        except EbookError as e:
            ui.falha(str(e))
            return None
        trabalho = Trabalho(params["mode"], src, out, params.get("source", ""), params.get("target", ""))
        if trabalho.modo == "traduzir_converter":  # trabalho do Ebook Studio: hoje é "traduzir" com outro formato
            trabalho.modo = TRADUZIR
        if params.get("meta"):
            book.set_metadata(params["meta"])
        tr = None
        if trabalho.modo == TRADUZIR:
            if params.get("engine") == "fake":
                from bookstudio.ebook.translate.fake import FakeTranslator

                tr = FakeTranslator(trabalho.origem, trabalho.destino)
            elif params.get("engine") == "google":
                tr = google(settings, trabalho.origem, trabalho.destino)
            else:
                from bookstudio.ebook.translate.llm import LLMTranslator

                provedores = load_providers()
                pid = params.get("provider", "")
                chave, _ = _chave(pid, provedores.get(pid, {}))
                tr = LLMTranslator(trabalho.origem, trabalho.destino, params.get("model", ""), chave,
                                   params.get("api_base") or None, settings.llm_parallel, pid)
                tr.max_cost_usd = settings.max_cost_usd
            _glossario(tr, book, trabalho.origem)
        return executar(trabalho, settings, store,
                        {k: params.get(k) for k in ("engine", "provider", "model", "api_base", "meta") if k in params},
                        tr, book, workdir)


# ------------------------------------------------------------------- configurações da tradução
def menu_configuracoes(settings: Settings, store: Store) -> None:
    while True:
        teto = t("eb_no_ceiling") if not settings.max_cost_usd else f"US$ {settings.max_cost_usd:.2f}"
        escolha = ui.menu(t("eb_translation_settings"), [
            (t("eb_set_engine", engine=t("eb_engine_ai") if settings.engine == "llm" else t("eb_engine_google")), "engine"),
            (t("eb_set_target", language=nome_lang(settings.target_lang)), "lang"),
            (t("eb_set_parallel", n=settings.llm_parallel), "par"),
            (t("eb_set_ceiling", value=teto), "cost"),
            (t("eb_delete_key"), "key"),
            (t("eb_clear_cache", n=f"{store.count():,}".replace(",", ".")), "cache"),
            (t("eb_recent_errors"), "errors"),
            (t("eb_show_logs"), "logs"),
            (t("back"), "back")])
        if escolha == "back":
            return
        if escolha == "engine":
            settings.engine = ui.menu(t("eb_how_translate"), [(t("eb_engine_ai"), "llm"), (t("eb_engine_google"), "google")],
                                      padrao=settings.engine)
        elif escolha == "lang":
            settings.target_lang = escolher_destino(settings, "")
        elif escolha == "par":
            n = ui.perguntar(t("eb_parallel_prompt"), str(settings.llm_parallel))
            if n.isdigit() and 1 <= int(n) <= 32:
                settings.llm_parallel = int(n)
        elif escolha == "cost":
            v = ui.perguntar(t("eb_ceiling_prompt"), f"{settings.max_cost_usd:.2f}").replace(",", ".")
            try:
                settings.max_cost_usd = max(0.0, float(v))
            except ValueError:
                ui.alerta(t("eb_number_invalid"))
        elif escolha == "key":
            provedores = load_providers()
            pid = ui.menu(t("eb_ai_provider"), [(p["nome"], k) for k, p in provedores.items()] + [(t("back"), "__back__")])
            if pid != "__back__":
                (ui.ok if delete_key(pid) else ui.info)(t("eb_key_deleted") if get_saved_key(pid) is None else t("eb_no_saved_key"))
        elif escolha == "cache":
            if ui.sim_nao(t("eb_clear_cache_q"), padrao=False):
                ui.ok(t("eb_cache_cleared", n=store.clear_translations()))
        elif escolha == "errors":
            erros_recentes()
        elif escolha == "logs":
            from bookstudio.sistema import abrir

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            abrir(str(LOG_DIR))
        settings.save()
