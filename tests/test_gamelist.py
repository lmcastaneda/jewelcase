from datetime import datetime

from jewelcase.core import gamelist

XML = """<?xml version="1.0"?>
<gameList>
\t<game>
\t\t<path>./Final Fantasy VII (USA).m3u</path>
\t\t<name>Final Fantasy VII</name>
\t\t<playcount>3</playcount>
\t\t<lastplayed>20261120T213000</lastplayed>
\t\t<playtime>5400</playtime>
\t</game>
\t<game>
\t\t<path>./Ratchet &amp; Clank (USA).chd</path>
\t\t<name>Ratchet &amp; Clank</name>
\t</game>
\t<folder>
\t\t<path>./sub</path>
\t\t<name>sub</name>
\t</folder>
\t<game>
\t\t<path>./Baldur&apos;s Gate (USA).chd</path>
\t\t<name>Baldur's Gate</name>
\t\t<playcount>1</playcount>
\t\t<playtime>17</playtime>
\t</game>
</gameList>
"""


def test_parse_stats():
    e = gamelist.parse(XML)["Final Fantasy VII (USA).m3u"]
    assert (e.name, e.plays, e.seconds) == ("Final Fantasy VII", 3, 5400)
    assert e.last_played == datetime(2026, 11, 20, 21, 30, 0)


def test_parse_missing_tags_are_zero():
    e = gamelist.parse(XML)["Ratchet & Clank (USA).chd"]
    assert (e.name, e.plays, e.seconds, e.last_played) == ("Ratchet & Clank", 0, 0, None)


def test_parse_unescapes_apostrophe():
    assert "Baldur's Gate (USA).chd" in gamelist.parse(XML)


def test_parse_garbage_is_empty():
    assert gamelist.parse("") == {}
    assert gamelist.parse("not xml at all <game>") == {}


def test_drop_removes_only_named():
    out, removed = gamelist.drop(XML, {"Final Fantasy VII (USA).m3u"})
    assert removed == ["Final Fantasy VII (USA).m3u"]
    assert set(gamelist.parse(out)) == {"Ratchet & Clank (USA).chd", "Baldur's Gate (USA).chd"}


def test_drop_matches_ampersand_names():
    out, removed = gamelist.drop(XML, {"Ratchet & Clank (USA).chd", "Baldur's Gate (USA).chd"})
    assert sorted(removed) == ["Baldur's Gate (USA).chd", "Ratchet & Clank (USA).chd"]
    assert set(gamelist.parse(out)) == {"Final Fantasy VII (USA).m3u"}


def test_drop_preserves_rest_bytes():
    block = ("\t<game>\n\t\t<path>./Ratchet &amp; Clank (USA).chd</path>\n"
             "\t\t<name>Ratchet &amp; Clank</name>\n\t</game>\n")
    out, _ = gamelist.drop(XML, {"Ratchet & Clank (USA).chd"})
    assert out == XML.replace(block, "")
    assert "<folder>" in out and out.startswith('<?xml version="1.0"?>')


def test_drop_crlf():
    crlf = XML.replace("\n", "\r\n")
    out, removed = gamelist.drop(crlf, {"Final Fantasy VII (USA).m3u"})
    assert len(removed) == 1
    assert "\n\n" not in out.replace("\r\n", "\n")
    assert out.count("\r\n") == crlf.count("\r\n") - 7


def test_drop_nothing_returns_same_text():
    out, removed = gamelist.drop(XML, {"Not There.chd"})
    assert out == XML and removed == []
