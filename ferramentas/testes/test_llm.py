import asyncio
from types import SimpleNamespace

import pytest

from bookstudio.ebook.translate import llm
from bookstudio.ebook.translate.base import FatalTranslateError, TranslateError


def test_strip_json_variants():
    assert llm._strip_json('```json\n{"1": "a"}\n```') == {"1": "a"}
    assert llm._strip_json('Here: {"1": "a", "2": "b"} done') == {"1": "a", "2": "b"}
    with pytest.raises(TranslateError):
        llm._strip_json("sem json")


def _resp(content, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


def test_translate_batch_and_prompt(monkeypatch):
    tr = llm.LLMTranslator("en", "pt-BR", "anthropic/claude-haiku-5-5", "sk-test", parallel=2, provider="anthropic")
    tr.set_glossary(["Elizabeth"])
    seen = {}

    async def fake_call(messages, max_tokens):
        seen["messages"] = messages
        return _resp('{"1": "Olá <t1>mundo</t1>"}')

    monkeypatch.setattr(tr, "_call", fake_call)
    monkeypatch.setattr(tr._litellm, "completion_cost", lambda **k: 0.01)
    out = asyncio.run(tr.translate_batch({"1": "Hello <t1>world</t1>"}, context="Antes."))
    assert out == {"1": "Olá <t1>mundo</t1>"}
    system = seen["messages"][0]["content"][0]
    assert system["cache_control"] == {"type": "ephemeral"} and "Elizabeth" in system["text"]
    assert "Brazilian Portuguese" in system["text"] and "Antes." in seen["messages"][1]["content"]
    assert tr.spent_usd == pytest.approx(0.01)


def test_truncated_answer_is_retryable(monkeypatch):
    tr = llm.LLMTranslator("en", "pt-BR", "openai/gpt-5-mini", "k")

    async def fake_call(messages, max_tokens):
        return _resp('{"1": "Olá', finish="length")

    monkeypatch.setattr(tr, "_call", fake_call)
    with pytest.raises(TranslateError):
        asyncio.run(tr.translate_batch({"1": "Hello"}))


def test_auth_error_is_fatal(monkeypatch):
    tr = llm.LLMTranslator("en", "pt-BR", "openai/gpt-5-mini", "bad")

    class AuthenticationError(Exception):
        pass

    async def fake_call(messages, max_tokens):
        raise AuthenticationError("invalid api key")

    monkeypatch.setattr(tr, "_call", fake_call)
    with pytest.raises(FatalTranslateError, match="Chave de API inválida"):
        asyncio.run(tr.translate_batch({"1": "Hello"}))


def test_budget_ceiling(monkeypatch):
    from bookstudio.ebook.translate.base import BudgetExceeded

    tr = llm.LLMTranslator("en", "pt-BR", "openai/gpt-5-mini", "k")
    tr.max_cost_usd, tr.spent_usd = 1.0, 1.0
    with pytest.raises(BudgetExceeded):
        asyncio.run(tr.translate_batch({"1": "Hello"}))
