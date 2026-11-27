"""Game covers and system images, copied from ES-DE on the handheld into Jewelcase's cache."""
import zipfile

import pytest

from jewelcase.core import art as artmod
from jewelcase.core.art import Art
from jewelcase.core.device import PackageInfo

MEDIA = "/sdcard/ES-DE/downloaded_media"
THEMES = "/sdcard/Android/data/org.es_de.frontend/files/themes"
SETTINGS = "/sdcard/ES-DE/settings/es_settings.xml"
APK = "/data/app/org.es_de.frontend/base.apk"


def put(world, path, body=b"x"):
    f = world.dev.root / path.lstrip("/")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(body)
    return f


def use_theme(world, name="colorful"):
    put(world, SETTINGS, f'<?xml version="1.0"?>\n<string name="Theme" value="{name}" />\n'.encode())


def ship_app(world, images: dict, version="3.5.0"):
    """An ES-DE on the fake handheld whose app file carries these system images."""
    f = world.dev.root / APK.lstrip("/")
    f.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(f, "w") as z:
        for name, body in images.items():
            z.writestr(f"assets/linear-es-de/system/systemart/{name}", body)
        z.writestr("assets/systems/android/es_systems.xml", "<systemList/>")
    world.dev.fake_packages["org.es_de.frontend"] = PackageInfo("org.es_de.frontend", version, False, False, True)
    world.dev.fake_apks["org.es_de.frontend"] = APK


@pytest.fixture
def art(world, tmp_path):
    a = Art(tmp_path / "cache" / "art")
    a.esde_cache = tmp_path / "cache"
    return a


def fetch(art, world, folders=("ps2", "psx", "gamecube")):
    return art.refresh(world.dev, world.profile, folders, art.esde_cache)


def pulls(world):
    return [path for what, path in world.dev.calls if what == "pull"]


# ---------------------------------------------------------------- game covers
def test_covers_come_over_named_after_the_game_and_filed_under_jewelcases_system(world, art):
    put(world, f"{MEDIA}/gc/covers/Metroid Prime (USA).jpg", b"prime")
    assert art.cover("ps2", "God of War II (USA)") is None
    assert fetch(art, world) == 2
    assert art.cover("ps2", "God of War II (USA)").read_bytes() == b"cover"
    assert art.cover("gamecube", "Metroid Prime (USA)").read_bytes() == b"prime"       # ES-DE calls it "gc"
    assert art.cover("ps2", "Champions (USA)") is None


def test_only_covers_are_fetched_not_videos_or_manuals(world, art):
    put(world, f"{MEDIA}/ps2/videos/God of War II (USA).mp4", b"v" * 1000)
    put(world, f"{MEDIA}/ps2/manuals/God of War II (USA).pdf", b"m" * 1000)
    put(world, f"{MEDIA}/ps2/covers/notes.txt", b"not a picture")
    fetch(art, world)
    assert pulls(world) == [f"{MEDIA}/ps2/covers/God of War II (USA).png"]


def test_a_cover_is_fetched_again_only_when_it_changed(world, art):
    fetch(art, world)
    world.dev.calls.clear()
    assert fetch(art, world) == 0 and pulls(world) == []
    put(world, f"{MEDIA}/ps2/covers/God of War II (USA).png", b"a better scan")
    assert fetch(art, world) == 1
    assert art.cover("ps2", "God of War II (USA)").read_bytes() == b"a better scan"


def test_a_cover_deleted_on_the_handheld_goes_away_here_too(world, art):
    put(world, f"{MEDIA}/psx/covers/FF7 (USA).png", b"ff7")
    fetch(art, world)
    (world.dev.root / f"{MEDIA}/ps2/covers/God of War II (USA).png".lstrip("/")).unlink()
    fetch(art, world)                               # the first listing without it could have been cut short
    assert fetch(art, world) == 1 and art.cover("ps2", "God of War II (USA)") is None


def test_covers_are_kept_when_the_handheld_cannot_be_read(world, art):
    fetch(art, world)
    world.dev.online = False
    assert fetch(art, world) == 0
    assert art.cover("ps2", "God of War II (USA)").read_bytes() == b"cover"


def test_a_cover_that_fails_half_way_does_not_replace_the_one_we_have(world, art):
    fetch(art, world)
    put(world, f"{MEDIA}/ps2/covers/God of War II (USA).png", b"newer")
    world.dev.drop_on_pull = 1
    fetch(art, world)
    assert art.cover("ps2", "God of War II (USA)").read_bytes() == b"cover"
    assert not list(art.root.rglob("*.part"))


def test_a_handheld_without_es_de_has_no_art_and_that_is_fine(world, art):
    from jewelcase.core.catalog import Profile
    assert art.refresh(world.dev, Profile(world.profile.rom_root, None, world.profile.card_root), ["ps2"]) == 0


def test_covers_are_found_again_by_a_fresh_start(world, art):
    fetch(art, world)
    again = Art(art.root)
    assert again.cover("ps2", "God of War II (USA)").read_bytes() == b"cover"


# ---------------------------------------------------------------- system images
def test_system_images_come_from_the_theme_es_de_is_using(world, art):
    use_theme(world)
    put(world, f"{THEMES}/colorful/_inc/systems/images/ps2.webp", b"ps2 art")
    put(world, f"{THEMES}/colorful/_inc/systems/images/gc.webp", b"cube art")
    put(world, f"{THEMES}/other/_inc/systems/images/psx.webp", b"not the theme in use")
    fetch(art, world)
    assert art.system("ps2").read_bytes() == b"ps2 art" and art.system("gamecube").read_bytes() == b"cube art"
    assert art.system("psx") is None


def test_a_theme_kept_in_es_des_own_folder_is_found_too(world, art):
    use_theme(world, "linear-like")
    put(world, "/sdcard/ES-DE/themes/linear-like/system/systemart/ps2.webp", b"ps2 art")
    fetch(art, world)
    assert art.system("ps2").read_bytes() == b"ps2 art"


def test_without_theme_images_the_ones_inside_es_de_are_used(world, art):
    ship_app(world, {"ps2.webp": b"bundled ps2", "psx.webp": b"bundled psx", "snes.webp": b"not asked for"})
    fetch(art, world)
    assert art.system("ps2").read_bytes() == b"bundled ps2" and art.system("psx").read_bytes() == b"bundled psx"
    assert art.system("gamecube") is None
    world.dev.calls.clear()
    fetch(art, world)
    assert pulls(world) == []                       # ES-DE's app file is copied over once per version


def test_a_system_the_theme_has_no_image_for_gets_es_des_own(world, art):
    use_theme(world)
    put(world, f"{THEMES}/colorful/_inc/systems/images/ps2.webp", b"theme ps2")
    ship_app(world, {"ps2.webp": b"bundled ps2", "psx.webp": b"bundled psx"})
    fetch(art, world)
    assert art.system("ps2").read_bytes() == b"theme ps2" and art.system("psx").read_bytes() == b"bundled psx"


def test_es_des_app_file_is_not_copied_when_the_theme_covers_every_system(world, art):
    use_theme(world)
    for name in ("ps2", "psx", "gc"):
        put(world, f"{THEMES}/colorful/_inc/systems/images/{name}.webp", b"art")
    ship_app(world, {"ps2.webp": b"bundled"})
    fetch(art, world)
    assert APK not in pulls(world)


def test_changing_theme_changes_the_system_images(world, art):
    use_theme(world)
    put(world, f"{THEMES}/colorful/_inc/systems/images/ps2.webp", b"colorful")
    put(world, f"{THEMES}/mono/_inc/systems/images/ps2.webp", b"mono")
    fetch(art, world)
    use_theme(world, "mono")
    fetch(art, world)
    assert art.system("ps2").read_bytes() == b"mono"


def test_progress_is_told_while_many_covers_arrive(world, art):
    for i in range(30):
        put(world, f"{MEDIA}/ps2/covers/Game {i:02d} (USA).png", b"c")
    seen = []
    art.refresh(world.dev, world.profile, ["ps2"], art.esde_cache, changed=lambda: seen.append(1))
    assert len(seen) >= 2                           # more than once, so the window fills in as they come


def test_the_media_folder_is_beside_es_des_game_lists(world):
    assert artmod.media_dir(world.profile) == MEDIA


# ---------------------------------------------------------------- findings of the branch review
def test_an_older_es_de_names_its_theme_setting_differently(world, art):
    put(world, SETTINGS, b'<?xml version="1.0"?>\n<string name="ThemeSet" value="colorful" />\n')
    put(world, f"{THEMES}/colorful/_inc/systems/images/ps2.webp", b"ps2 art")
    fetch(art, world)
    assert art.system("ps2").read_bytes() == b"ps2 art"


def test_covers_fetched_so_far_are_written_down_while_the_fetch_goes_on(world, art):
    """Quitting during the first long fetch must not throw away what already came over."""
    import json
    for i in range(40):
        put(world, f"{MEDIA}/ps2/covers/Game {i:02d} (USA).png", b"c")
    on_disk = []

    def changed():
        on_disk.append(len(json.loads((art.root / "index.json").read_text())["covers"]))

    art.refresh(world.dev, world.profile, ["ps2"], art.esde_cache, changed=changed)
    assert on_disk and on_disk[0] >= 12 and on_disk == sorted(on_disk)


def test_a_cover_missing_from_one_listing_is_kept_until_it_is_missing_again(world, art):
    put(world, f"{MEDIA}/psx/covers/FF7 (USA).png", b"ff7")
    fetch(art, world)
    gow = world.dev.root / f"{MEDIA}/ps2/covers/God of War II (USA).png".lstrip("/")
    gow.rename(gow.with_suffix(".hidden"))          # one listing comes back without it (cut short, say)
    fetch(art, world)
    assert art.cover("ps2", "God of War II (USA)").read_bytes() == b"cover"
    gow.with_suffix(".hidden").rename(gow)
    fetch(art, world)
    gow.unlink()                                    # now it really is gone, two listings in a row
    fetch(art, world)
    assert art.cover("ps2", "God of War II (USA)") is not None
    fetch(art, world)
    assert art.cover("ps2", "God of War II (USA)") is None


def test_a_cover_whose_name_cannot_be_written_here_does_not_stop_the_others(world, art):
    put(world, f"{MEDIA}/ps2/covers/{'x' * 251}.png", b"too long once .part is added")
    put(world, f"{MEDIA}/psx/covers/FF7 (USA).png", b"ff7")
    fetch(art, world)
    assert art.cover("ps2", "God of War II (USA)").read_bytes() == b"cover"
    assert art.cover("psx", "FF7 (USA)").read_bytes() == b"ff7"


@pytest.mark.parametrize("damaged", ['{"covers": [1]}', '{"systems": {"ps2": 5}}', '{"systems": {"ps2": {"x": 1}}}',
                                     '{"covers": {"ps2/a.png": "nonsense"}}', "[]", "not json"])
def test_a_damaged_index_is_started_over_not_crashed_on(world, tmp_path, damaged):
    root = tmp_path / "cache" / "art"
    root.mkdir(parents=True)
    (root / "index.json").write_text(damaged)
    a = Art(root)
    assert a.cover("ps2", "a") is None and a.system("ps2") is None
    a.refresh(world.dev, world.profile, ["ps2"])
    assert a.cover("ps2", "God of War II (USA)").read_bytes() == b"cover"
