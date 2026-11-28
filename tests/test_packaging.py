"""The AppImage build script: every tool it downloads is pinned and checked before it runs."""
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging/inside-container.sh"


def script() -> str:
    return SCRIPT.read_text()


def fetch_function() -> str:
    """The script's fetch() function, the one place it downloads anything."""
    found = re.search(r"^fetch\(\) \{\n.*?^\}\n", script(), re.M | re.S)
    assert found, "inside-container.sh defines no fetch() function"
    return found.group(0)


def run_fetch(tmp_path: Path, source: Path, sha256: str) -> subprocess.CompletedProcess:
    """Run fetch alone on a local file, then a command that stands for running the tool."""
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{fetch_function()}fetch "$1" "$2" "$3"\necho ran',
         "fetch", source.as_uri(), sha256, str(tmp_path / "tool")],
        capture_output=True, text=True)


def test_the_build_script_is_valid_shell():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_a_download_with_the_right_checksum_gets_its_real_name(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"a tool")
    result = run_fetch(tmp_path, source, hashlib.sha256(b"a tool").hexdigest())
    assert result.returncode == 0 and result.stdout.strip() == "ran", result.stderr
    assert (tmp_path / "tool").read_bytes() == b"a tool"


def test_a_download_whose_checksum_does_not_match_stops_the_build_and_leaves_no_tool(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"a tool")
    result = run_fetch(tmp_path, source, hashlib.sha256(b"another tool").hexdigest())
    assert result.returncode != 0 and "ran" not in result.stdout
    assert "checksum" in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source"]


def test_every_tool_the_build_downloads_comes_from_an_exact_version_with_its_sha256_in_the_script():
    text = script()
    assert "continuous" not in text and "/latest/" not in text
    assert not re.search(r"\|\s*(ba)?sh\b", text), "a downloaded script is piped into a shell"
    outside = text.replace(fetch_function(), "")
    assert not re.search(r"\b(curl|wget) -", outside), "a download that does not go through fetch"
    calls = re.findall(r"^fetch (\S+) (\S+) (\S+)$", text, re.M)
    assert len(calls) == 2, calls  # uv and appimagetool
    pinned = dict(re.findall(r"^([A-Z0-9_]+)=(\S+)$", text, re.M))
    for url, sha, _dest in calls:
        version = re.search(r"/download/\$\{?([A-Z0-9_]+_VERSION)\}?/", url)
        assert version and re.fullmatch(r"\d+(\.\d+)+", pinned.get(version.group(1), "")), url
        assert re.fullmatch(r"[0-9a-f]{64}", pinned.get(sha.strip('"').strip("${}"), "")), sha
