"""Which emulator ES-DE launches for each system, and changing that."""
from jewelcase.core import esde, gamelist

# ---------------------------------------------------------------- game lists
LIST = ('<?xml version="1.0"?>\n<gameList>\n'
        '\t<game>\n\t\t<path>./A.chd</path>\n\t\t<playcount>4</playcount>\n'
        '\t\t<altemulator>ARMSX2 (Standalone)</altemulator>\n\t</game>\n'
        '\t<game>\n\t\t<path>./B.chd</path>\n\t</game>\n'
        '\t<game>\n\t\t<path>./C.chd</path>\n\t\t<altemulator>ARMSX2 (Standalone)</altemulator>\n\t</game>\n'
        '</gameList>\n')
BLOCK = "<alternativeEmulator>\n\t<label>{}</label>\n</alternativeEmulator>\n"


def test_a_list_without_a_system_emulator():
    assert gamelist.system_emulator(LIST) == "" and gamelist.system_emulator("") == ""


def test_setting_the_system_emulator_leaves_the_games_alone():
    new = gamelist.set_system_emulator(LIST, "ARMSX2 (Standalone)")
    assert new == LIST.replace("<gameList>", BLOCK.format("ARMSX2 (Standalone)") + "<gameList>", 1)
    assert gamelist.system_emulator(new) == "ARMSX2 (Standalone)"
    assert gamelist.parse(new) == gamelist.parse(LIST)


def test_changing_and_clearing_the_system_emulator():
    one = gamelist.set_system_emulator(LIST, "First")
    two = gamelist.set_system_emulator(one, "Second")
    assert gamelist.system_emulator(two) == "Second" and two.count("<alternativeEmulator>") == 1
    assert gamelist.set_system_emulator(two, "") == LIST


def test_a_label_with_markup_characters_survives():
    label = "Snes9x <2010> & Friends"
    new = gamelist.set_system_emulator(LIST, label)
    assert "<label>Snes9x &lt;2010&gt; &amp; Friends</label>" in new
    assert gamelist.system_emulator(new) == label


def test_a_system_without_a_list_gets_one():
    assert gamelist.set_system_emulator("", "aPS3e ISO (Standalone)") == \
        '<?xml version="1.0"?>\n' + BLOCK.format("aPS3e ISO (Standalone)") + "<gameList>\n</gameList>\n"


def test_emulators_chosen_game_by_game_are_counted():
    assert gamelist.game_emulators(LIST) == {"ARMSX2 (Standalone)": 2}
    assert gamelist.game_emulators("") == {}


# ---------------------------------------------------------------- ES-DE's definitions
BUNDLED = """<?xml version="1.0"?>
<!-- what ES-DE ships -->
<systemList>
    <system>
        <name>ps2</name>
        <fullname>Sony PlayStation 2</fullname>
        <path>%ROMPATH%/ps2</path>
        <extension>.chd .CHD .iso .ISO</extension>
        <command label="AetherSX2 (Standalone)">%EMULATOR_NETHERSX2% %EXTRA_bootPath%=%ROMSAF%</command>
        <command label="ARMSX2 (Standalone)">%EMULATOR_ARMSX2% %DATA%=%ROMSAF%</command>
        <command label="LRPS2">%EMULATOR_RETROARCH% %EXTRA_LIBRETRO%=pcsx2_libretro_android.so %EXTRA_ROM%=%ROM%</command>
        <platform>ps2</platform>
        <theme>ps2</theme>
    </system>
    <system>
        <name>ps3</name>
        <fullname>Sony PlayStation 3</fullname>
        <path>%ROMPATH%/ps3</path>
        <extension>.iso .ISO .ps3 .PS3</extension>
        <command label="aPS3e Game Serial (Standalone)">%EMULATOR_APS3E% %EXTRA_game_dir%=%INJECT%=%BASENAME%.ps3</command>
        <command label="aPS3e ISO (Standalone)">%EMULATOR_APS3E% %EXTRA_iso_uri%=%ROMSAF%</command>
        <command label="ARMSX3 Directory or ISO (Standalone)">%EMULATOR_ARMSX3% %EXTRA_path%=%ROM%</command>
        <platform>ps3</platform>
        <theme>ps3</theme>
    </system>
    <system>
        <name>gc</name>
        <fullname>Nintendo GameCube</fullname>
        <path>%ROMPATH%/gc</path>
        <extension>.rvz .RVZ .iso .ISO</extension>
        <command label="Dolphin (Standalone)">%EMULATOR_DOLPHIN% %EXTRA_AutoStartFile%=%ROM%</command>
        <platform>gc</platform>
        <theme>gc</theme>
    </system>
    <system>
        <name>n3ds</name>
        <fullname>Nintendo 3DS</fullname>
        <path>%ROMPATH%/n3ds</path>
        <extension>.3ds .3DS .cci .CCI</extension>
        <command label="Azahar (Standalone)">%EMULATOR_AZAHAR% %ACTION%=android.intent.action.VIEW %DATA%=%ROMSAF%</command>
        <platform>n3ds</platform>
        <theme>n3ds</theme>
    </system>
</systemList>
"""
CUSTOM = """<?xml version="1.0"?>
<!-- this handheld: games are on the card -->
<systemList>
    <system>
        <name>ps2</name>
        <fullname>Sony PlayStation 2</fullname>
        <path>/storage/CARD-0001/ps2</path>
        <extension>.chd .CHD .iso .ISO</extension>
        <command label="AetherSX2 (Standalone)">%EMULATOR_NETHERSX2% %EXTRA_bootPath%=%ROMSAF%</command>
        <command label="ARMSX2 (Standalone)">%EMULATOR_ARMSX2% %DATA%=%ROMSAF%</command>
        <platform>ps2</platform>
        <theme>ps2</theme>
    </system>
    <system>
        <name>ps3</name>
        <fullname>Sony PlayStation 3</fullname>
        <path>/storage/CARD-0001/ps3</path>
        <extension>.iso .ISO .ps3 .PS3</extension>
        <command label="ARMSX3 Directory or ISO (Standalone)">%EMULATOR_ARMSX3% %EXTRA_path%=%ROM%</command>
        <command label="aPS3e Game Serial (Standalone)">%EMULATOR_APS3E% %EXTRA_game_dir%=%INJECT%=%BASENAME%.ps3</command>
        <command label="aPS3e ISO (Standalone)">%EMULATOR_APS3E% %EXTRA_iso_uri%=%ROMSAF%</command>
        <platform>ps3</platform>
        <theme>ps3</theme>
    </system>
</systemList>
"""
RULES = """<?xml version="1.0"?>
<ruleList>
    <emulator name="RETROARCH">
        <rule type="androidpackage">
            <!-- 64-bit -->
            <entry>com.retroarch.aarch64/com.retroarch.browser.retroactivity.RetroActivityFuture</entry>
            <entry>com.retroarch/com.retroarch.browser.retroactivity.RetroActivityFuture</entry>
        </rule>
    </emulator>
    <emulator name="APS3E">
        <rule type="androidpackage">
            <entry>aenu.aps3e.premium/aenu.aps3e.EmulatorActivity</entry>
            <entry>aenu.aps3e/aenu.aps3e.EmulatorActivity</entry>
        </rule>
    </emulator>
    <emulator name="ARMSX3"><rule type="androidpackage"><entry>com.armsx3/com.armsx2.Main</entry></rule></emulator>
    <emulator name="ARMSX2"><rule type="androidpackage"><entry>com.armsx2/.MainActivity</entry></rule></emulator>
    <emulator name="NETHERSX2">
        <rule type="androidpackage"><entry>xyz.aethersx2.android/.EmulationActivity</entry></rule>
    </emulator>
    <emulator name="AZAHAR">
        <rule type="androidpackage"><entry>io.github.lime3ds.android/org.citra.citra_emu.activities.EmulationActivity</entry></rule>
    </emulator>
</ruleList>
"""
DEFS = esde.Definitions.parse(BUNDLED, RULES, CUSTOM)
HAVE = {"com.armsx2", "xyz.aethersx2.android", "aenu.aps3e", "com.retroarch.aarch64"}


def test_the_handhelds_own_systems_replace_the_ones_es_de_ships():
    assert sorted(DEFS.systems) == ["gc", "n3ds", "ps2", "ps3"]
    ps3 = DEFS.systems["ps3"]
    assert (ps3.fullname, ps3.path, ps3.custom) == ("Sony PlayStation 3", "/storage/CARD-0001/ps3", True)
    assert [c.label for c in ps3.commands][0] == "ARMSX3 Directory or ISO (Standalone)"     # the custom order
    assert [(c.label, c.emulator) for c in DEFS.systems["ps2"].commands] == \
        [("AetherSX2 (Standalone)", "NETHERSX2"), ("ARMSX2 (Standalone)", "ARMSX2")]
    n3ds = DEFS.systems["n3ds"]
    assert (n3ds.path, n3ds.custom) == ("%ROMPATH%/n3ds", False)


def test_an_emulator_name_leads_to_its_packages():
    assert DEFS.packages("APS3E") == ("aenu.aps3e.premium", "aenu.aps3e")
    assert DEFS.packages("RETROARCH") == ("com.retroarch.aarch64", "com.retroarch")
    assert DEFS.packages("NOPE") == ()


def test_the_first_command_is_what_es_de_launches():
    launch = esde.launch(DEFS, "ps2", LIST.replace("ARMSX2 (Standalone)", "nothing"), HAVE)
    assert (launch.current, launch.default, launch.working) == ("AetherSX2 (Standalone)",) * 2 + (True,)
    assert [(c.label, c.package) for c in launch.choices] == \
        [("AetherSX2 (Standalone)", "xyz.aethersx2.android"), ("ARMSX2 (Standalone)", "com.armsx2")]


def test_a_system_emulator_in_the_game_list_wins_over_the_first_command():
    launch = esde.launch(DEFS, "ps2", gamelist.set_system_emulator(LIST, "ARMSX2 (Standalone)"), HAVE)
    assert (launch.current, launch.default, launch.working) == ("ARMSX2 (Standalone)", "AetherSX2 (Standalone)", True)


def test_a_system_emulator_es_de_does_not_know_is_ignored_as_es_de_does():
    launch = esde.launch(DEFS, "ps2", gamelist.set_system_emulator(LIST, "Gone (Standalone)"), HAVE)
    assert launch.current == "AetherSX2 (Standalone)"


def test_games_with_their_own_emulator_are_reported():
    assert esde.launch(DEFS, "ps2", LIST, HAVE).per_game == {"ARMSX2 (Standalone)": 2}


def test_a_default_that_is_not_installed_does_not_work():
    launch = esde.launch(DEFS, "ps3", "", HAVE)
    assert (launch.current, launch.working) == ("ARMSX3 Directory or ISO (Standalone)", False)
    assert [c.label for c in launch.choices] == ["aPS3e Game Serial (Standalone)", "aPS3e ISO (Standalone)"]


def test_nothing_installed_for_a_system():
    launch = esde.launch(DEFS, "n3ds", "", HAVE)
    assert (launch.working, launch.choices) == (False, ())
    assert esde.launch(DEFS, "vectrex", "", HAVE) is None                     # ES-DE has no such system


# ---------------------------------------------------------------- what to switch to
def test_a_working_system_needs_no_change():
    assert esde.suggest(esde.launch(DEFS, "ps2", "", HAVE)) is None


def test_a_broken_system_gets_the_first_installed_emulator():
    assert esde.suggest(esde.launch(DEFS, "ps3", "", HAVE)) == "aPS3e Game Serial (Standalone)"


def test_the_catalogue_can_say_which_command_suits_what_jewelcase_syncs():
    prefer = {"aenu.aps3e": "aPS3e ISO (Standalone)"}
    assert esde.suggest(esde.launch(DEFS, "ps3", "", HAVE), prefer) == "aPS3e ISO (Standalone)"
    assert esde.suggest(esde.launch(DEFS, "ps3", "", HAVE), {"aenu.aps3e": "No Such Command"}) == \
        "aPS3e Game Serial (Standalone)"


def test_nothing_to_suggest_when_nothing_is_installed():
    assert esde.suggest(esde.launch(DEFS, "n3ds", "", HAVE)) is None


def test_only_the_emulator_just_installed_is_suggested():
    launch = esde.launch(DEFS, "ps3", "", HAVE)
    assert esde.suggest(launch, only="aenu.aps3e") == "aPS3e Game Serial (Standalone)"
    assert esde.suggest(launch, only="com.armsx2") is None


# ---------------------------------------------------------------- on the device
import zipfile                                                      # noqa: E402

import pytest                                                       # noqa: E402

from jewelcase.core.catalog import Profile                             # noqa: E402
from jewelcase.core.device import DeviceError, PackageInfo             # noqa: E402
from jewelcase.core.fakedevice import FakeDevice                       # noqa: E402

APK = "/data/app/esde/base.apk"
LISTS = "/sdcard/ES-DE/gamelists"
PS2_LIST = LISTS + "/ps2/gamelist.xml"
PS3_LIST = LISTS + "/ps3/gamelist.xml"
GAMES = {"com.armsx2", "xyz.aethersx2.android", "aenu.aps3e", "com.retroarch.aarch64"}


class World:
    def __init__(self, tmp_path):
        self.dev = dev = FakeDevice(tmp_path / "dev")
        self.cache, self.backups = tmp_path / "cache", tmp_path / "backups"
        self.profile = Profile("/storage/CARD-0001", LISTS, "/storage/CARD-0001")
        self.ship(BUNDLED, "3.5.0-65")
        for p in HAVE:
            dev.fake_packages[p] = PackageInfo(p, "1.0", False, False, True)
        self.put(esde.CUSTOM_SYSTEMS, CUSTOM)
        self.put(PS2_LIST, LIST)
        self.defs = esde.load(dev, self.cache)

    def ship(self, systems: str, version: str) -> None:
        apk = self.dev._p(APK)
        apk.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(apk, "w") as z:
            z.writestr("assets/systems/android/es_systems.xml", systems)
            z.writestr("assets/systems/android/es_find_rules.xml", RULES)
        self.dev.fake_apks[esde.PACKAGE] = APK
        self.dev.fake_packages[esde.PACKAGE] = PackageInfo(esde.PACKAGE, version, False, False, True)

    def put(self, path: str, text: str) -> None:
        p = self.dev._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def read(self, path: str) -> str:
        return self.dev._p(path).read_text(encoding="utf-8")

    def states(self, folders=("ps2", "ps3", "n3ds")):
        return {s.system: s for s in esde.overview(self.dev, self.profile, self.defs, folders)}

    def use(self, system: str, label: str) -> str:
        return esde.use(self.dev, self.profile, self.defs, system, label, self.backups, GAMES)


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_definitions_come_from_the_es_de_on_the_device(w):
    assert w.defs.systems["ps3"].custom and not w.defs.systems["n3ds"].custom
    assert w.defs.packages("APS3E") == ("aenu.aps3e.premium", "aenu.aps3e")
    assert not list(w.cache.rglob("*.apk*"))                        # only the two definition files are kept


def test_definitions_are_read_from_the_app_once_per_version(w):
    w.dev.calls.clear()
    assert esde.load(w.dev, w.cache).systems.keys() == w.defs.systems.keys()
    assert not [c for c in w.dev.calls if c[0] == "pull"]
    w.ship(BUNDLED.replace("<name>n3ds</name>", "<name>n3ds</name><!-- newer -->").replace("Nintendo 3DS", "3DS"),
           "3.6.0-70")
    assert esde.load(w.dev, w.cache).systems["n3ds"].fullname == "3DS"


def test_the_handhelds_own_file_is_read_fresh_every_time(w):
    w.put(esde.CUSTOM_SYSTEMS, CUSTOM.replace("/storage/CARD-0001/ps3", "/storage/CARD-0001/elsewhere"))
    assert esde.load(w.dev, w.cache).systems["ps3"].path == "/storage/CARD-0001/elsewhere"


def test_no_es_de_no_definitions(w):
    del w.dev.fake_packages[esde.PACKAGE]
    assert esde.load(w.dev, w.cache) is None


def test_overview_of_the_systems_jewelcase_knows(w):
    s = w.states()
    assert [(k, v.name, v.launch.current, v.launch.working) for k, v in s.items()] == [
        ("ps2", "PlayStation 2", "AetherSX2 (Standalone)", True),
        ("ps3", "PlayStation 3", "ARMSX3 Directory or ISO (Standalone)", False),
        ("n3ds", "Nintendo 3DS", "Azahar (Standalone)", False)]
    assert s["ps2"].launch.per_game == {"ARMSX2 (Standalone)": 2}


def test_a_system_es_de_looks_for_elsewhere_is_not_listed(w):
    s = w.states()
    assert (s["ps2"].listed, s["ps3"].listed, s["n3ds"].listed) == (True, True, False)


def test_without_a_file_of_its_own_es_de_is_taken_to_look_where_the_games_are(w):
    w.dev._p(esde.CUSTOM_SYSTEMS).unlink()
    w.defs = esde.load(w.dev, w.cache)
    assert all(s.listed for s in w.states().values())


def test_a_disabled_emulator_is_not_a_choice(w):
    w.dev.set_enabled("aenu.aps3e", False)
    assert w.states()["ps3"].launch.choices == ()


def test_choosing_an_emulator_for_a_system(w):
    w.dev.foreground = esde.PACKAGE
    assert w.use("ps2", "ARMSX2 (Standalone)") == ""                # what it was before: ES-DE's default
    assert gamelist.system_emulator(w.read(PS2_LIST)) == "ARMSX2 (Standalone)"
    assert gamelist.parse(w.read(PS2_LIST)) == gamelist.parse(LIST)
    assert w.states()["ps2"].launch.current == "ARMSX2 (Standalone)"
    kept = list(w.backups.rglob("*gamelist.xml"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == LIST


def test_es_de_is_restarted_after_the_change_and_comes_back_if_it_was_in_front(w):
    w.dev.foreground = esde.PACKAGE
    w.dev.calls.clear()
    w.use("ps2", "ARMSX2 (Standalone)")
    order = [c[0] for c in w.dev.calls if c[0] in ("write", "stop", "start")]
    assert order == ["write", "stop", "start"]
    assert w.dev.foreground == esde.PACKAGE


def test_es_de_in_the_background_is_stopped_and_left_closed(w):
    w.dev.foreground = "com.android.launcher3"
    w.dev.calls.clear()
    w.use("ps2", "ARMSX2 (Standalone)")
    assert [c[0] for c in w.dev.calls if c[0] in ("stop", "start")] == ["stop"]


def test_going_back_to_the_default_removes_the_choice(w):
    assert w.use("ps2", "ARMSX2 (Standalone)") == ""
    assert w.use("ps2", "AetherSX2 (Standalone)") == "ARMSX2 (Standalone)"
    assert w.read(PS2_LIST) == LIST


def test_a_system_without_a_game_list_gets_one(w):
    w.use("ps3", "aPS3e ISO (Standalone)")
    assert gamelist.system_emulator(w.read(PS3_LIST)) == "aPS3e ISO (Standalone)"
    assert w.states()["ps3"].launch.working


def test_no_change_while_a_game_is_running(w):
    w.dev.foreground = "com.armsx2"
    w.dev.calls.clear()
    with pytest.raises(esde.EsdeError, match="Close the game on the handheld first"):
        w.use("ps2", "ARMSX2 (Standalone)")
    assert w.read(PS2_LIST) == LIST and not [c for c in w.dev.calls if c[0] in ("write", "stop")]


def test_only_commands_es_de_has_for_the_system_can_be_chosen(w):
    with pytest.raises(esde.EsdeError, match="ES-DE has no"):
        w.use("ps2", "aPS3e ISO (Standalone)")
    with pytest.raises(esde.EsdeError):
        w.use("vectrex", "Anything")


def test_es_de_saving_its_list_at_the_last_moment_does_not_undo_the_choice(w):
    w.dev.foreground = esde.PACKAGE
    w.dev.before_stop = lambda: w.put(PS2_LIST, LIST)               # ES-DE writes what it had in memory
    w.use("ps2", "ARMSX2 (Standalone)")
    assert gamelist.system_emulator(w.read(PS2_LIST)) == "ARMSX2 (Standalone)"


def test_a_game_in_an_emulator_only_es_de_knows_also_blocks_the_change(w):
    w.dev.foreground = "aenu.aps3e.premium"             # in ES-DE's rules, not in Jewelcase's catalogue
    w.dev.calls.clear()
    with pytest.raises(esde.EsdeError, match="Close the game on the handheld first"):
        w.use("ps2", "ARMSX2 (Standalone)")
    assert not [c for c in w.dev.calls if c[0] in ("write", "stop")]


def test_es_de_comes_back_even_when_the_change_fails_half_way(w):
    w.dev.foreground = esde.PACKAGE
    real = w.dev.write_bytes
    writes = []

    def write(path, data):
        writes.append(path)
        if len(writes) == 2:
            raise DeviceError("the card is full")
        real(path, data)
    w.dev.write_bytes = write
    w.dev.before_stop = lambda: w.put(PS2_LIST, LIST)               # forces the second write, which fails
    with pytest.raises(esde.EsdeError):
        w.use("ps2", "ARMSX2 (Standalone)")
    assert w.dev.foreground == esde.PACKAGE


def test_undo_of_a_choice_es_de_was_ignoring_goes_back_to_the_default(w):
    w.put(PS2_LIST, gamelist.set_system_emulator(LIST, "Gone (Standalone)"))
    assert w.use("ps2", "ARMSX2 (Standalone)") == ""                # not "Gone (Standalone)", which cannot be set


def test_a_command_jewelcase_cannot_judge_is_taken_to_work(w):
    custom = CUSTOM.replace(
        '<command label="AetherSX2 (Standalone)">%EMULATOR_NETHERSX2% %EXTRA_bootPath%=%ROMSAF%</command>',
        '<command label="Shell Script">/system/bin/sh %ROM%</command>\n'
        '        <command label="Mystery (Standalone)">%EMULATOR_MYSTERY% %ROM%</command>')
    defs = esde.Definitions.parse(BUNDLED, RULES, custom)
    for chosen in ("", "Mystery (Standalone)"):
        state = esde.launch(defs, "ps2", gamelist.set_system_emulator("", chosen), HAVE)
        assert state.working and esde.suggest(state) is None        # never switched away from


def test_the_handhelds_own_find_rules_are_read_too(w):
    own = ('<?xml version="1.0"?>\n<ruleList>\n    <emulator name="MYSTERY"><rule type="androidpackage">'
           '<entry>org.example.mystery/.Main</entry></rule></emulator>\n</ruleList>\n')
    w.put(esde.CUSTOM_RULES, own)
    defs = esde.load(w.dev, w.cache)
    assert defs.packages("MYSTERY") == ("org.example.mystery",)
    assert defs.packages("APS3E") == ("aenu.aps3e.premium", "aenu.aps3e")       # the shipped rules stay


def test_comments_in_a_systems_file_are_not_systems():
    text = BUNDLED.replace("<systemList>", """<systemList>
    <!--
    <system>
        <name>nes</name>
        <fullname>Commented Out</fullname>
        <path>%ROMPATH%/nes</path>
        <command label="Mesen">%EMULATOR_RETROARCH% %ROM%</command>
    </system>
    -->
    <!-- every <system> below is one console -->""", 1)
    defs = esde.Definitions.parse(text, RULES)
    assert sorted(defs.systems) == ["gc", "n3ds", "ps2", "ps3"]


# ---------------------------------------------------------------- several systems, one restart
def _two_broken(w):
    del w.dev.fake_packages["xyz.aethersx2.android"]            # ps2 loses its emulator; ps3 never had one
    w.dev.foreground = esde.PACKAGE


def _many(w, changes):
    return esde.use_many(w.dev, w.profile, w.defs, changes, w.backups, GAMES)


def test_two_systems_changed_together_restart_es_de_once(w):
    _two_broken(w)
    w.dev.calls.clear()
    before = _many(w, {"ps2": "ARMSX2 (Standalone)", "ps3": "aPS3e ISO (Standalone)"})
    assert before == {"ps2": "", "ps3": ""}
    assert gamelist.system_emulator(w.read(PS2_LIST)) == "ARMSX2 (Standalone)"
    assert gamelist.system_emulator(w.read(PS3_LIST)) == "aPS3e ISO (Standalone)"
    assert [c for c in w.dev.calls if c[0] in ("stop", "start")] == \
        [("stop", esde.PACKAGE), ("start", esde.PACKAGE + "/.MainActivity")]
    assert w.dev.foreground == esde.PACKAGE


def test_the_result_says_what_each_system_had_before(w):
    w.use("ps2", "ARMSX2 (Standalone)")
    assert _many(w, {"ps2": "AetherSX2 (Standalone)", "ps3": "aPS3e ISO (Standalone)"}) == \
        {"ps2": "ARMSX2 (Standalone)", "ps3": ""}


def test_one_invalid_change_among_several_writes_nothing(w):
    _two_broken(w)
    w.dev.calls.clear()
    with pytest.raises(esde.EsdeError, match="ES-DE has no"):
        _many(w, {"ps2": "ARMSX2 (Standalone)", "ps3": "Nope"})
    assert w.read(PS2_LIST) == LIST and not w.dev._p(PS3_LIST).exists()
    assert not [c for c in w.dev.calls if c[0] in ("write", "stop")]


def test_a_game_running_blocks_all_of_the_changes(w):
    w.dev.foreground = "com.armsx2"
    w.dev.calls.clear()
    with pytest.raises(esde.EsdeError, match="Close the game on the handheld first"):
        _many(w, {"ps2": "ARMSX2 (Standalone)", "ps3": "aPS3e ISO (Standalone)"})
    assert w.read(PS2_LIST) == LIST and not [c for c in w.dev.calls if c[0] in ("write", "stop")]


def test_es_de_saving_a_list_at_the_last_moment_does_not_undo_one_of_several(w):
    _two_broken(w)
    w.dev.before_stop = lambda: w.put(PS3_LIST, "<gameList>\n</gameList>\n")
    _many(w, {"ps2": "ARMSX2 (Standalone)", "ps3": "aPS3e ISO (Standalone)"})
    assert gamelist.system_emulator(w.read(PS3_LIST)) == "aPS3e ISO (Standalone)"
    assert gamelist.system_emulator(w.read(PS2_LIST)) == "ARMSX2 (Standalone)"


def test_es_de_in_the_background_stays_closed_after_several_changes(w):
    w.dev.foreground = "com.android.launcher3"
    w.dev.calls.clear()
    _many(w, {"ps2": "ARMSX2 (Standalone)", "ps3": "aPS3e ISO (Standalone)"})
    assert [c[0] for c in w.dev.calls if c[0] in ("stop", "start")] == ["stop"]


def test_a_failure_between_two_lists_says_what_was_changed_and_what_was_not(w):
    _two_broken(w)
    real = w.dev.write_bytes

    def write(path, data):
        if path == PS3_LIST:
            raise DeviceError("the handheld went away")
        real(path, data)
    w.dev.write_bytes = write
    with pytest.raises(esde.EsdeError) as e:
        _many(w, {"ps2": "ARMSX2 (Standalone)", "ps3": "aPS3e ISO (Standalone)"})
    text = str(e.value)
    assert "PlayStation 2" in text and "PlayStation 3" in text and "went away" in text
    assert text.index("PlayStation 2") < text.index("not") < text.rindex("PlayStation 3")
    assert e.value.previous == {"ps2": ""}
    assert gamelist.system_emulator(w.read(PS2_LIST)) == "ARMSX2 (Standalone)"


def test_a_failure_on_the_first_list_says_nothing_was_changed(w):
    w.dev.write_bytes = lambda path, data: (_ for _ in ()).throw(DeviceError("the card is full"))
    with pytest.raises(esde.EsdeError, match="Nothing was changed") as e:
        _many(w, {"ps2": "ARMSX2 (Standalone)"})
    assert e.value.previous == {}


# ---------------------------------------------------------------- an emulator that was not given the folder
ROOT = "/storage/CARD-0001"


def _ps3_state(w, label="aPS3e ISO (Standalone)"):
    return esde.launch(w.defs, "ps3", gamelist.set_system_emulator("", label), HAVE)


def test_a_command_remembers_whether_it_hands_the_game_over_as_a_document_link(w):
    saf = {c.label: c.saf for c in w.defs.systems["ps3"].commands}
    assert saf == {"ARMSX3 Directory or ISO (Standalone)": False, "aPS3e Game Serial (Standalone)": False,
                   "aPS3e ISO (Standalone)": True}


def test_an_emulator_with_no_grant_for_the_folder_is_told_so(w):
    state = _ps3_state(w)
    assert (state.saf, state.package) == (True, "aenu.aps3e")
    note = esde.grant_note(state, "PlayStation 3", ROOT + "/ps3", {"com.armsx2": (ROOT + "/ps2",)}, "aPS3e")
    assert note == ("aPS3e has not been given the PlayStation 3 folder yet. "
                    "Open aPS3e on the handheld and choose that folder in the app.")


def test_nothing_is_claimed_when_the_grants_are_not_known(w):
    assert esde.grant_note(_ps3_state(w), "PlayStation 3", ROOT + "/ps3", {}, "aPS3e") == ""


def test_a_grant_for_the_folder_or_a_parent_of_it_silences_the_note(w):
    for held in (ROOT + "/ps3", ROOT, ROOT + "/ps3/"):
        assert esde.grant_note(_ps3_state(w), "PlayStation 3", ROOT + "/ps3", {"aenu.aps3e": (held,)}, "x") == ""
    other = esde.grant_note(_ps3_state(w), "PlayStation 3", ROOT + "/ps3", {"aenu.aps3e": (ROOT + "/ps3x",)}, "x")
    assert other != ""                                              # a folder that only starts the same way


def test_the_internal_storage_has_two_names_for_the_same_place(w):
    held = {"aenu.aps3e": ("/storage/emulated/0/ROMs",)}
    assert esde.grant_note(_ps3_state(w), "PlayStation 3", "/sdcard/ROMs/ps3", held, "x") == ""
    held = {"aenu.aps3e": ("/sdcard/ROMs",)}
    assert esde.grant_note(_ps3_state(w), "PlayStation 3", "/storage/emulated/0/ROMs/ps3", held, "x") == ""


def test_a_command_that_does_not_pass_a_document_link_never_warns(w):
    state = _ps3_state(w, "ARMSX3 Directory or ISO (Standalone)")
    assert not state.saf
    assert esde.grant_note(state, "PlayStation 3", ROOT + "/ps3", {"com.armsx2": ()}, "ARMSX3") == ""


def test_an_emulator_that_is_not_installed_is_not_told_off_about_a_folder(w):
    state = esde.launch(w.defs, "ps3", gamelist.set_system_emulator("", "aPS3e ISO (Standalone)"), set())
    assert esde.grant_note(state, "PlayStation 3", ROOT + "/ps3", {"com.armsx2": ()}, "aPS3e") == ""


# ---------------------------------------------------------------- systems ES-DE does not look for
def _wanted(w):
    return esde.ensure_listed(w.dev, w.profile, w.defs, ["n3ds", "ps2", "gamecube", "vectrex"], w.backups)


def test_a_system_es_de_looks_for_elsewhere_is_added_to_the_handhelds_file(w):
    import xml.etree.ElementTree as ET
    assert _wanted(w) == ["Nintendo 3DS", "GameCube"]
    text = w.read(esde.CUSTOM_SYSTEMS)
    assert text.startswith(CUSTOM[: CUSTOM.index("</systemList>")])           # what was there is untouched
    assert text.rstrip().endswith("</systemList>") and ET.fromstring(text) is not None
    defs = esde.load(w.dev, w.cache)
    assert (defs.systems["n3ds"].path, defs.systems["n3ds"].custom) == ("/storage/CARD-0001/n3ds", True)
    assert defs.systems["gc"].path == "/storage/CARD-0001/gamecube"           # Jewelcase's folder, ES-DE's name
    assert [c.label for c in defs.systems["n3ds"].commands] == ["Azahar (Standalone)"]
    kept = list(w.backups.rglob("*es_systems.xml"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == CUSTOM
    w.defs = defs
    assert _wanted(w) == [] and w.read(esde.CUSTOM_SYSTEMS) == text


def test_nothing_is_added_when_es_de_looks_where_the_games_are(w):
    w.dev._p(esde.CUSTOM_SYSTEMS).unlink()
    w.defs = esde.load(w.dev, w.cache)
    assert _wanted(w) == [] and not w.dev._p(esde.CUSTOM_SYSTEMS).exists()


def test_a_systems_file_written_on_one_line_stays_well_formed(w):
    import xml.etree.ElementTree as ET
    one_line = "<systemList>" + CUSTOM.split("<systemList>", 1)[1].replace("\n", "").replace("    ", "")
    w.put(esde.CUSTOM_SYSTEMS, one_line)
    w.defs = esde.load(w.dev, w.cache)
    assert esde.ensure_listed(w.dev, w.profile, w.defs, ["n3ds"], w.backups) == ["Nintendo 3DS"]
    names = [s.findtext("name") for s in ET.fromstring(w.read(esde.CUSTOM_SYSTEMS)).findall("system")]
    assert names == ["ps2", "ps3", "n3ds"]


def test_a_system_the_handheld_points_somewhere_else_is_left_alone(w):
    moved = CUSTOM.replace("/storage/CARD-0001/ps3", "/storage/emulated/0/ps3games")
    w.put(esde.CUSTOM_SYSTEMS, moved)
    w.defs = esde.load(w.dev, w.cache)
    assert esde.ensure_listed(w.dev, w.profile, w.defs, ["ps3"], w.backups) == []
    assert w.read(esde.CUSTOM_SYSTEMS) == moved


# ---------------------------------------------------------------- the command, against the demo world
from test_saves_cli import Cli                                      # noqa: E402


@pytest.fixture
def c(capsys, tmp_path):
    return Cli(capsys, tmp_path / "demo")


def _ps2(c):
    return next(s for s in c("--json", "esde").json if s["system"] == "ps2")


def test_command_lists_what_es_de_launches(c):
    ps2 = _ps2(c)
    assert (ps2["name"], ps2["current"], ps2["working"], ps2["fix"]) == \
        ("PlayStation 2", "AetherSX2 (Standalone)", False, "ARMSX2 (Standalone)")
    assert ps2["games"] == 6 and ps2["choices"] == ["ARMSX2 (Standalone)"]
    psx = next(s for s in c.json if s["system"] == "psx")
    assert (psx["working"], psx["fix"]) == (True, "")
    text = c("esde").out
    assert "PlayStation 2" in text and "AetherSX2 (Standalone) is not installed" in text
    assert 'jewelcase esde use ps2 "ARMSX2 (Standalone)"' in text


def test_command_only_lists_systems_that_matter(c):
    listed = {s["system"] for s in c("--json", "esde").json}
    assert {"ps2", "psx", "gamecube", "psp", "gba", "snes", "dreamcast", "n64"} <= listed
    assert "ps3" not in listed                  # no games, nothing chosen, no emulator for it installed


def test_command_use(c):
    assert c("esde", "use", "ps2", "ARMSX2 (Standalone)").code == 0
    assert "PlayStation 2 games now open with ARMSX2 (Standalone)" in c.out
    ps2 = _ps2(c)
    assert (ps2["current"], ps2["working"], ps2["fix"]) == ("ARMSX2 (Standalone)", True, "")


def test_command_use_takes_es_des_name_for_a_system_too(c):
    assert c("esde", "use", "gc", "Dolphin").code == 0
    assert next(s for s in c("--json", "esde").json if s["system"] == "gamecube")["current"] == "Dolphin"


def test_command_use_refuses_what_es_de_does_not_have(c):
    assert c("esde", "use", "ps2", "Nope (Standalone)").code == 2
    assert 'ES-DE has no "Nope (Standalone)" for PlayStation 2.' in c.out
    assert _ps2(c)["current"] == "AetherSX2 (Standalone)"


def test_command_fix(c):
    assert c("esde", "fix").code == 0
    assert "PlayStation 2 games now open with ARMSX2 (Standalone)" in c.out
    assert _ps2(c)["working"]
    assert c("esde", "fix").code == 0 and "Nothing to fix" in c.out


def test_an_emulator_installed_for_a_system_without_games_is_offered(c):
    from jewelcase.core.device import PackageInfo as Info
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=c.demo)
    s.connect()
    s.refresh()
    s.device.fake_packages["aenu.aps3e"] = Info("aenu.aps3e", "2.43", False, False, True)
    ps3 = next(x for x in esde.for_session(s) if x.system == "ps3")
    assert (ps3.launch.working, ps3.fix, ps3.games) == (False, "aPS3e ISO (Standalone)", 0)
    done = esde.apply_fixes(s, only="aenu.aps3e")
    assert [(x.system, label, before) for x, label, before in done] == [("ps3", "aPS3e ISO (Standalone)", "")]
    assert next(x for x in esde.for_session(s) if x.system == "ps3").launch.working


def test_an_emulator_just_installed_does_not_take_systems_it_is_not_for(c):
    """RetroArch has a core command for almost every system; installing it must not claim them all."""
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=c.demo)
    s.connect()
    s.refresh()
    wii = next(x for x in esde.for_session(s) if x.system == "wii")
    assert (wii.games, wii.launch.working, [ch.label for ch in wii.launch.choices]) == (0, False, ["Dolphin"])
    assert esde.apply_fixes(s, only="com.retroarch.aarch64") == []
    assert next(x for x in esde.for_session(s) if x.system == "wii").launch.current == "Dolphin MMJR2 (Standalone)"


def test_fixing_stops_at_a_system_that_cannot_be_changed_and_says_why(c):
    from jewelcase.core.device import PackageInfo as Info
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=c.demo)
    s.connect()
    s.refresh()
    s.device.fake_packages["aenu.aps3e"] = Info("aenu.aps3e", "2.43", False, False, True)
    s.device.foreground = "com.armsx2"
    problems = []
    assert esde.apply_fixes(s, problems=problems) == []
    assert problems == ["Close the game on the handheld first."]


def test_without_es_de_the_command_says_so(c):
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=c.demo)
    s.connect()
    del s.device.fake_packages[esde.PACKAGE]
    assert esde.for_session(s) is None


# ---------------------------------------------------------------- the grant note, against the demo world
def _demo_session(c):
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=c.demo)
    s.connect()
    s.refresh()
    return s


def _psp(s):
    return next(x for x in esde.for_session(s) if x.system == "psp")


def test_the_demo_has_one_emulator_without_its_folder(c):
    s = _demo_session(c)
    assert _psp(s).grant == ("PPSSPP has not been given the PSP folder yet. "
                             "Open PPSSPP on the handheld and choose that folder in the app.")
    assert [x.system for x in esde.for_session(s) if x.grant] == ["psp"]


def test_a_grant_for_the_games_folder_silences_the_demo_note(c):
    s = _demo_session(c)
    s.device.fake_grants["org.ppsspp.ppsspp"] = ["/storage/1A2B-3C4D"]
    assert _psp(s).grant == ""


def test_no_note_when_the_device_cannot_say_what_was_granted(c):
    s = _demo_session(c)
    s.device.fake_grants = {}
    assert not any(x.grant for x in esde.for_session(s))


def test_a_system_without_games_gets_no_note(c):
    s = _demo_session(c)
    for f in (s.device.root / "storage/1A2B-3C4D/psp").iterdir():
        f.unlink()
    s.refresh()
    assert not _psp(s).grant


def test_the_command_and_the_json_show_the_missing_folder(c):
    psp = next(x for x in c("--json", "esde").json if x["system"] == "psp")
    assert psp["grant"].startswith("PPSSPP has not been given the PSP folder yet.")
    assert next(x for x in c.json if x["system"] == "psx")["grant"] == ""
    assert "PPSSPP has not been given the PSP folder yet." in c("esde").out


def test_fixing_several_systems_restarts_es_de_once(c):
    s = _demo_session(c)
    del s.device.fake_packages["com.retroarch.aarch64"]         # PlayStation now needs DuckStation as well
    s.device.foreground = esde.PACKAGE
    s.device.calls.clear()
    done = esde.apply_fixes(s)
    assert sorted(x.system for x, _, _ in done) == ["n64", "ps2", "psx"]
    assert [call for call in s.device.calls if call[0] == "stop"] == [("stop", esde.PACKAGE)]
    assert s.device.foreground == esde.PACKAGE


def test_fixing_that_breaks_off_part_way_still_returns_what_was_switched(c):
    s = _demo_session(c)
    del s.device.fake_packages["com.retroarch.aarch64"]
    real, writes = s.device.write_bytes, []

    def write(path, data):
        writes.append(path)
        if len(writes) == 2:
            raise DeviceError("the handheld went away")
        real(path, data)
    s.device.write_bytes = write
    problems: list[str] = []
    done = esde.apply_fixes(s, problems=problems)
    assert len(done) == 1 and "went away" in problems[0]
    assert gamelist.system_emulator(s.device.read_bytes(f"{s.profile.gamelists_dir}/{done[0][0].launch.esde}/gamelist.xml").decode()) == done[0][1]
