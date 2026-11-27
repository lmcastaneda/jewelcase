from datetime import datetime, timedelta

import pytest

from jewelcase.core import catalog, config
from jewelcase.core.catalog import GameRow
from jewelcase.core.config import Config
from jewelcase.core.smartlists import UserList, apply, make_id, resolve

NOW = datetime(2026, 6, 1, 12, 0)
MB = 1024 * 1024


def row(system="gba", stem="Game", title=None, state="on_device", size=MB, seconds=0, last=None,
        on_device=True, in_library=True):
    return GameRow(system, stem, title or stem, state, None, None, size, 1 if seconds else 0, seconds, last,
                   on_device, on_device, in_library)


ROWS = [
    row("gba", "Minish Hat", size=2 * MB, seconds=5 * 3600, last=NOW - timedelta(days=3)),
    row("snes", "Super Plumber Land", size=MB, seconds=0),
    row("ps2", "Clockwork Citadel", size=4000 * MB, seconds=30 * 60, last=NOW - timedelta(days=200)),
    row("ps2", "Tidewater", size=500 * MB, seconds=0, on_device=False, state="library_only"),
    row("psx", "Glass Garden", size=300 * MB, seconds=90, last=NOW - timedelta(days=10),
        in_library=False, state="device_only"),
]


def keys(rows):
    return [r.stem for r in rows]


def test_a_list_with_no_conditions_shows_every_game():
    assert keys(apply(ROWS, UserList("user:a", "A"), NOW)) == keys(ROWS)


def test_systems_is_one_of_a_chosen_set():
    got = apply(ROWS, UserList("user:a", "A", systems=("gba", "psx")), NOW)
    assert keys(got) == ["Minish Hat", "Glass Garden"]


def test_on_the_handheld_yes_or_no():
    assert keys(apply(ROWS, UserList("user:a", "A", on_device=False), NOW)) == ["Tidewater"]
    assert "Tidewater" not in keys(apply(ROWS, UserList("user:a", "A", on_device=True), NOW))


def test_in_the_library_yes_or_no():
    assert keys(apply(ROWS, UserList("user:a", "A", in_library=False), NOW)) == ["Glass Garden"]
    assert "Glass Garden" not in keys(apply(ROWS, UserList("user:a", "A", in_library=True), NOW))


def test_never_played_uses_the_never_played_floor():
    ul = UserList("user:a", "A", play_time="never")
    assert keys(apply(ROWS, ul, NOW)) == ["Super Plumber Land", "Tidewater"]    # 90 s is a real play
    assert keys(apply(ROWS, ul, NOW, never_seconds=120))[-1] == "Glass Garden"


def test_play_time_under_some_minutes_includes_games_without_play_data():
    ul = UserList("user:a", "A", play_time="under", play_amount=45)
    assert keys(apply(ROWS, ul, NOW)) == ["Super Plumber Land", "Clockwork Citadel", "Tidewater", "Glass Garden"]


def test_play_time_at_least_some_hours():
    ul = UserList("user:a", "A", play_time="over", play_amount=5)
    assert keys(apply(ROWS, ul, NOW)) == ["Minish Hat"]


def test_last_played_more_than_days_ago_counts_games_never_played():
    ul = UserList("user:a", "A", last_played="before", last_days=30)
    assert keys(apply(ROWS, ul, NOW)) == ["Super Plumber Land", "Clockwork Citadel", "Tidewater"]


def test_last_played_within_the_last_days_needs_a_date():
    ul = UserList("user:a", "A", last_played="within", last_days=30)
    assert keys(apply(ROWS, ul, NOW)) == ["Minish Hat", "Glass Garden"]


def test_size_at_least_and_at_most():
    assert keys(apply(ROWS, UserList("user:a", "A", size="at_least", size_mb=500), NOW)) == [
        "Clockwork Citadel", "Tidewater"]
    assert keys(apply(ROWS, UserList("user:a", "A", size="at_most", size_mb=2), NOW)) == [
        "Minish Hat", "Super Plumber Land"]


def test_title_contains_ignores_case():
    assert keys(apply(ROWS, UserList("user:a", "A", title_has="GLASS"), NOW)) == ["Glass Garden"]
    assert keys(apply(ROWS, UserList("user:a", "A", title_has="  garden "), NOW)) == ["Glass Garden"]


def test_every_condition_must_hold():
    ul = UserList("user:a", "A", systems=("ps2", "psx"), on_device=True, play_time="under", play_amount=45,
                  size="at_most", size_mb=1000, title_has="glass")
    assert keys(apply(ROWS, ul, NOW)) == ["Glass Garden"]
    assert apply(ROWS, UserList("user:a", "A", systems=("gba",), size="at_least", size_mb=500), NOW) == []


def test_sorting_by_size_play_time_title_and_last_played():
    by = lambda sort, desc=False: keys(apply(ROWS, UserList("user:a", "A", sort=sort, descending=desc), NOW))
    assert by("size", True)[:2] == ["Clockwork Citadel", "Tidewater"]
    assert by("title")[:2] == ["Clockwork Citadel", "Glass Garden"]
    assert by("played", True)[0] == "Minish Hat"
    assert by("last", True)[:2] == ["Minish Hat", "Glass Garden"]
    assert by("") == keys(ROWS)


def test_ids_come_from_the_name_and_are_unique():
    assert make_id("Short Games!", []) == "user:short-games"
    assert make_id("Short Games", ["user:short-games"]) == "user:short-games-2"
    assert make_id("Short Games", ["user:short-games", "user:short-games-2"]) == "user:short-games-3"
    assert make_id("???", []) == "user:list"


def test_catalog_smart_works_for_the_owners_lists(tmp_path):
    cfg = Config(user_lists=[UserList("user:big", "Big", size="at_least", size_mb=1000)])
    assert keys(catalog.smart(ROWS, "user:big", cfg, NOW)) == ["Clockwork Citadel"]
    with pytest.raises(KeyError):
        catalog.smart(ROWS, "user:gone", cfg, NOW)


def test_resolve_finds_a_list_by_id_or_by_name():
    cfg = Config(user_lists=[UserList("user:big", "Big Ones")])
    assert resolve(cfg, "user:big") == "user:big"
    assert resolve(cfg, "big ones") == "user:big"
    assert resolve(cfg, "never_played") == "never_played"
    assert resolve(cfg, "Never Played") == "never_played"
    assert resolve(cfg, "nothing") is None


# ---------------------------------------------------------------- the settings file
def test_lists_survive_a_round_trip(tmp_path):
    lists = [
        UserList("user:short", "Short & sweet", systems=("gba", "snes"), on_device=True, in_library=False,
                 play_time="under", play_amount=30, last_played="before", last_days=60, size="at_most",
                 size_mb=512, title_has='say "hi"', sort="size", descending=True),
        UserList("user:plain", "Plain"),
        UserList("user:long", "Long", play_time="over", play_amount=10, last_played="within", last_days=7,
                 size="at_least", size_mb=2048),
    ]
    path = tmp_path / "config.toml"
    config.save(Config(user_lists=lists), path)
    assert config.load(path).user_lists == lists


def test_lists_are_written_in_a_readable_shape(tmp_path):
    path = tmp_path / "config.toml"
    config.save(Config(user_lists=[UserList("user:short", "Short", play_time="under", play_amount=30)]), path)
    text = path.read_text()
    assert '[[lists]]' in text and 'name = "Short"' in text and "play_minutes = 30" in text


def test_lists_and_systems_share_a_file(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config(user_lists=[UserList("user:a", "A")], extra_systems={"listtestbox": {"name": "List Test Box"}})
    config.save(cfg, path)
    back = config.load(path)
    assert [u.name for u in back.user_lists] == ["A"] and "listtestbox" in back.extra_systems


def test_a_hand_edited_file_with_bad_entries_still_loads(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('''
never_played_seconds = 60

[[lists]]
name = "Good"
play_time = "under"
play_minutes = 20

[[lists]]
play_time = "under"

[[lists]]
name = 12

[[lists]]
name = "Odd values"
systems = "gba"
on_device = "maybe"
play_time = "sometimes"
play_hours = "lots"
last_played = "before"
last_days = -4
size = "at_least"
size_mb = "big"
title_has = 5
sort = "colour"

[[lists]]
name = "Good"
''')
    lists = config.load(path).user_lists
    assert [u.name for u in lists] == ["Good", "Odd values", "Good"]
    assert lists[0].play_time == "under" and lists[0].play_amount == 20
    odd = lists[1]
    assert odd.systems == () and odd.on_device is None and odd.play_time == "any"
    assert odd.last_played == "any" and odd.size == "any" and odd.title_has == "" and odd.sort == ""
    assert len({u.id for u in lists}) == 3                      # ids are unique even for equal names


def test_lists_as_something_other_than_a_table_array_are_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('lists = "nope"\n')
    assert config.load(path).user_lists == []
    path.write_text('lists = [1, 2]\n')
    assert config.load(path).user_lists == []


def test_ids_in_the_file_are_kept_when_valid(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[lists]]\nid = "user:keep"\nname = "Renamed since"\n\n[[lists]]\nid = "user:keep"\nname = "Dup"\n'
                    '\n[[lists]]\nid = "never_played"\nname = "Sneaky"\n')
    lists = config.load(path).user_lists
    assert lists[0].id == "user:keep"
    assert len({u.id for u in lists}) == 3 and all(u.id.startswith("user:") for u in lists)
