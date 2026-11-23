"""Escritor de EPUB.

- Livro que veio de EPUB: copia o arquivo original inteiro (imagens, estilos,
  fontes, metadados) e só troca os capítulos alterados e o idioma.
- Livro novo (TXT/PDF): monta um EPUB3 compatível com EPUB2 (nav + toc.ncx).
"""

from __future__ import annotations

import posixpath
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from ..metadata import apply_opf
from ..model import Book
from ..xhtml import first_heading

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
_PARSER = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=True)

CSS = """body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2, h3 { font-family: sans-serif; line-height: 1.2; margin: 1.5em 0 0.8em; }
p { margin: 0; text-indent: 1.5em; text-align: justify; }
h2 + p, p:first-child { text-indent: 0; }
"""

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def _update_opf(data: bytes, book: Book) -> bytes:
    # idioma: o que o usuário digitou nos metadados vale mais que o da tradução
    lang = None if "language" in book.edited else book.target_language
    if not lang and not book.edited:
        return data
    root = etree.fromstring(data, _PARSER)
    if root is None:
        return data
    if book.edited:
        apply_opf(root, book.metadata(), book.edited)
    if not lang:
        return etree.tostring(root, encoding="utf-8", xml_declaration=True)
    if root.get("{http://www.w3.org/XML/1998/namespace}lang") is not None:
        root.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    langs = root.findall(f".//{{{DC_NS}}}language")
    if langs:
        langs[0].text = lang
        for extra in langs[1:]:
            extra.getparent().remove(extra)
    else:
        md = root.find(f"{{{OPF_NS}}}metadata")
        if md is not None:
            el = etree.SubElement(md, f"{{{DC_NS}}}language")
            el.text = lang
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def _copy_epub(book: Book, out: Path) -> None:
    replaced = {c.path: c.data for c in book.chapters}
    with zipfile.ZipFile(book.source_epub) as zin, zipfile.ZipFile(out, "w") as zout:
        # "mimetype" precisa ser o primeiro arquivo e sem compressão
        zout.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for info in zin.infolist():
            if info.filename == "mimetype" or info.is_dir():
                continue
            if info.filename in replaced:
                data = replaced[info.filename]
            elif info.filename == book.opf_path:
                data = _update_opf(zin.read(info), book)
            else:
                data = zin.read(info)
            zout.writestr(info.filename, data, compress_type=zipfile.ZIP_DEFLATED)


def _new_epub(book: Book, out: Path) -> None:
    if "language" in book.edited and book.language:
        lang = book.language
    else:
        lang = book.target_language or book.language or "und"
    uid = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    chapters = book.content_chapters
    items, spine, nav_li, ncx_points = [], [], [], []
    for i, ch in enumerate(chapters, 1):
        href = posixpath.relpath(ch.path, "OEBPS")
        title = first_heading(ch.data) or ch.title or f"Capítulo {i}"
        items.append(f'<item id="c{i}" href="{escape(href)}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
        nav_li.append(f'<li><a href="{escape(href)}">{escape(title)}</a></li>')
        ncx_points.append(
            f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{escape(title)}</text></navLabel>'
            f'<content src="{escape(href)}"/></navPoint>'
        )
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="{OPF_NS}" version="3.0" unique-identifier="uid" xml:lang="{escape(lang)}">
  <metadata xmlns:dc="{DC_NS}">
    <dc:identifier id="uid">{uid}</dc:identifier>
    <dc:language>{escape(lang)}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    {"".join(items)}
  </manifest>
  <spine toc="ncx">{"".join(spine)}</spine>
</package>
"""
    # título, autores e o resto dos metadados entram pelo mesmo código da edição
    root = etree.fromstring(opf.encode("utf-8"), _PARSER)
    md = book.metadata()
    md.language = lang
    apply_opf(root, md, set(md.to_dict()) - {"language"})
    opf = etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True)
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{escape(lang)}" xml:lang="{escape(lang)}">
<head><meta charset="utf-8"/><title>{escape(book.title)}</title></head>
<body><nav epub:type="toc" id="toc"><h1>{escape(book.title)}</h1><ol>{"".join(nav_li)}</ol></nav></body>
</html>
"""
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{uid}"/></head>
<docTitle><text>{escape(book.title)}</text></docTitle>
<navMap>{"".join(ncx_points)}</navMap>
</ncx>
"""
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for ch in chapters:
            z.writestr(ch.path, ch.data, compress_type=zipfile.ZIP_DEFLATED)


def write_epub(book: Book, out: Path) -> None:
    if book.source_epub is not None and Path(book.source_epub).exists():
        _copy_epub(book, out)
    else:
        _new_epub(book, out)
