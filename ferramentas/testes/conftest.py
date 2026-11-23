import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def sample_epub(tmp_path):
    """EPUB com imagem, CSS, itálico, link e sumário (nav + ncx)."""
    import zipfile

    p = tmp_path / "livro.epub"
    ch1 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><title>c1</title><link rel="stylesheet" href="s.css"/></head>
<body><h1>Chapter One</h1>
<p>It was the <em>best</em> of times, it was the <a href="#n1">worst</a> of times.&nbsp;Really.</p>
<p><img src="img/x.png" alt=""/></p>
<p>Elizabeth walked to the market with Jane, and Elizabeth bought bread for the whole family that morning.</p>
</body></html>"""
    ch2 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c2</title></head>
<body><h1>Chapter Two</h1><p>The next day was quiet.</p><ul><li>First item</li><li>Second item</li></ul></body></html>"""
    nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>nav</title></head>
<body><nav epub:type="toc"><ol><li><a href="c1.xhtml">Chapter One</a></li><li><a href="c2.xhtml">Chapter Two</a></li></ol></nav></body></html>"""
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>My Book</text></docTitle>
<navMap><navPoint id="a" playOrder="1"><navLabel><text>Chapter One</text></navLabel><content src="c1.xhtml"/></navPoint></navMap></ncx>"""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id" xml:lang="en">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x</dc:identifier>
<dc:title>My Book</dc:title><dc:creator>Jane Doe</dc:creator><dc:language>en</dc:language></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="s.css" media-type="text/css"/><item id="img" href="img/x.png" media-type="image/png"/></manifest>
<spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine></package>"""
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>""")
        z.writestr("OPS/content.opf", opf)
        z.writestr("OPS/c1.xhtml", ch1)
        z.writestr("OPS/c2.xhtml", ch2)
        z.writestr("OPS/nav.xhtml", nav)
        z.writestr("OPS/toc.ncx", ncx)
        z.writestr("OPS/s.css", "p{color:black}")
        z.writestr("OPS/img/x.png", b"\x89PNG fake")
    return p
