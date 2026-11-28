#!/usr/bin/env python3
"""Decides whether the code on main gets released, and raises the version when the maintainer asks.

A release is made for the version that pyproject.toml states, once, when that version has never been
released. Raising the version is a change to pyproject.toml, which never merges by itself, so a person
decides on every release.

  release_plan.py plan           what to do now; tells the workflow through GITHUB_OUTPUT
                                 (do=release | nothing | trial | refuse, version=1.2.3)
  release_plan.py bump VERSION   write VERSION into pyproject.toml and src/jewelcase/__init__.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
MAIN = "refs/heads/main"
PYPROJECT = Path("pyproject.toml")
INIT = Path("src/jewelcase/__init__.py")


def key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def project_version(pyproject: str) -> str:
    """The version pyproject.toml states, "" if it states none."""
    try:
        version = tomllib.loads(pyproject).get("project", {}).get("version")
    except tomllib.TOMLDecodeError:
        return ""
    return version if isinstance(version, str) else ""


def init_version(init: str) -> str:
    found = re.search(r'(?m)^__version__ = "([^"\n]*)"$', init)
    return found.group(1) if found else ""


def plan(version, init, released, tag_sha, head_sha, ref, before=None) -> dict:
    """What to do with the commit `head_sha` on `ref`, whose files state `version` and `init`.

    `released` are the tags of the releases published so far; `tag_sha` is the commit the tag for this
    version marks, if that tag exists. `before` is the version main stated before the push that started
    this run ("" if it stated none), or None when the maintainer started the run by hand.
    """
    def refuse(why: str) -> dict:
        return {"do": "refuse", "why": why}

    if not isinstance(version, str) or not VERSION.fullmatch(version):
        return refuse("The version in pyproject.toml is not three plain numbers like 1.2.3.")
    if init != version:
        return refuse(f"pyproject.toml says {version} and src/jewelcase/__init__.py says {init or 'nothing'}.")
    tag = f"v{version}"
    if tag in released:
        return {"do": "nothing", "why": f"{version} is released already."}
    if before is not None and before == version:
        # some other change to pyproject.toml: only raising the version asks for a release
        return {"do": "nothing", "why": f"The version is still {version}; this change did not raise it."}
    if before and VERSION.fullmatch(before) and key(version) < key(before):
        return refuse(f"The version went down, from {before} to {version}.")
    if ref != MAIN:
        return refuse("A release is made from main only.")
    earlier = [r[1:] for r in released if isinstance(r, str) and r.startswith("v") and VERSION.fullmatch(r[1:])]
    if earlier and key(version) < max(key(e) for e in earlier):
        newest = max(earlier, key=key)
        return refuse(f"{version} is lower than {newest}, which is released already.")
    if tag_sha and tag_sha != head_sha:
        return refuse(f"The tag {tag} exists and marks another commit.")
    return {"do": "release", "why": f"{version} has not been released."}


def bumped(pyproject: str, init: str, new: str) -> tuple[str, str]:
    """The two files with the version raised to `new`. Raises ValueError unless `new` is well formed and higher."""
    current = project_version(pyproject)
    if not isinstance(new, str) or not VERSION.fullmatch(new):
        raise ValueError("A version is three plain numbers like 1.2.3.")
    if not VERSION.fullmatch(current) or init_version(init) != current:
        raise ValueError("The files do not state one well-formed version to start from.")
    if key(new) <= key(current):
        raise ValueError(f"{new} is not higher than the current version {current}.")
    pyproject = re.sub(r'(?m)^version = "[^"\n]*"$', f'version = "{new}"', pyproject, count=1)
    init = re.sub(r'(?m)^__version__ = "[^"\n]*"$', f'__version__ = "{new}"', init, count=1)
    if project_version(pyproject) != new or init_version(init) != new:
        raise ValueError("Could not find the version line to change.")
    return pyproject, init


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _tag_commit(repo: str, tag: str) -> str | None:
    """The commit a tag marks, None if there is no such tag.

    Only GitHub's plain "not found" means there is none. Any other failure is passed on, so that a tag
    that could not be read is never taken for a tag that is not there.
    """
    try:
        ref = json.loads(_gh("api", f"repos/{repo}/git/ref/tags/{tag}"))["object"]
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return None
        raise
    if ref.get("type") == "tag":                # a tag with a message points at a tag object first
        ref = json.loads(_gh("api", f"repos/{repo}/git/tags/{ref['sha']}"))["object"]
    return ref.get("sha")


def _version_before(repo: str, sha: str) -> str | None:
    """The version main stated at commit `sha`, "" if none; None when the run was not started by a push."""
    if not sha:
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or set(sha) == {"0"}:
        return ""
    try:
        return project_version(_gh("api", f"repos/{repo}/contents/pyproject.toml?ref={sha}",
                                   "-H", "Accept: application/vnd.github.raw"))
    except subprocess.CalledProcessError:
        return ""


def _plan_command() -> int:
    version = project_version(PYPROJECT.read_text(encoding="utf-8"))
    init = init_version(INIT.read_text(encoding="utf-8")) if INIT.exists() else ""
    releases = json.loads(_gh("release", "list", "--limit", "200", "--json", "tagName,isDraft"))
    released = [r["tagName"] for r in releases if not r.get("isDraft")]
    tag_sha = _tag_commit(os.environ["GH_REPO"], f"v{version}") if VERSION.fullmatch(version) else None
    before = _version_before(os.environ["GH_REPO"], os.environ.get("BEFORE", "").strip())
    decision = plan(version, init, released, tag_sha, os.environ["GITHUB_SHA"], os.environ["GITHUB_REF"], before)
    do = decision["do"]
    if os.environ.get("PUBLISH", "").strip().lower() != "true":
        print(f"A trial: the AppImage is built and nothing is published. A real run would say: {decision['why']}")
        do = "trial"
    else:
        print(decision["why"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"do={do}\nversion={version if VERSION.fullmatch(version) else ''}\n")
    return 1 if do == "refuse" else 0


def _bump_command(new: str) -> int:
    try:
        pyproject, init = bumped(PYPROJECT.read_text(encoding="utf-8"), INIT.read_text(encoding="utf-8"), new)
    except ValueError as error:
        print(error)
        return 1
    PYPROJECT.write_text(pyproject, encoding="utf-8")
    INIT.write_text(init, encoding="utf-8")
    print(f"The version is now {new}.")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["plan"]:
        return _plan_command()
    if len(argv) == 2 and argv[0] == "bump":
        return _bump_command(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
