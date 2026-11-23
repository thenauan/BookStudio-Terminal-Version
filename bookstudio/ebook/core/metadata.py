"""Metadados do livro: leitura e escrita no content.opf (EPUB 2 e 3).

Ao reescrever um EPUB existente, só os campos que o usuário editou são tocados;
o resto do OPF (identificadores, refinamentos, capa…) fica como estava.
A série segue o padrão do Calibre (``calibre:series``), que Kindle/Kobo/KOReader
e o próprio Calibre entendem, e também o ``belongs-to-collection`` do EPUB 3.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, fields

from lxml import etree

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NS = {"opf": OPF_NS, "dc": DC_NS}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass
class Metadata:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    language: str = ""
    publisher: str = ""
    year: str = ""
    description: str = ""
    subjects: list[str] = field(default_factory=list)
    series: str = ""
    series_index: str = ""
    isbn: str = ""

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def update(self, values: dict) -> None:
        for k, v in values.items():
            if k in self.__dataclass_fields__:
                setattr(self, k, list(v) if isinstance(v, list) else v)


# ----------------------------------------------------------------- leitura


def _text(el) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


def _description(el) -> str:
    if el is None:
        return ""
    raw = "".join(el.itertext()) if len(el) else (el.text or "")
    # sinopses costumam vir em HTML escapado: tira as tags e normaliza espaços
    raw = re.sub(r"<br\s*/?>|</p>|</div>", "\n", raw, flags=re.I)
    raw = re.sub(r"</?(p|div|span|b|i|u|em|strong|small|a|ul|ol|li|h[1-6]|blockquote|font)\b[^>]*>", "",
                 raw, flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", raw)).strip()


def _looks_isbn(el) -> bool:
    txt = _text(el)
    scheme = (el.get(f"{{{OPF_NS}}}scheme") or el.get("scheme") or "").lower()
    if scheme == "isbn" or txt.lower().startswith(("urn:isbn:", "isbn:")):
        return True
    # muitas editoras põem o ISBN-13 cru, sem esquema (ex.: 9788491234567)
    return bool(re.fullmatch(r"97[89]\d{10}", re.sub(r"[-\s]", "", txt)))


def _isbn_of(md) -> str:
    for el in md.findall("dc:identifier", NS):
        if _looks_isbn(el):
            return re.sub(r"[-\s]", "", re.sub(r"^(urn:)?isbn:", "", _text(el), flags=re.I))
    return ""


def read_opf(root) -> Metadata:
    md = root.find("opf:metadata", NS)
    if md is None:
        md = root.find(".//opf:metadata", NS)
    if md is None:
        return Metadata()
    m = Metadata(
        title=_text(md.find("dc:title", NS)),
        authors=[_text(a) for a in md.findall("dc:creator", NS) if _text(a)],
        language=_text(md.find("dc:language", NS)),
        publisher=_text(md.find("dc:publisher", NS)),
        description=_description(md.find("dc:description", NS)),
        subjects=[_text(s) for s in md.findall("dc:subject", NS) if _text(s)],
        isbn=_isbn_of(md),
    )
    date = _text(md.find("dc:date", NS))
    y = re.match(r"\d{4}", date)
    m.year = y.group(0) if y else ""
    for meta in md.findall("opf:meta", NS):
        name, prop = meta.get("name"), meta.get("property")
        if name == "calibre:series":
            m.series = meta.get("content", "").strip()
        elif name == "calibre:series_index":
            m.series_index = _fmt_index(meta.get("content", ""))
        elif prop == "belongs-to-collection" and not m.series:
            m.series = _text(meta)
            cid = meta.get("id")
            for r in md.findall("opf:meta", NS):
                if cid and r.get("refines") == f"#{cid}" and r.get("property") == "group-position":
                    m.series_index = _fmt_index(_text(r))
    return m


def _fmt_index(v: str) -> str:
    try:
        f = float(v.replace(",", "."))
    except ValueError:
        return ""
    return str(int(f)) if f.is_integer() else str(f)


# ----------------------------------------------------------------- escrita


def _remove(el) -> None:
    """Remove o elemento e os <meta refines="#id"> que apontam para ele (EPUB 3)."""
    parent = el.getparent()
    eid = el.get("id")
    if eid is not None:
        for r in parent.findall("opf:meta", NS):
            if r.get("refines") == f"#{eid}":
                parent.remove(r)
    parent.remove(el)


def _set_single(md, tag: str, value: str) -> None:
    els = md.findall(f"dc:{tag}", NS)
    if not value:
        for el in els:
            _remove(el)
        return
    if els:
        els[0].text = value
        for el in els[0]:  # algum filho estranho (HTML dentro da sinopse)
            els[0].remove(el)
        for extra in els[1:]:
            if tag != "date":  # várias datas (EPUB 2 com opf:event): mantém as outras
                _remove(extra)
    else:
        etree.SubElement(md, f"{{{DC_NS}}}{tag}").text = value


def _set_list(md, tag: str, values: list[str]) -> None:
    old = md.findall(f"dc:{tag}", NS)
    for i, v in enumerate(values):
        if i < len(old):
            old[i].text = v  # mantém atributos (opf:role, file-as…) dos que continuam
        else:
            etree.SubElement(md, f"{{{DC_NS}}}{tag}").text = v
    for el in old[len(values):]:
        _remove(el)


def _set_isbn(md, unique_id: str | None, isbn: str, epub3: bool) -> None:
    """EPUB 3 usa ``urn:isbn:…`` (o opf:scheme não existe mais); EPUB 2 usa opf:scheme="ISBN"."""
    found = next((el for el in md.findall("dc:identifier", NS) if _looks_isbn(el)), None)
    is_main = found is not None and bool(unique_id) and found.get("id") == unique_id
    if not isbn:
        if is_main:  # identificador principal do livro: nunca remove, só troca
            found.text = f"urn:uuid:{uuid.uuid4()}"
            found.attrib.pop(f"{{{OPF_NS}}}scheme", None)
        elif found is not None:
            _remove(found)
        return
    if found is None:
        found = etree.SubElement(md, f"{{{DC_NS}}}identifier")
    if epub3:
        found.text = f"urn:isbn:{isbn}"
        found.attrib.pop(f"{{{OPF_NS}}}scheme", None)
    else:
        found.text = isbn
        found.set(f"{{{OPF_NS}}}scheme", "ISBN")


def _set_series(md, epub3: bool, series: str, index: str) -> None:
    for meta in md.findall("opf:meta", NS):
        if meta.get("name") in ("calibre:series", "calibre:series_index") or \
                meta.get("property") == "belongs-to-collection":
            _remove(meta)
    if not series:
        return
    etree.SubElement(md, f"{{{OPF_NS}}}meta", name="calibre:series", content=series)
    if index:
        etree.SubElement(md, f"{{{OPF_NS}}}meta", name="calibre:series_index", content=index)
    if epub3:
        cid = f"serie-{uuid.uuid4().hex[:6]}"
        c = etree.SubElement(md, f"{{{OPF_NS}}}meta", property="belongs-to-collection", id=cid)
        c.text = series
        t = etree.SubElement(md, f"{{{OPF_NS}}}meta", property="collection-type", refines=f"#{cid}")
        t.text = "series"
        if index:
            p = etree.SubElement(md, f"{{{OPF_NS}}}meta", property="group-position", refines=f"#{cid}")
            p.text = index


def apply_opf(root, meta: Metadata, edited: set[str]) -> None:
    """Grava no OPF só os campos em ``edited``."""
    md = root.find("opf:metadata", NS)
    if md is None:
        md = root.find(".//opf:metadata", NS)
    if md is None:
        md = etree.SubElement(root, f"{{{OPF_NS}}}metadata")
    if "title" in edited:
        _set_single(md, "title", meta.title)
    if "authors" in edited:
        _set_list(md, "creator", meta.authors)
    if "language" in edited and meta.language:
        if root.get(XML_LANG) is not None:
            root.set(XML_LANG, meta.language)
        _set_single(md, "language", meta.language)
    if "publisher" in edited:
        _set_single(md, "publisher", meta.publisher)
    if "year" in edited:
        _set_single(md, "date", meta.year)
    if "description" in edited:
        _set_single(md, "description", meta.description)
    if "subjects" in edited:
        _set_list(md, "subject", meta.subjects)
    epub3 = (root.get("version") or "").startswith("3")
    if "isbn" in edited:
        _set_isbn(md, root.get("unique-identifier"), meta.isbn, epub3)
    if "series" in edited or "series_index" in edited:
        _set_series(md, epub3, meta.series, meta.series_index)
