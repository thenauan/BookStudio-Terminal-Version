"""Tradução grátis com Google, sem chave.

Primeiro pelo endpoint público do Google Tradutor com o texto no corpo do pedido (POST): aceita lotes de
~4.500 caracteres, então são bem menos pedidos (mais rápido e menos bloqueios). Se ele recusar, o
deep-translator (outra página do Google, com o texto na URL e por isso em pedaços de até 1.800 caracteres).
Poucos pedidos simultâneos e pausas, para não ser bloqueado. Os parágrafos do lote são separados por
marcadores numerados; se o Google bagunçar a separação, o lote é refeito parágrafo a parágrafo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import urllib.error
import urllib.parse
import urllib.request

from .base import TranslateError, Translator

log = logging.getLogger(__name__)

SEP_RE = re.compile(r"\[\[\s*§\s*(\d+)\s*\]\]")
MAX_CHARS = 4500          # lote pelo pedido direto (POST, sem limite de URL)
MAX_CHARS_URL = 1800      # deep-translator: o texto vai na URL (GET), que cresce ~3x com acentos codificados
PAUSE = 0.4
MAX_ATTEMPTS = 5
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class _Limitado(Exception):
    """O Google recusou por excesso de pedidos (429)."""


def _direto(text: str, source: str, target: str) -> str:
    corpo = urllib.parse.urlencode({"q": text}).encode("utf-8")
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
        {"client": "gtx", "sl": source or "auto", "tl": target, "dt": "t", "ie": "UTF-8", "oe": "UTF-8"})
    req = urllib.request.Request(url, data=corpo, headers={
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise _Limitado() from e
        raise
    res = "".join(seg[0] for seg in (dados[0] or []) if seg and seg[0])
    if not res.strip():
        raise ValueError("tradução vazia")
    return res


class GoogleFreeTranslator(Translator):
    batch_chars = MAX_CHARS
    batch_units = 30
    keeps_tags = False  # o Google costuma mexer nos marcadores: usamos texto puro

    def __init__(self, source_google: str, target_google: str, parallel: int = 2):
        super().__init__(source_google, target_google, parallel)
        self.engine_id = "google-free"
        self._source, self._target = source_google or "auto", target_google
        self._client = None  # deep-translator, só se o pedido direto falhar

    def _reserva(self, text: str) -> str:
        """deep-translator, em pedaços que cabem na URL (cortados entre parágrafos, que é onde ficam os marcadores)."""
        if self._client is None:
            from deep_translator import GoogleTranslator

            self._client = GoogleTranslator(source=self._source, target=self._target)
        if len(text) <= MAX_CHARS_URL:
            return self._client.translate(text) or ""
        pedacos, atual = [], ""
        for par in text.split("\n\n"):
            if atual and len(atual) + len(par) + 2 > MAX_CHARS_URL:
                pedacos.append(atual)
                atual = ""
            atual = f"{atual}\n\n{par}" if atual else par
        if atual:
            pedacos.append(atual)
        return "\n\n".join((self._client.translate(p) or "") for p in pedacos)

    def _uma_vez(self, text: str) -> str:
        try:
            return _direto(text, self._source, self._target)
        except _Limitado:
            raise
        except Exception as exc:  # noqa: BLE001 - endpoint fora do ar ou formato mudou: usa a reserva
            log.info("Google direto falhou (%s); usando o deep-translator", type(exc).__name__)
            return self._reserva(text)

    async def _translate_text(self, text: str) -> str:
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with self.limiter:
                    result = await asyncio.to_thread(self._uma_vez, text)
                    await asyncio.sleep(PAUSE + random.uniform(0, 0.3))
                await self.limiter.success()
                if not result:
                    raise TranslateError("resposta vazia")
                return result
            except TranslateError:
                raise
            except Exception as exc:  # noqa: BLE001 - deep-translator lança vários tipos
                msg = str(exc).lower()
                if isinstance(exc, _Limitado) or "too many" in msg or "429" in msg:
                    self.limiter.rate_limited()
                wait = min(60.0, 2 ** (attempt + 1)) + random.uniform(0, 2)
                log.info("Google falhou (%s); esperando %.1fs", type(exc).__name__, wait)
                await asyncio.sleep(wait)
        raise TranslateError("O Google não respondeu depois de várias tentativas (possível bloqueio temporário).")

    async def _translate_long(self, text: str) -> str:
        """Parágrafo maior que o limite: traduz em pedaços de frases inteiras."""
        if len(text) <= MAX_CHARS:
            return await self._translate_text(text)
        sentences = re.split(r"(?<=[.!?…])\s+", text)
        chunks, cur = [], ""
        for s in sentences:
            while len(s) > MAX_CHARS:  # frase gigante: corta no espaço mais próximo
                cut = s.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > 0 else MAX_CHARS
                chunks.append(s[:cut])
                s = s[cut:].lstrip()
            if cur and len(cur) + len(s) + 1 > MAX_CHARS:
                chunks.append(cur)
                cur = ""
            cur = f"{cur} {s}".strip()
        if cur:
            chunks.append(cur)
        parts = [await self._translate_text(c) for c in chunks]
        return " ".join(parts)

    async def translate_batch(self, items: dict[str, str], context: str = "") -> dict[str, str]:
        keys = list(items)
        if len(keys) == 1:
            k = keys[0]
            return {k: await self._translate_long(items[k])}
        joined = "\n\n".join(f"[[§{k}]] {items[k]}" for k in keys)
        result = await self._translate_text(joined)
        parts = SEP_RE.split(result)
        out: dict[str, str] = {}
        # parts = [antes, id1, texto1, id2, texto2, ...]
        for i in range(1, len(parts) - 1, 2):
            out[parts[i]] = parts[i + 1].strip()
        if set(out) != set(keys):
            log.info("Google misturou a separação do lote; refazendo item a item")
            out = {}
            for k in keys:
                out[k] = await self._translate_text(items[k])
        return out
