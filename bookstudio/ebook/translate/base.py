"""Interface dos motores de tradução e controle de concorrência."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class TranslateError(Exception):
    """Falha recuperável de um lote (o pipeline tenta de novo ou divide)."""


class FatalTranslateError(Exception):
    """Falha que para o trabalho todo (chave inválida, sem crédito...)."""


class BudgetExceeded(Exception):
    """O teto de gasto foi atingido: o trabalho é pausado e pode ser retomado."""


class AdaptiveLimiter:
    """Semáforo que diminui sozinho ao receber limite de taxa (429) e volta a
    crescer devagar quando os pedidos dão certo."""

    def __init__(self, limit: int):
        self.max = max(1, limit)
        self.limit = self.max
        self.active = 0
        self._ok_streak = 0
        self._cond = asyncio.Condition()
        # ritmo mínimo entre o início de dois pedidos (para limites por minuto)
        self.min_interval = 0.0
        self._next_start = 0.0
        self._pace_lock = asyncio.Lock()

    def set_rpm(self, rpm: int) -> None:
        """Limita a ``rpm`` pedidos por minuto (com uma pequena folga)."""
        if rpm > 0:
            self.min_interval = max(self.min_interval, 60.0 / rpm * 1.05)

    def hold(self, seconds: float) -> None:
        """Ninguém começa um pedido novo antes de ``seconds`` a partir de agora."""
        loop = asyncio.get_running_loop()
        self._next_start = max(self._next_start, loop.time() + seconds)

    async def __aenter__(self):
        async with self._cond:
            while self.active >= self.limit:
                await self._cond.wait()
            self.active += 1
        try:
            async with self._pace_lock:
                loop = asyncio.get_running_loop()
                wait = self._next_start - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._next_start = max(self._next_start, loop.time()) + self.min_interval
        except BaseException:
            async with self._cond:
                self.active -= 1
                self._cond.notify_all()
            raise

    async def __aexit__(self, *exc):
        async with self._cond:
            self.active -= 1
            self._cond.notify_all()

    def rate_limited(self) -> None:
        self.limit = max(1, self.limit // 2)
        self._ok_streak = 0

    async def success(self) -> None:
        self._ok_streak += 1
        if self.limit < self.max and self._ok_streak >= 10:
            self._ok_streak = 0
            async with self._cond:
                self.limit += 1
                self._cond.notify_all()


class Translator(ABC):
    #: identifica o motor no cache (inclui o modelo)
    engine_id: str = "base"
    #: limite de caracteres por lote
    batch_chars: int = 6000
    #: limite de unidades por lote
    batch_units: int = 40
    #: aceita marcadores <t1>…</t1> com confiabilidade
    keeps_tags: bool = True

    def __init__(self, source: str, target: str, parallel: int):
        self.source = source
        self.target = target
        self.limiter = AdaptiveLimiter(parallel)
        self.spent_usd = 0.0
        self.max_cost_usd = 0.0

    @abstractmethod
    async def translate_batch(self, items: dict[str, str], context: str = "") -> dict[str, str]:
        """Traduz ``{id: texto}`` e devolve ``{id: tradução}`` (pode faltar id)."""

    def check_budget(self) -> None:
        if self.max_cost_usd and self.spent_usd >= self.max_cost_usd:
            raise BudgetExceeded(
                f"Teto de gasto atingido (US$ {self.spent_usd:.2f} de US$ {self.max_cost_usd:.2f})."
            )

    def set_glossary(self, terms: list[str]) -> None:  # noqa: B027 - opcional
        pass
