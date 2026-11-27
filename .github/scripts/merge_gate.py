#!/usr/bin/env python3
"""Decides whether a pull request may merge by itself.

Only a fix that Claude wrote for an accepted issue can merge without a person: a small change to code,
tests or the README, with a test, that a second Claude reviewed and approved. Everything else waits for
the maintainer. The decision is made here, by rules, so that no text in an issue or a pull request can
argue with it. The workflow that runs this file always takes it from the main branch.

    merge_gate.py facts NUMBER > facts.json      what GitHub reports about the pull request
    merge_gate.py decide facts.json              the reviewer's verdict comes in REVIEW
    merge_gate.py decide facts.json --rules-only everything but the review
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# The account the fix workflow opens pull requests as, and the only branch name it uses.
BOT = "github-actions[bot]"
BRANCH = re.compile(r"claude/issue-([1-9][0-9]{0,8})")
BASE = "main"
TAKEN = "claude:working"            # the label the fix workflow puts on the issue it was given

MAX_LINES = 500     # added plus removed
MAX_FILES = 30
HOLD_LABELS = ("needs-maintainer", "hold")


def allowed(path) -> bool:
    """The only files one of Claude's fixes may touch: code, tests and the README.

    Everything else is the maintainer's: the automation, packaging, dependencies, the licence, data files
    (they name download sources), test configuration, and any file that could instruct a later Claude.
    """
    if not isinstance(path, str):
        return False
    if path == "README.md":
        return True
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    name = parts[-1]
    if path.startswith("src/jewelcase/") and name.endswith(".py"):
        return True
    return (parts[0] == "tests" and name.startswith("test_") and name.endswith(".py")
            and path != "tests/test_automation.py")


def issue_number(head_ref) -> int | None:
    """The issue a branch named claude/issue-N belongs to."""
    m = BRANCH.fullmatch(head_ref) if isinstance(head_ref, str) else None
    return int(m.group(1)) if m else None


def _is_test(path: str) -> bool:
    return path.startswith("tests/")


def _is_code(path: str) -> bool:
    return path.startswith("src/")


def _no(automated: bool, reasons: list[str]) -> dict:
    return {"automated": automated, "ok": False, "merge": False, "reasons": reasons}


def decide(pr, review, rules_only: bool = False) -> dict:
    """{"automated": bool, "ok": bool, "merge": bool, "reasons": [...]} for one pull request.

    `automated` says whether this is one of Claude's own fixes. A person's pull request is never merged
    here and never blocked here (`ok` is true): the maintainer decides. For an automated one, `ok` is
    true only when every rule holds; anything missing or odd in the facts counts against it. With
    `rules_only` the review is left out and nothing merges: it answers whether a review is worth running.
    """
    pr = pr if isinstance(pr, dict) else {}
    number = issue_number(pr.get("head_ref"))
    automated = (pr.get("author") == BOT and pr.get("author_type") == "Bot" and pr.get("same_repo") is True
                 and number is not None)
    if not automated:
        return {"automated": False, "ok": True, "merge": False,
                "reasons": ["Not one of Claude's own fixes: the maintainer merges this."]}

    files, labels = pr.get("files"), pr.get("labels")
    readable = (isinstance(files, list) and isinstance(labels, list)
                and all(isinstance(f, dict) and isinstance(f.get("path"), str)
                        and type(f.get("additions")) is int and type(f.get("deletions")) is int for f in files)
                and isinstance(pr.get("draft"), bool) and isinstance(pr.get("base_ref"), str))
    if not readable:
        return _no(True, ["The pull request could not be read completely."])

    reasons: list[str] = []
    if pr["base_ref"] != BASE:
        reasons.append(f"Not a pull request into {BASE}.")

    issue = pr.get("issue")
    issue_labels = issue.get("labels") if isinstance(issue, dict) else None
    if not (isinstance(issue, dict) and issue.get("number") == number and issue.get("state") == "open"
            and isinstance(issue_labels, list) and TAKEN in issue_labels):
        reasons.append(f"Issue #{number} is not an open issue that Claude was given.")
    else:
        reasons.extend(f"Issue #{number} is labelled {label}." for label in HOLD_LABELS if label in issue_labels)

    if not files:
        reasons.append("The pull request changes nothing.")
    paths = []
    for f in files:
        paths.append(f["path"])
        if "previous_path" in f and f["previous_path"] is not None:
            paths.append(f["previous_path"])
    for path in sorted({p for p in paths if not allowed(p)}, key=str)[:10]:
        reasons.append(f"Changes a file only the maintainer changes: {path}")
    for f in files:
        if f.get("status") in ("removed", "renamed") and any(
                isinstance(p, str) and _is_test(p) for p in (f["path"], f.get("previous_path"))):
            reasons.append(f"Removes or moves a test file: {f.get('previous_path') or f['path']}")
    tests = [f for f in files if _is_test(f["path"])]
    if sum(f["deletions"] for f in tests) > sum(f["additions"] for f in tests):
        reasons.append("Takes more out of the tests than it adds.")
    if any(isinstance(p, str) and _is_code(p) for p in paths) and not any(
            f.get("status") in ("added", "modified") for f in tests):
        reasons.append("Changes code without adding or changing a test.")

    lines = sum(f["additions"] + f["deletions"] for f in files)
    if lines > MAX_LINES:
        reasons.append(f"Too large to merge unseen: {lines} changed lines (limit {MAX_LINES}).")
    if len(files) > MAX_FILES:
        reasons.append(f"Too large to merge unseen: {len(files)} files (limit {MAX_FILES}).")

    if pr["draft"]:
        reasons.append("The pull request is a draft.")
    reasons.extend(f"Labelled {label}." for label in HOLD_LABELS if label in labels)

    if rules_only:
        return {"automated": True, "ok": not reasons, "merge": False, "reasons": reasons}

    verdict = review.get("verdict") if isinstance(review, dict) else None
    if verdict != "approve":
        said = review.get("reasons") if isinstance(review, dict) else None
        reasons.append("The reviewer did not approve." if verdict == "reject" else "There is no review verdict.")
        reasons.extend(f"Reviewer: {r}" for r in (said if isinstance(said, list) else []) if isinstance(r, str))
    return {"automated": True, "ok": not reasons, "merge": not reasons, "reasons": reasons}


def facts(api_pr: dict, api_files: list, api_issue: dict | None) -> dict:
    """What the rules need, from GitHub's `pulls/N`, `pulls/N/files` and `issues/N` answers."""
    head, base = api_pr.get("head") or {}, api_pr.get("base") or {}
    head_repo, base_repo = head.get("repo") or {}, base.get("repo") or {}
    issue = None
    if isinstance(api_issue, dict):
        issue = {"number": api_issue.get("number"), "state": api_issue.get("state"),
                 "labels": [l.get("name") for l in api_issue.get("labels") or [] if isinstance(l, dict)]}
    return {
        "author": (api_pr.get("user") or {}).get("login"),
        "author_type": (api_pr.get("user") or {}).get("type"),
        "head_ref": head.get("ref"),
        "head_sha": head.get("sha"),
        "base_ref": base.get("ref"),
        "same_repo": bool(head_repo.get("full_name")) and head_repo.get("full_name") == base_repo.get("full_name"),
        "draft": api_pr.get("draft"),
        "state": api_pr.get("state"),
        "merged": api_pr.get("merged"),
        "labels": [l.get("name") for l in api_pr.get("labels") or [] if isinstance(l, dict)],
        "issue": issue,
        "files": [{"path": f.get("filename"), "previous_path": f.get("previous_filename"),
                   "status": f.get("status"), "additions": f.get("additions"), "deletions": f.get("deletions")}
                  for f in api_files],
    }


def _api(path: str, paginate: bool = False):
    cmd = ["gh", "api", path] + (["--paginate", "--slurp"] if paginate else [])
    data = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    return [item for page in data for item in page] if paginate else data


def _facts_for(number: str) -> dict:
    repo = os.environ["GH_REPO"]
    api_pr = _api(f"repos/{repo}/pulls/{int(number)}")
    api_files = _api(f"repos/{repo}/pulls/{int(number)}/files?per_page=100", paginate=True)
    n = issue_number((api_pr.get("head") or {}).get("ref"))
    try:
        api_issue = _api(f"repos/{repo}/issues/{n}") if n is not None else None
    except subprocess.CalledProcessError:
        api_issue = None
    return facts(api_pr, api_files, api_issue)


def main(argv: list[str]) -> int:
    if argv[:1] == ["facts"] and len(argv) == 2:
        print(json.dumps(_facts_for(argv[1])))
        return 0
    if argv[:1] == ["decide"] and len(argv) >= 2:
        try:
            with open(argv[1], encoding="utf-8") as f:
                pr = json.load(f)
        except (OSError, ValueError):
            pr = None
        try:
            review = json.loads(os.environ.get("REVIEW") or "null")
        except ValueError:
            review = None
        print(json.dumps(decide(pr, review, rules_only="--rules-only" in argv[2:])))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
