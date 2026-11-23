from bookstudio.ebook.core import xhtml

DOC = b"""<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Title</h1><p>He said <em>hello &amp; <b>bye</b></em>&nbsp;now<br/>ok.</p><p>123</p><pre>code here</pre></body></html>"""


def test_units_and_placeholders():
    tree = xhtml.parse(DOC)
    units = xhtml.collect_units(tree)
    assert [u.plain for u in units] == ["Title", "He said hello & bye now ok."]
    u = units[1]
    assert u.text == "He said <t1>hello &amp; <t2>bye</t2></t1> now<t3/>ok."
    assert xhtml.apply_translation(u, "Ele disse <t1>olá &amp; <t2>tchau</t2></t1> agora<t3/>ok.")
    out = xhtml.serialize(tree).decode()
    assert "<em>olá &amp; <b>tchau</b></em> agora<br/>ok." in out


def test_broken_placeholders_are_rejected_without_changes():
    tree = xhtml.parse(DOC)
    u = xhtml.collect_units(tree)[1]
    before = xhtml.serialize(tree)
    assert not xhtml.apply_translation(u, "Ele disse <t1>olá</t2> tchau")  # aninhamento errado
    assert not xhtml.apply_translation(u, "Ele disse olá")  # marcadores sumiram
    assert not xhtml.apply_translation(u, "<t1>a</t1><t1>b</t1><t2>c</t2><t3/>")  # repetido
    assert xhtml.serialize(tree) == before


def test_tolerates_spaces_inside_markers():
    tree = xhtml.parse(DOC)
    u = xhtml.collect_units(tree)[1]
    assert xhtml.apply_translation(u, "Ele < t1 >olá <t2>tchau</ t2></t1> agora <t3 />ok.")


def test_plain_fallback_keeps_empty_elements():
    tree = xhtml.parse(b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>A <i>b</i><img src="x.png"/></p></body></html>')
    u = xhtml.collect_units(tree)[0]
    xhtml.apply_plain_fallback(u, "Um <t1>b")
    out = xhtml.serialize(tree).decode()
    assert "Um b" in out and 'src="x.png"' in out


def test_make_chapter_escapes_text():
    data = xhtml.make_chapter("T & <x>", [("h", "T & <x>"), ("p", "a < b")])
    assert xhtml.chapter_blocks(data) == [("h", "T & <x>"), ("p", "a < b")]
