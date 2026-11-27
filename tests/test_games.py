from jewelcase.core.games import Unit, cue_files, group_units, m3u_files

CUE = 'FILE "MTV Music Generator (USA) (Track 1).bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n' \
      'FILE "MTV Music Generator (USA) (Track 2).bin" BINARY\r\n  TRACK 02 AUDIO\r\n'


def reader(texts):
    return lambda name: texts[name]


def by_stem(units):
    return {u.stem: u for u in units}


def test_cue_owns_bins():
    names = ["MTV Music Generator (USA).cue", "MTV Music Generator (USA) (Track 1).bin",
             "MTV Music Generator (USA) (Track 2).bin"]
    units = group_units(names, reader({names[0]: CUE}))
    assert len(units) == 1
    u = units[0]
    assert u.stem == "MTV Music Generator (USA)"
    assert u.head == names[0] and u.ext == ".cue"
    assert set(u.files) == set(names) and u.files[0] == u.head
    assert u.missing == ()


def test_m3u_owns_discs():
    discs = [f"Final Fantasy VII (USA) (Disc {i}).chd" for i in (1, 2, 3)]
    m3u = "Final Fantasy VII (USA).m3u"
    units = group_units([m3u, *discs], reader({m3u: "\n".join(discs) + "\n"}))
    assert len(units) == 1
    assert units[0].stem == "Final Fantasy VII (USA)"
    assert units[0].files == (m3u, *discs)


def test_m3u_of_cues():
    texts = {"G.m3u": "G (Disc 1).cue\nG (Disc 2).cue\n",
             "G (Disc 1).cue": 'FILE "G (Disc 1).bin" BINARY',
             "G (Disc 2).cue": 'FILE "G (Disc 2).bin" BINARY'}
    names = [*texts, "G (Disc 1).bin", "G (Disc 2).bin"]
    units = group_units(names, reader(texts))
    assert len(units) == 1
    assert set(units[0].files) == set(names)


def test_single_files():
    units = by_stem(group_units(["A (USA).chd", "B (USA).iso", "B (USA).7z"], reader({})))
    assert set(units) == {"A (USA)", "B (USA)"} or len(units) == 2
    all_units = group_units(["A (USA).chd", "B (USA).iso", "B (USA).7z"], reader({}))
    assert sorted((u.stem, u.ext) for u in all_units) == [
        ("A (USA)", ".chd"), ("B (USA)", ".7z"), ("B (USA)", ".iso")]


def test_shared_file_belongs_to_both_units():
    texts = {"One.cue": 'FILE "shared.bin" BINARY', "Two.cue": 'FILE "shared.bin" BINARY'}
    units = by_stem(group_units([*texts, "shared.bin"], reader(texts)))
    assert set(units) == {"One", "Two"}
    assert "shared.bin" in units["One"].files and "shared.bin" in units["Two"].files


def test_missing_reference_reported():
    texts = {"X.cue": 'FILE "X.bin" BINARY\nFILE "X (Track 2).bin" BINARY'}
    u = group_units(["X.cue", "X.bin"], reader(texts))[0]
    assert u.files == ("X.cue", "X.bin")
    assert u.missing == ("X (Track 2).bin",)


def test_apostrophe_and_ampersand_names():
    m3u = "Baldur's Gate & Co (USA).m3u"
    discs = ["Baldur's Gate & Co (USA) (Disc 1).chd", "Baldur's Gate & Co (USA) (Disc 2).chd"]
    units = group_units([m3u, *discs, "Pokémon (USA).zip"], reader({m3u: "\n".join(discs)}))
    assert sorted(u.stem for u in units) == ["Baldur's Gate & Co (USA)", "Pokémon (USA)"]


def test_cue_with_crlf_and_quotes():
    assert cue_files(CUE) == ["MTV Music Generator (USA) (Track 1).bin",
                              "MTV Music Generator (USA) (Track 2).bin"]
    assert cue_files("FILE track.bin BINARY\n") == ["track.bin"]
    assert cue_files('file "sub\\dir\\a.bin" BINARY') == ["a.bin"]


def test_m3u_skips_comments_and_paths():
    assert m3u_files("# comment\n\n./sub/Disc 1.chd\r\nDisc 2.chd\n") == ["Disc 1.chd", "Disc 2.chd"]


def test_unreadable_playlist_is_a_single_file():
    def boom(name):
        raise OSError("read error")
    units = group_units(["X.m3u", "X (Disc 1).chd"], boom)
    assert sorted(u.head for u in units) == ["X (Disc 1).chd", "X.m3u"]


def test_unit_is_hashable_value():
    assert Unit("a", "a.chd", ("a.chd",)) == Unit("a", "a.chd", ("a.chd",))
