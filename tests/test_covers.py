"""Covers downloaded for games ES-DE has none for: names, requests, misses, and where they are kept."""
import time

import pytest

from jewelcase.core import covers
from jewelcase.core.art import Art

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20 + b"\0\0\0\0IEND\xaeB`\x82"
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 20 + b"\xff\xd9"


class Fake:
    """A stand-in for the network: url -> bytes, anything else is a 404 (None)."""

    def __init__(self, files=None, fail=None):
        self.files, self.fail, self.asked = dict(files or {}), fail, []

    def __call__(self, url):
        self.asked.append(url)
        if self.fail:
            raise OSError(self.fail)
        return self.files.get(url)


BASE = "https://raw.githubusercontent.com/libretro-thumbnails/"


@pytest.fixture
def art(tmp_path):
    return Art(tmp_path / "art")


# ---------------------------------------------------------------- names and addresses
def test_the_address_names_the_repository_of_the_system_and_the_game():
    assert covers.url("ps2", "God of War II (USA)") == \
        BASE + "Sony_-_PlayStation_2/master/Named_Boxarts/God%20of%20War%20II%20%28USA%29.png"


def test_characters_the_thumbnail_names_do_not_allow_become_underscores():
    assert covers.thumbnail_name('Baldur\'s Gate & Co: "A" <b> a/b?*|\\`') == "Baldur's Gate _ Co_ _A_ _b_ a_b" + "_" * 5


def test_every_listed_system_has_a_repository_and_switch_has_none():
    for folder in ("nes", "snes", "gb", "gbc", "gba", "n64", "nds", "n3ds", "gamecube", "wii", "wiiu", "mastersystem",
                   "megadrive", "segacd", "saturn", "dreamcast", "psx", "ps2", "ps3", "psp", "psvita", "neogeocd",
                   "pcengine", "arcade", "cps1", "cps2", "mame", "atari2600", "atari7800", "atarijaguar"):
        assert covers.REPOS[folder]
    assert covers.REPOS["arcade"] == covers.REPOS["cps1"] == covers.REPOS["cps2"] == "FBNeo_-_Arcade_Games"
    assert covers.url("switch", "Game (USA)") is None


def test_revision_version_and_language_groups_come_off_from_the_right_keeping_the_region():
    assert covers.candidates("Game (USA) (En,Fr) (Rev 1)") == ["Game (USA) (En,Fr) (Rev 1)", "Game (USA) (En,Fr)",
                                                                "Game (USA)"]
    assert covers.candidates("Game (Europe) (v1.01)") == ["Game (Europe) (v1.01)", "Game (Europe)"]
    assert covers.candidates("Game (USA)") == ["Game (USA)"]
    assert covers.candidates("Game (USA) (Disc 1)") == ["Game (USA) (Disc 1)"]
    assert covers.candidates("Game") == ["Game"]


# ---------------------------------------------------------------- fetching
def test_a_missing_cover_is_downloaded_and_kept_apart_from_es_des_covers(art):
    get = Fake({covers.url("ps2", "Northwind Saga (USA)"): PNG})
    result = covers.fetch_missing(art, [("ps2", "Northwind Saga (USA)")], get)
    assert result.arrived == 1 and result.none == 0
    path = art.cover("ps2", "Northwind Saga (USA)")
    assert path.read_bytes() == PNG and path.parent == art.root / "downloaded" / "ps2"


def test_a_cover_es_de_has_is_not_asked_for_and_wins_over_a_downloaded_one(art, tmp_path):
    (art.root / "downloaded" / "ps2").mkdir(parents=True)
    (art.root / "downloaded" / "ps2" / "A (USA).png").write_bytes(PNG)
    assert art.cover("ps2", "A (USA)").parent.name == "ps2" and "downloaded" in art.cover("ps2", "A (USA)").parts
    own = art.root / "covers" / "ps2"
    own.mkdir(parents=True)
    (own / "A (USA).png").write_bytes(b"from es-de")
    art._index["covers"]["ps2/A (USA).png"] = [1, 1]
    art._by_game = None
    assert art.cover("ps2", "A (USA)").read_bytes() == b"from es-de"
    get = Fake()
    covers.fetch_missing(art, [("ps2", "A (USA)")], get)
    assert get.asked == []


def test_names_are_tried_in_order_and_a_404_means_try_the_next(art):
    get = Fake({covers.url("ps2", "Game (USA)"): JPEG})
    result = covers.fetch_missing(art, [("ps2", "Game (USA) (En,Fr) (Rev 1)")], get)
    assert get.asked == [covers.url("ps2", "Game (USA) (En,Fr) (Rev 1)"), covers.url("ps2", "Game (USA) (En,Fr)"),
                         covers.url("ps2", "Game (USA)")]
    assert result.arrived == 1
    assert art.cover("ps2", "Game (USA) (En,Fr) (Rev 1)").read_bytes() == JPEG


def test_requests_go_one_at_a_time_and_a_system_without_a_repository_is_skipped(art):
    get = Fake()
    result = covers.fetch_missing(art, [("switch", "Zelda (USA)"), ("psx", "A (USA)"), ("psx", "B (USA)")], get)
    assert all("Sony_-_PlayStation/" in u for u in get.asked) and len(get.asked) == 2
    assert result.arrived == 0 and result.none == 3


def test_what_comes_back_must_be_a_picture_of_sane_size(art):
    url = covers.url("ps2", "A (USA)")
    for body in (b"<html>rate limited</html>", b"", PNG[:4], PNG + b"\0" * covers.MAX_BYTES):
        assert covers.fetch_missing(art, [("ps2", "A (USA)")], Fake({url: body}), now=time.time()).arrived == 0
        assert art.cover("ps2", "A (USA)") is None
        art._misses.clear()
    assert not list(art.root.rglob("*.part"))


def test_a_miss_is_remembered_for_thirty_days(art):
    get = Fake()
    now = 1_000_000.0
    covers.fetch_missing(art, [("ps2", "Nope (USA)")], get, now=now)
    first = len(get.asked)
    covers.fetch_missing(art, [("ps2", "Nope (USA)")], get, now=now + 29 * 86400)
    assert len(get.asked) == first
    covers.fetch_missing(art, [("ps2", "Nope (USA)")], get, now=now + 31 * 86400)
    assert len(get.asked) == 2 * first
    again = Art(art.root)                                   # and a fresh start remembers it too
    covers.fetch_missing(again, [("ps2", "Nope (USA)")], get, now=now + 32 * 86400)
    assert len(get.asked) == 2 * first


def test_a_network_failure_stops_the_run_and_is_not_remembered_as_a_miss(art):
    get = Fake(fail="no route")
    result = covers.fetch_missing(art, [("ps2", "A (USA)"), ("ps2", "B (USA)")], get)
    assert len(get.asked) == 1 and result.arrived == 0 and result.failed
    good = Fake({covers.url("ps2", "A (USA)"): PNG})
    assert covers.fetch_missing(art, [("ps2", "A (USA)")], good).arrived == 1


def test_downloaded_covers_survive_a_fresh_start_and_a_run_can_be_told_to_stop(art):
    games = [("ps2", "A (USA)"), ("ps2", "B (USA)")]
    get = Fake({covers.url("ps2", "A (USA)"): PNG, covers.url("ps2", "B (USA)"): PNG})
    told = []
    covers.fetch_missing(art, games, get, changed=lambda: told.append(1), stop=lambda: len(get.asked) >= 1)
    assert len(get.asked) == 1 and told
    assert Art(art.root).cover("ps2", "A (USA)") is not None and Art(art.root).cover("ps2", "B (USA)") is None


def test_counts_say_where_each_cover_comes_from(art):
    (art.root / "covers" / "ps2").mkdir(parents=True)
    (art.root / "covers" / "ps2" / "A (USA).png").write_bytes(b"x")
    art._index["covers"]["ps2/A (USA).png"] = [1, 1]
    covers.fetch_missing(art, [("ps2", "B (USA)")], Fake({covers.url("ps2", "B (USA)"): PNG}))
    assert covers.counts(art, [("ps2", "A (USA)"), ("ps2", "B (USA)"), ("ps2", "C (USA)")]) == (1, 1, 1)


# ---------------------------------------------------------------- review fixes
def test_a_picture_cut_short_is_not_kept_and_ends_the_run_without_a_miss(art):
    url = covers.url("ps2", "A (USA)")
    for body in (PNG[:-12], JPEG[:-2]):
        result = covers.fetch_missing(art, [("ps2", "A (USA)")], Fake({url: body}), now=time.time())
        assert result.arrived == 0 and result.failed
        assert art.cover("ps2", "A (USA)") is None and art.missed_since("ps2", "A (USA)") is None


def test_something_that_is_not_a_picture_at_all_is_not_under_that_name(art):
    result = covers.fetch_missing(art, [("ps2", "A (USA)")], Fake({covers.url("ps2", "A (USA)"): b"<html>"}))
    assert not result.failed and result.none == 1 and art.missed_since("ps2", "A (USA)") is not None


@pytest.mark.parametrize("error", [__import__("http.client").client.IncompleteRead(b"ab", 5),
                                   __import__("http.client").client.BadStatusLine("x")])
def test_an_error_that_is_not_an_oserror_is_a_network_failure_too(art, error):
    def get(url):
        raise error
    result = covers.fetch_missing(art, [("ps2", "A (USA)"), ("ps2", "B (USA)")], get)
    assert result.failed and result.arrived == 0 and art.missed_since("ps2", "A (USA)") is None


def test_the_cover_of_a_game_whose_name_starts_with_a_dot_is_found_after_a_restart(art):
    stem = ".hack Infection (USA)"
    assert covers.fetch_missing(art, [("ps2", stem)], Fake({covers.url("ps2", stem): PNG})).arrived == 1
    assert Art(art.root).downloaded_cover("ps2", stem) is not None
    again = Fake({covers.url("ps2", stem): PNG})
    covers.fetch_missing(Art(art.root), [("ps2", stem)], again)
    assert again.asked == []
