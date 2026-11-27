from pathlib import Path

from jewelcase.core import config


def test_paths_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    p = config.paths()
    assert p.data == tmp_path / "d" / "jewelcase"
    assert p.config == tmp_path / "c" / "jewelcase" / "config.toml"
    assert p.cache == tmp_path / "k" / "jewelcase"


def test_paths_default_to_home(monkeypatch, tmp_path):
    for v in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = config.paths()
    assert p.data == tmp_path / ".local/share/jewelcase"
    assert p.config == tmp_path / ".config/jewelcase/config.toml"


def test_defaults_when_file_missing(tmp_path):
    cfg = config.load(tmp_path / "nope.toml")
    assert cfg.library is None
    assert cfg.inbox == Path.home() / "Downloads"
    assert cfg.never_played_seconds == 60
    assert cfg.not_played_days == 90
    assert cfg.adb is None


def test_config_roundtrip(tmp_path):
    f = tmp_path / "sub" / "config.toml"
    cfg = config.Config(library=Path('/mnt/My "Lib"/roms'), inbox=tmp_path / "in",
                        adb="/opt/adb", never_played_seconds=120, not_played_days=30)
    config.save(cfg, f)
    assert config.load(f) == cfg


def test_extra_systems_extend_the_table(tmp_path):
    from jewelcase.core import systems
    f = tmp_path / "config.toml"
    f.write_text('[systems.vectrex]\nname = "Vectrex"\nesde = "vectrex"\n'
                 'device_exts = [".vec", ".zip"]\ncolor = "#112233"\n')
    cfg = config.load(f)
    assert cfg.extra_systems["vectrex"]["name"] == "Vectrex"
    try:
        s = systems.get("vectrex")
        assert (s.name, s.device_exts, s.color) == ("Vectrex", (".vec", ".zip"), "#112233")
        config.save(cfg, f)
        assert config.load(f).extra_systems == cfg.extra_systems
    finally:
        systems.unregister("vectrex")
    assert systems.get("vectrex").name == "vectrex"


# ---------------------------------------------------------------- a settings file that cannot be read
def test_a_file_that_cannot_be_parsed_is_kept_as_broken_before_the_first_save(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('library = "/mnt/roms"\n[[lists]\nname = "mine"\n')          # a typo from editing by hand
    cfg = config.load(f)
    assert cfg.unreadable and cfg.library is None
    assert f.read_text().startswith('library = "/mnt/roms"')                # loading changes nothing
    config.save(cfg, f)
    assert (tmp_path / "config.toml.broken").read_text().startswith('library = "/mnt/roms"')
    assert config.load(f).unreadable is False


def test_an_existing_broken_copy_is_never_overwritten(tmp_path):
    f = tmp_path / "config.toml"
    (tmp_path / "config.toml.broken").write_text("older copy")
    f.write_text("not [ toml")
    cfg = config.load(f)
    config.save(cfg, f)
    assert (tmp_path / "config.toml.broken").read_text() == "older copy"
    assert [p.read_text() for p in tmp_path.glob("config.toml.broken*") if p.read_text() != "older copy"] == ["not [ toml"]


def test_only_the_first_save_keeps_a_copy(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text("not [ toml")
    cfg = config.load(f)
    config.save(cfg, f)
    config.save(cfg, f)
    assert len(list(tmp_path.glob("config.toml.broken*"))) == 1


def test_a_missing_file_is_not_a_broken_one(tmp_path):
    cfg = config.load(tmp_path / "none.toml")
    assert not cfg.unreadable
    config.save(cfg, tmp_path / "none.toml")
    assert not list(tmp_path.glob("*.broken*"))


def test_a_value_of_the_wrong_type_falls_back_to_its_default(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('never_played_seconds = "x"\nnot_played_days = [1]\nbackup_settings = "yes"\nlibrary = 5\n'
                 'adb = 7\ndownload_covers = 3\nconvert_on_import = "no"\ncard = []\ninbox = 1\n')
    cfg = config.load(f)
    d = config.Config()
    assert (cfg.never_played_seconds, cfg.not_played_days, cfg.backup_settings) == (60, 90, True)
    assert (cfg.library, cfg.adb, cfg.download_covers, cfg.convert_on_import, cfg.card) == (None, None, False, True, None)
    assert cfg.inbox == d.inbox and not cfg.unreadable
