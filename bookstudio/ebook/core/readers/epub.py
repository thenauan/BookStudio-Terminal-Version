"""Leitor de EPUB (2 e 3) feito só com zipfile + lxml."""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from ..metadata import read_opf
from ..model import Book, Chapter, DRMError, EbookError
from ..xhtml import first_heading

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "enc": "http://www.w3.org/2001/04/xmlenc#",
}
# Ofuscação de fontes é permitida; qualquer outra criptografia é DRM.
FONT_OBFUSCATION = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
}
XHTML_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbook+xml"}

_PARSER = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=True)


def _check_drm(z: zipfile.ZipFile) -> None:
    names = set(z.namelist())
    if "META-INF/rights.xml" in names:
        raise DRMError("Este EPUB tem DRM (proteção contra cópia) e não pode ser aberto.")
    if "META-INF/encryption.xml" in names:
        root = etree.fromstring(z.read("META-INF/encryption.xml"), _PARSER)
        if root is None:
            return
        for m in root.iter("{%s}EncryptionMethod" % NS["enc"]):
            if m.get("Algorithm") not in FONT_OBFUSCATION:
                raise DRMError("Este EPUB tem DRM (proteção contra cópia) e não pode ser aberto.")


def opf_location(z: zipfile.ZipFile) -> str:
    try:
        root = etree.fromstring(z.read("META-INF/container.xml"), _PARSER)
        rf = root.find(".//c:rootfile", NS)
        if rf is not None and rf.get("full-path"):
            return rf.get("full-path")
    except KeyError:
        pass
    for name in z.namelist():  # EPUB malformado: procura um .opf qualquer
        if name.lower().endswith(".opf"):
            return name
    raise EbookError("Arquivo EPUB inválido: não encontrei o content.opf.")


def resolve(base_dir: str, href: str) -> str:
    href = unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else posixpath.normpath(href)


def read_epub(path: Path) -> Book:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise EbookError("Arquivo EPUB corrompido ou inválido.") from e
    with z:
        _check_drm(z)
        opf_path = opf_location(z)
        opf = etree.fromstring(z.read(opf_path), _PARSER)
        if opf is None:
            raise EbookError("Arquivo EPUB inválido: content.opf ilegível.")
        base = posixpath.dirname(opf_path)
        names = set(z.namelist())

        title = (opf.findtext(".//dc:title", namespaces=NS) or "").strip() or path.stem
        authors = [a.text.strip() for a in opf.findall(".//dc:creator", NS) if a.text and a.text.strip()]
        language = (opf.findtext(".//dc:language", namespaces=NS) or "").strip()

        manifest = {}
        for item in opf.findall(".//opf:manifest/opf:item", NS):
            manifest[item.get("id")] = item

        chapters: list[Chapter] = []
        seen: set[str] = set()
        for ref in opf.findall(".//opf:spine/opf:itemref", NS):
            item = manifest.get(ref.get("idref"))
            if item is None or item.get("media-type") not in XHTML_TYPES:
                continue
            full = resolve(base, item.get("href", ""))
            if full in seen or full not in names:
                continue
            seen.add(full)
            data = z.read(full)
            chapters.append(Chapter(path=full, data=data, title=first_heading(data), kind="content"))

        # sumários, para os títulos também serem traduzidos
        for item in manifest.values():
            full = resolve(base, item.get("href", ""))
            if full in seen or full not in names:
                continue
            props = item.get("properties") or ""
            if "nav" in props.split():
                chapters.append(Chapter(path=full, data=z.read(full), kind="nav"))
                seen.add(full)
            elif item.get("media-type") == "application/x-dtbncx+xml":
                chapters.append(Chapter(path=full, data=z.read(full), kind="ncx"))
                seen.add(full)
        # se o nav também estava no spine, marca como nav
        for item in manifest.values():
            if "nav" in (item.get("properties") or "").split():
                full = resolve(base, item.get("href", ""))
                for c in chapters:
                    if c.path == full:
                        c.kind = "nav"

    if not any(c.kind == "content" for c in chapters):
        raise EbookError("O EPUB não tem capítulos de texto legíveis.")
    book = Book(
        title=title,
        authors=authors,
        language=language,
        chapters=chapters,
        source_epub=path,
        opf_path=opf_path,
    )
    meta = read_opf(opf).to_dict()
    for k in ("title", "authors", "language"):  # já lidos acima (com o nome do arquivo como reserva)
        meta.pop(k)
    book.set_metadata(meta, mark_edited=False)
    return book
