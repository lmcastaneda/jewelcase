#!/usr/bin/env python3
"""Picks the issue Claude fixes next, marks it as taken, and tells the workflow its number.

An issue is picked only if triage labelled it `claude:ready` and nothing has set it aside since. With
WANTED set (the triage workflow passes the issue it just read) only that issue is considered.
Writes `number=<n>` to GITHUB_OUTPUT, or `number=` when there is nothing to do.

Before picking, and when called as `pick_issue.py tidy`, it closes the issues whose fix has merged.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys

READY = "claude:ready"
TAKEN = "claude:working"
BLOCKERS = (TAKEN, "needs-maintainer", "needs-info", "duplicate", "out-of-scope", "hold")
BRANCH = re.compile(r"claude/issue-[1-9][0-9]{0,8}")
MAX_OPEN = 3        # fixes waiting to merge; no new one starts beyond this
MAX_PER_DAY = 6     # fixes opened in the last 24 hours


def _labels(issue: dict) -> set[str]:
    return {l.get("name") for l in issue.get("labels") or [] if isinstance(l, dict)}


def eligible(issue: dict) -> bool:
    labels = _labels(issue)
    return READY in labels and not labels.intersection(BLOCKERS)


def choose(issues: list[dict], wanted: int | None) -> int | None:
    """The number of the issue to fix: the wanted one if it is eligible, otherwise the oldest eligible one."""
    ok = [i for i in issues if isinstance(i.get("number"), int) and eligible(i)]
    if wanted is not None:
        return wanted if any(i["number"] == wanted for i in ok) else None
    ok.sort(key=lambda i: (str(i.get("createdAt") or ""), i["number"]))
    return ok[0]["number"] if ok else None


def count_fixes(prs: list[dict]) -> int:
    """How many of these pull requests are Claude's own fixes: a claude/issue-N branch in this repository.

    A pull request from a fork can name its branch anything, so those never count.
    """
    return sum(1 for p in prs if p.get("isCrossRepository") is False
               and isinstance(p.get("headRefName"), str) and BRANCH.fullmatch(p["headRefName"]))


def may_start(open_fixes: int, fixes_today: int) -> bool:
    return open_fixes < MAX_OPEN and fixes_today < MAX_PER_DAY


def fixed(issues: list[dict], merged: list[dict]) -> list[tuple[int, int]]:
    """(issue, pull request) for every issue Claude was working on whose fix has merged.

    Only Claude's own fix counts: opened by the workflows' bot from the branch claude/issue-N in this
    repository and merged into main. Anyone can name a branch in a fork.
    """
    by_branch = {}
    for p in merged:
        author = p.get("author") or {}
        if (p.get("isCrossRepository") is False and p.get("baseRefName") == "main"
                and author.get("is_bot") is True and author.get("login") == "app/github-actions"
                and isinstance(p.get("headRefName"), str) and isinstance(p.get("number"), int)):
            by_branch[p["headRefName"]] = p["number"]
    done = []
    for i in issues:
        number = i.get("number")
        if isinstance(number, int) and TAKEN in _labels(i) and f"claude/issue-{number}" in by_branch:
            done.append((number, by_branch[f"claude/issue-{number}"]))
    return sorted(done)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def tidy() -> list[tuple[int, int]]:
    """Close the issues whose fix has merged. GitHub does not do it for a pull request a workflow opened."""
    issues = json.loads(_gh("issue", "list", "--state", "open", "--label", TAKEN, "--limit", "100",
                            "--json", "number,labels"))
    if not issues:
        return []
    prs = json.loads(_gh("pr", "list", "--state", "merged", "--limit", "100",
                         "--json", "number,headRefName,isCrossRepository,baseRefName,author"))
    done = fixed(issues, prs)
    for issue, pull in done:
        _gh("issue", "close", str(issue), "--comment", f"Fixed by pull request #{pull}, which has been merged.")
        _gh("issue", "edit", str(issue), "--remove-label", TAKEN)
        print(f"Closed issue #{issue}: fixed by pull request #{pull}.")
    return done


def main() -> int:
    if sys.argv[1:] == ["tidy"]:
        tidy()
        return 0
    try:
        tidy()
    except subprocess.CalledProcessError as error:      # tidying up must not stop the next fix
        print(f"Could not close fixed issues: {error.stderr.strip() or error}")
    wanted = os.environ.get("WANTED", "").strip()
    wanted_number = int(wanted) if wanted.isdigit() else None
    fields = "headRefName,isCrossRepository"
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    open_fixes = count_fixes(json.loads(_gh("pr", "list", "--state", "open", "--limit", "100", "--json", fields)))
    today = count_fixes(json.loads(_gh("pr", "list", "--state", "all", "--limit", "100", "--json", fields,
                                       "--search", f"created:>={since}")))
    number = None
    if not may_start(open_fixes, today):
        print(f"{open_fixes} fixes are waiting to merge and {today} were opened today; not starting another.")
    else:
        issues = json.loads(_gh("issue", "list", "--state", "open", "--label", READY, "--limit", "100",
                                "--json", "number,labels,createdAt"))
        number = choose(issues, wanted_number)
        if number is None:
            print("No issue is ready for a fix.")
        else:
            _gh("issue", "edit", str(number), "--add-label", TAKEN)
            print(f"Picked issue #{number}.")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"number={number if number is not None else ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
