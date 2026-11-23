"""Motor falso para testes: "traduz" sem rede e sem custo."""

from __future__ import annotations

import asyncio
import re

from .base import Translator

_WORD = re.compile(r"(<[^>]+>)|([^\W\d_]+)", re.UNICODE)


class FakeTranslator(Translator):
    """Põe as palavras em MAIÚSCULAS e preserva os marcadores."""

    keeps_tags = True

    def __init__(self, source: str = "en", target: str = "pt-BR", parallel: int = 4,
                 fail_every: int = 0):
        super().__init__(source, target, parallel)
        self.engine_id = "fake"
        self.calls = 0
        self.fail_every = fail_every

    async def translate_batch(self, items: dict[str, str], context: str = "") -> dict[str, str]:
        self.calls += 1
        async with self.limiter:
            await asyncio.sleep(0)
        out = {}
        for k, v in items.items():
            out[k] = _WORD.sub(lambda m: m.group(1) or m.group(2).upper(), v)
        if self.fail_every and self.calls % self.fail_every == 0:
            out.pop(next(iter(out)))  # simula a IA pulando um parágrafo
        return out
