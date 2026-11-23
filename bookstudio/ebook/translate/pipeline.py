"""Orquestra a tradução do livro.

- Capítulos são traduzidos em paralelo; dentro de um capítulo os lotes seguem em
  ordem, levando os últimos parágrafos já traduzidos como contexto.
- Cada lote validado é gravado no cache antes de seguir (retomada segura).
- Respostas são conferidas: ids faltando, texto vazio, tamanho implausível,
  marcadores quebrados e texto que voltou no idioma original são refeitos.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from .. import languages
from ..core import xhtml
from ..core.detect import detect_text
from ..core.model import Book, Chapter
from ..storage.cache import Store, make_key
from .base import BudgetExceeded, FatalTranslateError, TranslateError, Translator

log = logging.getLogger(__name__)

NCX_TEXT = "{http://www.daisy.org/z3986/2005/ncx/}text"
CONTEXT_UNITS = 2
CONTEXT_CHARS = 500
EXTRA_ROUNDS = 2  # novas tentativas para itens que falharam na validação
LOTES_POR_FAIXA = 6  # capítulo com mais lotes que isso é traduzido em faixas paralelas


@dataclass
class Report:
    total_units: int = 0
    from_cache: int = 0
    translated: int = 0
    plain_fallback: int = 0
    untranslated: int = 0
    cost_usd: float = 0.0
    #: (início do trecho, motivo da última falha) de cada trecho que ficou no original
    problems: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Estimate:
    total_units: int
    pending_units: int
    pending_chars: int
    batches: int


ProgressFn = Callable[[int, str], None]  # (unidades concluídas agora, prévia)


# ------------------------------------------------------------------ preparação


def _units_of(chapter: Chapter, tree) -> list[xhtml.Unit]:
    if chapter.kind == "ncx":
        return [
            xhtml.element_to_unit(el) for el in tree.getroot().iter(NCX_TEXT)
            if el.text and xhtml._has_letters(el.text)  # noqa: SLF001
        ]
    return xhtml.collect_units(tree, nav=chapter.kind == "nav")


def _request_text(unit: xhtml.Unit, translator: Translator) -> str:
    return unit.text if translator.keeps_tags else unit.plain


def _batches(pending: list[int], texts: list[str], max_chars: int, max_units: int) -> list[list[int]]:
    out, cur, size = [], [], 0
    for i in pending:
        n = len(texts[i])
        if cur and (size + n > max_chars or len(cur) >= max_units):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


def _translatable(book: Book) -> list[Chapter]:
    return [c for c in book.chapters if c.kind in ("content", "nav", "ncx")]


def estimate(book: Book, translator: Translator, store: Store, source: str, target: str) -> Estimate:
    total = pending_units = pending_chars = batches = 0
    for ch in _translatable(book):
        tree = xhtml.parse(ch.data)
        if tree is None:
            continue
        texts = [_request_text(u, translator) for u in _units_of(ch, tree)]
        keys = [make_key(translator.engine_id, source, target, t) for t in texts]
        cached = store.get_many(keys)
        pending = [i for i, k in enumerate(keys) if k not in cached]
        total += len(texts)
        pending_units += len(pending)
        pending_chars += sum(len(texts[i]) for i in pending)
        batches += len(_batches(pending, texts, translator.batch_chars, translator.batch_units))
    return Estimate(total, pending_units, pending_chars, batches)


# ------------------------------------------------------------------ validação


def _plausible_length(orig: str, trans: str, target: str) -> bool:
    lo = 0.12 if target.split("-")[0] in {"zh", "ja", "ko"} else 0.25
    a, b = len(orig), len(trans)
    if a < 30:
        return b > 0
    return lo * a <= b <= 4 * a


class _Validator:
    def __init__(self, source: str, target: str):
        src = languages.get(source)
        tgt = languages.get(target)
        self.src_iso = src.iso1 if src else None
        self.check_lang = bool(src and tgt and src.iso1 != tgt.iso1)
        self.target = target

    def ok_text(self, unit: xhtml.Unit, trans: str) -> bool:
        plain = xhtml.strip_placeholders(trans)
        if not plain.strip():
            return False
        if not _plausible_length(unit.plain, plain, self.target):
            return False
        if self.check_lang and len(plain) >= 80 and plain != unit.plain:
            if detect_text(plain) == self.src_iso:
                return False
        if self.check_lang and len(plain) >= 80 and plain == unit.plain:
            return False  # voltou sem traduzir
        return True


# ------------------------------------------------------------------ tradução


class _Run:
    def __init__(self, book: Book, translator: Translator, store: Store, source: str, target: str,
                 on_progress: ProgressFn | None):
        self.book = book
        self.tr = translator
        self.store = store
        self.source = source
        self.target = target
        self.on_progress = on_progress or (lambda n, p: None)
        self.report = Report()
        self.validator = _Validator(source, target)
        if translator.engine_id == "fake":  # o motor de teste não muda o idioma de verdade
            self.validator.check_lang = False
        lang = languages.get(target)
        self.rtl = bool(lang and lang.rtl)
        self.chapter_sem = asyncio.Semaphore(max(2, translator.limiter.max * 2))

    def key(self, text: str) -> str:
        return make_key(self.tr.engine_id, self.source, self.target, text)

    def apply(self, unit: xhtml.Unit, trans: str) -> bool:
        """Aplica a tradução; retorna False se precisou cair no texto puro."""
        if self.tr.keeps_tags and unit.templates:
            if xhtml.apply_translation(unit, trans):
                return True
            xhtml.apply_plain_fallback(unit, trans)
            return False
        if unit.templates:  # motor sem marcadores (Google): mantém imagens e quebras
            xhtml.apply_plain_fallback(unit, trans)
            return not (set(unit.templates) - unit.empty_ids)
        unit.el.text = xhtml.strip_placeholders(trans)
        return True

    async def translate_items(self, idxs: list[int], units: list[xhtml.Unit], texts: list[str],
                              context: str) -> dict[int, str]:
        """Traduz e valida; devolve só os itens aprovados."""
        good: dict[int, str] = {}
        reasons: dict[int, str] = {}
        todo = list(idxs)
        for rnd in range(1 + EXTRA_ROUNDS):
            if not todo:
                break
            # na última rodada, item a item (a IA erra menos com pouco texto)
            groups = [todo] if rnd < EXTRA_ROUNDS else [[i] for i in todo]
            failed: list[int] = []
            for group in groups:
                payload = {str(n): texts[i] for n, i in enumerate(group, 1)}
                try:
                    result = await self.tr.translate_batch(payload, context)
                except TranslateError as e:
                    log.warning("lote falhou (rodada %d): %s", rnd + 1, e)
                    reason = str(e).splitlines()[0] if str(e) else type(e).__name__
                    reasons.update({i: reason for i in group})
                    failed.extend(group)
                    continue
                for n, i in enumerate(group, 1):
                    trans = result.get(str(n))
                    unit = units[i]
                    if trans is None:
                        reasons[i] = "a IA não devolveu este trecho"
                        failed.append(i)
                        continue
                    if not self.validator.ok_text(unit, trans):
                        reasons[i] = "tradução rejeitada na conferência (vazia, igual ao original ou incompleta)"
                        failed.append(i)
                        continue
                    if self.tr.keeps_tags and unit.templates and rnd < EXTRA_ROUNDS:
                        ids = sorted(xhtml.placeholder_ids(trans))
                        if ids != sorted(xhtml.placeholder_ids(texts[i])):
                            reasons[i] = "marcadores de formatação trocados"
                            failed.append(i)
                            continue
                    good[i] = trans
            todo = failed
        for i in todo:
            self.report.untranslated += 1
            reason = reasons.get(i, "motivo desconhecido")
            self.report.problems.append((units[i].plain[:80], reason))
            log.warning("trecho não traduzido (%s): %r", reason, units[i].plain[:200])
        return good

    async def chapter(self, ch: Chapter) -> None:
        async with self.chapter_sem:
            tree = xhtml.parse(ch.data)
            if tree is None:
                self.book.warnings.append(f"Capítulo ilegível mantido no original: {ch.path}")
                return
            units = _units_of(ch, tree)
            texts = [_request_text(u, self.tr) for u in units]
            keys = [self.key(t) for t in texts]
            cached = self.store.get_many(keys)

            recent: list[str] = []
            done_now = 0
            for i, k in enumerate(keys):
                if k in cached:
                    if not self.apply(units[i], cached[k]):
                        self.report.plain_fallback += 1
                    self.report.from_cache += 1
                    done_now += 1
            if done_now:
                self.on_progress(done_now, "")

            pending = [i for i, k in enumerate(keys) if k not in cached]
            batches = _batches(pending, texts, self.tr.batch_chars, self.tr.batch_units)
            # Capítulo longo: os lotes vão em faixas paralelas (cada faixa em ordem, com o próprio contexto), para
            # um capítulo enorme não segurar o fim do livro enquanto os outros já terminaram
            n_faixas = max(1, min(self.tr.limiter.max, -(-len(batches) // LOTES_POR_FAIXA)))
            tamanho = -(-len(batches) // n_faixas) if batches else 0
            faixas = [batches[i:i + tamanho] for i in range(0, len(batches), tamanho)] if batches else []

            async def faixa(lotes: list[list[int]]) -> None:
                recentes: list[str] = list(recent)
                for batch in lotes:
                    context = "\n".join(recentes[-CONTEXT_UNITS:])
                    good = await self.translate_items(batch, units, texts, context)
                    if good:
                        self.store.put_many({keys[i]: t for i, t in good.items()})
                    last_preview = ""
                    for i in batch:
                        if i in good:
                            if not self.apply(units[i], good[i]):
                                self.report.plain_fallback += 1
                            self.report.translated += 1
                            plain = xhtml.strip_placeholders(good[i])
                            recentes.append(plain[:CONTEXT_CHARS])
                            last_preview = plain
                    self.report.cost_usd = self.tr.spent_usd
                    self.on_progress(len(batch), last_preview)

            await asyncio.gather(*(faixa(f) for f in faixas))

            if ch.kind in ("content", "nav"):
                xhtml.set_language(tree, self.target, self.rtl)
            ch.data = xhtml.serialize(tree)

    async def run(self) -> Report:
        chapters = _translatable(self.book)
        tasks = [asyncio.create_task(self.chapter(c)) for c in chapters]
        try:
            await asyncio.gather(*tasks)
        except (FatalTranslateError, BudgetExceeded, asyncio.CancelledError, KeyboardInterrupt):
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        self.book.target_language = self.target
        self.report.cost_usd = self.tr.spent_usd
        return self.report


def count_units(book: Book, translator: Translator) -> int:
    total = 0
    for ch in _translatable(book):
        tree = xhtml.parse(ch.data)
        if tree is not None:
            total += len(_units_of(ch, tree))
    return total


async def translate_book(book: Book, translator: Translator, store: Store, source: str, target: str,
                         on_progress: ProgressFn | None = None) -> Report:
    run = _Run(book, translator, store, source, target, on_progress)
    run.report.total_units = count_units(book, translator)
    return await run.run()
