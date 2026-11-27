#!/usr/bin/env python3
"""The only two things triage may do to an issue: set labels from a fixed list, and post a comment.

Triage reads issues written by anyone, so it gets no general access to GitHub. It may run this script,
which acts on the one issue the workflow was started for (ISSUE_NUMBER) and refuses everything else.

    issue_action.py label --add bug --add claude:ready --remove needs-info
    issue_action.py comment "Thanks. Which emulator opens the game?"
    issue_action.py check FILE          is this text fit to be posted? (used for pull request texts)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# What triage may set. `claude:working` and `hold` are not here: only the workflows and the maintainer set those.
LABELS = ("bug", "feature", "question", "docs", "duplicate", "needs-info", "out-of-scope", "needs-maintainer",
          "claude:ready")
# What triage may take away: its own question to the reporter, once it is answered. Never somebody's hold.
REMOVABLE = ("needs-info",)
MAX_COMMENT = 4000

_LINK = re.compile(r"https?://|www\.", re.I)
_MENTION = re.compile(r"(?<![\w.`/])@[A-Za-z0-9]")
_CREDENTIAL = re.compile(r"sk-ant-|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN ")
_SECRET_NAME = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL", re.I)


def publishable(text: str) -> bool:
    """False for text that carries a link, an @mention, or anything that looks like a credential.

    A triage comment needs none of these, and they are how a run that was talked into misbehaving would
    advertise something or leak what it can see.
    """
    if _LINK.search(text) or _MENTION.search(text) or _CREDENTIAL.search(text):
        return False
    for name, value in os.environ.items():
        if _SECRET_NAME.search(name) and len(value) >= 16 and value in text:
            return False
    return True


def _number(number) -> str:
    if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]{0,8}", number):
        raise ValueError("no issue number: this script only runs inside the triage workflow")
    return number


def label_args(number, add: list[str], remove: list[str]) -> list[str]:
    """The gh command that sets these labels on the issue; ValueError for anything outside the lists."""
    number = _number(number)
    for label in add:
        if label not in LABELS:
            raise ValueError(f"not a label triage may set: {label!r} (allowed: {', '.join(LABELS)})")
    for label in remove:
        if label not in REMOVABLE:
            raise ValueError(f"not a label triage may remove: {label!r} (allowed: {', '.join(REMOVABLE)})")
    if "claude:ready" in add and "needs-maintainer" in add:
        raise ValueError("an issue goes to Claude or to the maintainer, not both")
    if not add and not remove:
        raise ValueError("nothing to add or remove")
    cmd = ["gh", "issue", "edit", number]
    if add:
        cmd += ["--add-label", ",".join(add)]
    if remove:
        cmd += ["--remove-label", ",".join(remove)]
    return cmd


def comment_args(number, body: str) -> list[str]:
    """The gh command that posts this comment on the issue."""
    number = _number(number)
    if not isinstance(body, str) or not body.strip():
        raise ValueError("an empty comment")
    if len(body) > MAX_COMMENT:
        raise ValueError(f"the comment is too long ({len(body)} characters, limit {MAX_COMMENT})")
    if not publishable(body):
        raise ValueError("a comment may not contain a link, an @mention or anything shaped like a credential")
    return ["gh", "issue", "comment", number, "--body", body]


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd, check=False).returncode


def main(argv: list[str], run=_run) -> int:
    ap = argparse.ArgumentParser(prog="issue_action.py")
    sub = ap.add_subparsers(dest="what", required=True)
    lab = sub.add_parser("label")
    lab.add_argument("--add", action="append", default=[])
    lab.add_argument("--remove", action="append", default=[])
    com = sub.add_parser("comment")
    com.add_argument("body")
    chk = sub.add_parser("check")
    chk.add_argument("file")
    args = ap.parse_args(argv)
    if args.what == "check":
        try:
            with open(args.file, encoding="utf-8") as f:
                return 0 if publishable(f.read()) else 1
        except (OSError, ValueError):
            return 1
    number = os.environ.get("ISSUE_NUMBER")
    try:
        cmd = (label_args(number, args.add, args.remove) if args.what == "label"
               else comment_args(number, args.body))
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return run(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
