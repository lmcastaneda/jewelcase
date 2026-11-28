"""Programs the packaged app starts get the user's library path, not the bundle's."""
import sys

import pytest

from jewelcase import cli

SAGA = "ps2/Northwind Saga (USA)"          # the demo's one game that the library has only as an archive


@pytest.fixture
def seven_zip(tmp_path, monkeypatch):
    """A stand-in 7z, alone on PATH, that writes down its library path in libs.txt and lists nothing."""
    folder = tmp_path / "bin"
    folder.mkdir()
    libs = tmp_path / "libs.txt"
    tool = folder / "7z"
    tool.write_text(f'#!/bin/sh\necho "${{LD_LIBRARY_PATH-unset}}" >> "{libs}"\nexit 1\n')
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    return libs


def packaged(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path / "bundle/_internal"))     # as the packaged app has it


def convert(tmp_path, capsys):
    code = cli.main(["--demo-dir", str(tmp_path / "demo"), "convert", SAGA])
    out = capsys.readouterr().out
    assert code == 2 and out.rstrip().endswith("The archive could not be read."), out


def library_paths(libs):
    lines = libs.read_text().splitlines()
    assert lines, "7z was never started"
    return set(lines)


def test_started_from_the_packaged_app_a_program_gets_no_library_path_when_the_user_had_none(
        tmp_path, monkeypatch, capsys, seven_zip):
    packaged(tmp_path, monkeypatch)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    convert(tmp_path, capsys)
    assert library_paths(seven_zip) == {"unset"}


def test_a_library_path_the_user_had_before_starting_the_packaged_app_is_the_one_the_program_gets(
        tmp_path, monkeypatch, capsys, seven_zip):
    packaged(tmp_path, monkeypatch)
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")
    convert(tmp_path, capsys)
    assert library_paths(seven_zip) == {"/host/lib"}


def test_run_from_source_the_program_gets_the_library_path_as_it_is(tmp_path, monkeypatch, capsys, seven_zip):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/user/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    convert(tmp_path, capsys)
    assert library_paths(seven_zip) == {"/user/lib"}
