"""Ferramentas de XHTML: leitura tolerante, unidades traduzíveis e marcadores.

A tradução nunca joga a marcação fora. Cada parágrafo (ou título, item de lista...)
vira uma *unidade*: o texto dele com as tags internas trocadas por marcadores curtos
(``<t1>…</t1>``, ``<t2/>``). Depois da tradução, os marcadores voltam a ser as tags
originais, com todos os atributos.
"""

from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass, field
from html.entities import name2codepoint
from io import BytesIO

from lxml import etree

XHTML_NS = "http://www.w3.org/1999/xhtml"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "td", "th",
    "blockquote", "figcaption", "caption", "div", "section", "article", "aside",
    "header", "footer", "ul", "ol", "dl", "table", "tr", "tbody", "thead", "tfoot",
    "figure", "body", "nav", "pre", "hr", "address", "center", "main",
}
# Blocos que nunca são traduzidos (nem seu conteúdo).
SKIP_TAGS = {"script", "style", "pre", "code", "head", "svg", "math"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_PH_RE = re.compile(r"<\s*(/?)\s*t\s*(\d+)\s*(/?)\s*>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

_PARSER = etree.XMLParser(
    recover=True, resolve_entities=False, huge_tree=True, remove_blank_text=False, no_network=True
)


def localname(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname.lower()


def _fix_entities(data: bytes) -> bytes:
    """Troca entidades HTML (``&nbsp;``) por numéricas: XML puro não as conhece e
    um parser em modo tolerante descartaria o texto silenciosamente."""

    text = data.decode("utf-8", errors="replace")

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        cp = name2codepoint.get(name)
        return f"&#{cp};" if cp else m.group(0)

    return _ENTITY_RE.sub(repl, text).encode("utf-8")


def parse(data: bytes) -> etree._ElementTree | None:
    """Lê XHTML de forma tolerante. Retorna ``None`` se não houver documento."""

    try:
        tree = etree.parse(BytesIO(_fix_entities(data)), _PARSER)
    except etree.XMLSyntaxError:
        return None
    if tree.getroot() is None:
        return None
    return tree


def serialize(tree: etree._ElementTree) -> bytes:
    return etree.tostring(tree, encoding="utf-8", xml_declaration=True)


# --------------------------------------------------------------------------- unidades


@dataclass
class Unit:
    el: etree._Element
    text: str  # texto com marcadores
    templates: dict[int, etree._Element] = field(default_factory=dict)
    empty_ids: set[int] = field(default_factory=set)

    @property
    def plain(self) -> str:
        return strip_placeholders(self.text)


def _has_letters(text: str) -> bool:
    return bool(_LETTER_RE.search(text))


def iter_unit_elements(root: etree._Element, nav: bool = False):
    """Elementos-folha de bloco com texto, em ordem de leitura.

    Em sumários (``nav``), os links (``<a>``) também são unidades, porque ficam
    dentro de ``<li>`` que contêm outras listas.
    """

    has_block_desc: set = set()
    skipped: set = set()
    for el in root.iter():
        name = localname(el)
        if name in SKIP_TAGS:
            skipped.add(el)
            for d in el.iterdescendants():
                skipped.add(d)
        if name in BLOCK_TAGS:
            parent = el.getparent()
            while parent is not None:
                if parent in has_block_desc:
                    break
                has_block_desc.add(parent)
                parent = parent.getparent()

    for el in root.iter():
        if el in skipped or not isinstance(el.tag, str):
            continue
        name = localname(el)
        is_leaf_block = name in BLOCK_TAGS and el not in has_block_desc
        is_nav_link = nav and name == "a" and not any(
            localname(a) == "a" for a in el.iterancestors()
        )
        if nav and is_leaf_block and any(localname(d) == "a" for d in el.iterdescendants()):
            # o link dentro dele será a unidade
            continue
        if not (is_leaf_block or is_nav_link):
            continue
        if name in {"hr", "body"}:
            continue
        if _has_letters("".join(el.itertext())):
            yield el


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def element_to_unit(el: etree._Element) -> Unit:
    templates: dict[int, etree._Element] = {}
    empty_ids: set[int] = set()
    counter = [0]

    def inner(node) -> str:
        parts = [_esc(node.text or "")]
        for child in node:
            counter[0] += 1
            n = counter[0]
            tpl = copy.copy(child)
            for sub in list(tpl):
                tpl.remove(sub)
            if isinstance(tpl.tag, str):
                tpl.text = None
            tpl.tail = None
            templates[n] = tpl
            has_content = isinstance(child.tag, str) and (bool(child.text) or len(child) > 0)
            if has_content:
                parts.append(f"<t{n}>{inner(child)}</t{n}>")
            else:
                empty_ids.add(n)
                parts.append(f"<t{n}/>")
            parts.append(_esc(child.tail or ""))
        return "".join(parts)

    text = inner(el)
    text = _WS_RE.sub(" ", text).strip()
    return Unit(el=el, text=text, templates=templates, empty_ids=empty_ids)


def collect_units(tree: etree._ElementTree, nav: bool = False) -> list[Unit]:
    return [element_to_unit(el) for el in iter_unit_elements(tree.getroot(), nav=nav)]


def strip_placeholders(text: str) -> str:
    # marcadores vazios (<br/>, <img/>) viram espaço para não grudar palavras
    text = _PH_RE.sub(lambda m: " " if m.group(3) else "", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def placeholder_ids(text: str) -> list[int]:
    return [int(m.group(2)) for m in _PH_RE.finditer(text)]


def apply_translation(unit: Unit, translated: str) -> bool:
    """Reconstrói o elemento com o texto traduzido.

    Retorna ``False`` (sem alterar nada) se os marcadores voltaram quebrados.
    """

    tokens: list[tuple[str, object]] = []
    pos = 0
    for m in _PH_RE.finditer(translated):
        if m.start() > pos:
            tokens.append(("text", html.unescape(translated[pos:m.start()])))
        closing, n, selfclose = m.group(1) == "/", int(m.group(2)), m.group(3) == "/"
        if n not in unit.templates:
            return False
        kind = "close" if closing else ("empty" if selfclose else "open")
        tokens.append((kind, n))
        pos = m.end()
    if pos < len(translated):
        tokens.append(("text", html.unescape(translated[pos:])))

    # valida: cada marcador usado uma vez, aninhamento correto, todos presentes
    seen: set[int] = set()
    stack: list[int] = []
    for kind, val in tokens:
        if kind == "open":
            if val in seen or val in unit.empty_ids:
                return False
            seen.add(val)
            stack.append(val)
        elif kind == "close":
            if not stack or stack.pop() != val:
                return False
        elif kind == "empty":
            if val in seen:
                return False
            seen.add(val)
    if stack or seen != set(unit.templates):
        return False

    # reconstrói
    el = unit.el
    for child in list(el):
        el.remove(child)
    el.text = None
    current = el
    last: etree._Element | None = None  # último filho fechado no nível atual
    parents: list[tuple[etree._Element, etree._Element | None]] = []

    def add_text(txt: str):
        nonlocal last
        if last is None:
            current.text = (current.text or "") + txt
        else:
            last.tail = (last.tail or "") + txt

    for kind, val in tokens:
        if kind == "text":
            add_text(val)  # type: ignore[arg-type]
        elif kind in ("open", "empty"):
            new = copy.copy(unit.templates[val])  # type: ignore[index]
            current.append(new)
            if kind == "open":
                parents.append((current, new))
                current = new
                last = None
            else:
                last = new
        else:  # close
            parent, child = parents.pop()
            current = parent
            last = child
    return True


def apply_plain_fallback(unit: Unit, translated: str) -> None:
    """Último recurso: texto traduzido sem formatação interna, mas mantendo
    imagens e quebras de linha (elementos vazios) no fim."""

    el = unit.el
    keep = [copy.copy(unit.templates[n]) for n in sorted(unit.empty_ids)]
    for child in list(el):
        el.remove(child)
    el.text = strip_placeholders(translated)
    for k in keep:
        el.append(k)


# --------------------------------------------------------------------------- texto


def element_text(el: etree._Element) -> str:
    """Texto legível de um elemento, com ``<br>`` virando quebra de linha."""

    parts: list[str] = []

    def walk(node):
        if isinstance(node.tag, str) and localname(node) == "br":
            parts.append("\n")
        elif isinstance(node.tag, str):
            parts.append(node.text or "")
            for c in node:
                walk(c)
                parts.append(c.tail or "")

    walk(el)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in "".join(parts).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def chapter_blocks(data: bytes) -> list[tuple[str, str]]:
    """Lista ``(tipo, texto)`` dos blocos do capítulo; tipo é ``"h"`` ou ``"p"``."""

    tree = parse(data)
    if tree is None:
        return []
    out = []
    for el in iter_unit_elements(tree.getroot()):
        txt = element_text(el)
        if txt:
            out.append(("h" if localname(el) in HEADING_TAGS else "p", txt))
    return out


def first_heading(data: bytes) -> str:
    tree = parse(data)
    if tree is None:
        return ""
    for el in tree.getroot().iter():
        if localname(el) in HEADING_TAGS:
            txt = element_text(el).replace("\n", " ")
            if txt:
                return txt[:120]
    return ""


def set_language(tree: etree._ElementTree, lang: str, rtl: bool) -> None:
    root = tree.getroot()
    root.set("lang", lang)
    root.set(XML_LANG, lang)
    for el in root.iter():
        if localname(el) == "body":
            if rtl:
                el.set("dir", "rtl")
            elif el.get("dir") == "rtl":
                del el.attrib["dir"]
            break


def make_chapter(title: str, blocks: list[tuple[str, str]], lang: str = "") -> bytes:
    """Gera um capítulo XHTML a partir de blocos ``(tipo, texto)``."""

    E = f"{{{XHTML_NS}}}"
    nsmap = {None: XHTML_NS, "epub": "http://www.idpf.org/2007/ops"}
    root = etree.Element(E + "html", nsmap=nsmap)
    if lang:
        root.set("lang", lang)
        root.set(XML_LANG, lang)
    head = etree.SubElement(root, E + "head")
    meta = etree.SubElement(head, E + "meta")
    meta.set("charset", "utf-8")
    t = etree.SubElement(head, E + "title")
    t.text = title
    link = etree.SubElement(head, E + "link", rel="stylesheet", href="style.css", type="text/css")
    link.tail = None
    body = etree.SubElement(root, E + "body")
    for kind, text in blocks:
        el = etree.SubElement(body, E + ("h2" if kind == "h" else "p"))
        el.text = text
    doc = etree.tostring(root, encoding="utf-8", xml_declaration=True, doctype="<!DOCTYPE html>")
    return doc
