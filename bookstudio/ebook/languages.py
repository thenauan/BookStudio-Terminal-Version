"""Idiomas suportados: nome em português, código ISO 639-1 (lingua) e código do Google."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lang:
    code: str  # código usado no app e nos metadados (BCP 47)
    name: str
    iso1: str  # para o detector lingua
    google: str
    flag: str = "🏳️"
    source: bool = True  # aparece como idioma de origem
    target: bool = True  # aparece como idioma de destino
    rtl: bool = False


LANGS: list[Lang] = [
    Lang("pt-BR", "português (Brasil)", "PT", "pt", "🇧🇷", source=False),
    Lang("pt-PT", "português (Portugal)", "PT", "pt", "🇵🇹", source=False),
    Lang("pt", "português", "PT", "pt", "🇧🇷", target=False),
    Lang("en", "inglês", "EN", "en", "🇬🇧"),
    Lang("es", "espanhol", "ES", "es", "🇪🇸"),
    Lang("fr", "francês", "FR", "fr", "🇫🇷"),
    Lang("de", "alemão", "DE", "de", "🇩🇪"),
    Lang("it", "italiano", "IT", "it", "🇮🇹"),
    Lang("nl", "holandês", "NL", "nl", "🇳🇱"),
    Lang("ru", "russo", "RU", "ru", "🇷🇺"),
    Lang("uk", "ucraniano", "UK", "uk", "🇺🇦"),
    Lang("pl", "polonês", "PL", "pl", "🇵🇱"),
    Lang("cs", "tcheco", "CS", "cs", "🇨🇿"),
    Lang("sv", "sueco", "SV", "sv", "🇸🇪"),
    Lang("da", "dinamarquês", "DA", "da", "🇩🇰"),
    Lang("nb", "norueguês", "NB", "no", "🇳🇴"),
    Lang("fi", "finlandês", "FI", "fi", "🇫🇮"),
    Lang("ro", "romeno", "RO", "ro", "🇷🇴"),
    Lang("hu", "húngaro", "HU", "hu", "🇭🇺"),
    Lang("ca", "catalão", "CA", "ca", "🏳️"),
    Lang("el", "grego", "EL", "el", "🇬🇷"),
    Lang("tr", "turco", "TR", "tr", "🇹🇷"),
    Lang("ar", "árabe", "AR", "ar", "🇸🇦", rtl=True),
    Lang("he", "hebraico", "HE", "iw", "🇮🇱", rtl=True),
    Lang("fa", "persa", "FA", "fa", "🇮🇷", rtl=True),
    Lang("hi", "hindi", "HI", "hi", "🇮🇳"),
    Lang("bn", "bengali", "BN", "bn", "🇧🇩"),
    Lang("zh-CN", "chinês (simplificado)", "ZH", "zh-CN", "🇨🇳", source=False),
    Lang("zh-TW", "chinês (tradicional)", "ZH", "zh-TW", "🇹🇼", source=False),
    Lang("zh", "chinês", "ZH", "zh-CN", "🇨🇳", target=False),
    Lang("ja", "japonês", "JA", "ja", "🇯🇵"),
    Lang("ko", "coreano", "KO", "ko", "🇰🇷"),
    Lang("id", "indonésio", "ID", "id", "🇮🇩"),
    Lang("vi", "vietnamita", "VI", "vi", "🇻🇳"),
    Lang("th", "tailandês", "TH", "th", "🇹🇭"),
    Lang("la", "latim", "LA", "la", "🏛️"),
]

BY_CODE = {lang.code: lang for lang in LANGS}
SOURCE_LANGS = [lang for lang in LANGS if lang.source]
TARGET_LANGS = [lang for lang in LANGS if lang.target]
# idiomas sem maiúsculas: o glossário automático de nomes não funciona neles
NO_CASE = {"ar", "he", "fa", "hi", "bn", "zh", "zh-CN", "zh-TW", "ja", "ko", "th"}


def get(code: str) -> Lang | None:
    if code in BY_CODE:
        return BY_CODE[code]
    base = code.split("-")[0].lower()
    return BY_CODE.get(base)


def source_from_iso1(iso1: str) -> Lang | None:
    return next((lang for lang in SOURCE_LANGS if lang.iso1 == iso1), None)


def same_language(a: str, b: str) -> bool:
    return a.split("-")[0].lower() == b.split("-")[0].lower()
