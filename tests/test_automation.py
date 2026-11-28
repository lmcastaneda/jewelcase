"""The repository's own automation: what Claude may label, which issue it picks up, what merges by itself.

These rules are plain scripts (.github/scripts) so that they can be tested here and cannot be talked out
of their decision by the text of an issue or a pull request.
"""
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = load("merge_gate")
issue_action = load("issue_action")
pick = load("pick_issue")


# ---------------------------------------------------------------- what merges by itself
def pr(**over):
    base = {"author": "github-actions[bot]", "author_type": "Bot", "head_ref": "claude/issue-7",
            "head_sha": "a" * 40, "base_ref": "main", "same_repo": True, "draft": False, "state": "open",
            "merged": False, "labels": [],
            "issue": {"number": 7, "state": "open", "labels": ["bug", "claude:working"]},
            "files": [{"path": "src/jewelcase/core/plan.py", "status": "modified", "additions": 12, "deletions": 3},
                      {"path": "tests/test_plan.py", "status": "modified", "additions": 20, "deletions": 0}]}
    base.update(over)
    return base


def files(*paths):
    return [{"path": p, "status": "modified", "additions": 3, "deletions": 1} for p in paths]


APPROVE = {"verdict": "approve", "reasons": []}


def test_a_small_reviewed_fix_from_claude_merges_by_itself():
    assert gate.decide(pr(), APPROVE) == {"automated": True, "ok": True, "merge": True, "reasons": []}


def test_a_persons_pull_request_is_never_merged_by_the_gate_and_never_blocked_by_it():
    for who in (pr(author="someone", author_type="User", head_ref="fix-typo"),
                pr(author="someone", author_type="User"),
                pr(same_repo=False),
                pr(author="dependabot[bot]"),
                pr(author="github-actions[bot]", author_type="User"),        # a person named like the bot
                pr(head_ref="claude/something-else"),                        # not a fix for an issue
                pr(head_ref="xclaude/issue-7"), pr(head_ref="claude/issue-7/x")):
        d = gate.decide(who, APPROVE)
        assert d["automated"] is False and d["ok"] is True and d["merge"] is False


@pytest.mark.parametrize("path", [
    "src/jewelcase/core/plan.py", "src/jewelcase/ui/window.py", "tests/test_plan.py", "tests/ui/test_window.py",
    "README.md"])
def test_a_fix_may_change_code_tests_and_the_readme(path):
    assert gate.allowed(path)


@pytest.mark.parametrize("path", [
    ".github/workflows/gate.yml", ".github/scripts/merge_gate.py", ".claude/settings.json", "CLAUDE.md",
    "src/jewelcase/CLAUDE.md", "tests/AGENTS.md", "LICENSE", "pyproject.toml", "uv.lock", "pytest.ini", "tox.ini",
    "setup.cfg", "conftest.py", "tests/conftest.py", "tests/ui/uihelpers.py", "packaging/build-appimage.sh",
    "SECURITY.md", "CONTRIBUTING.md", "docs/how-this-was-built.md", "tests/test_automation.py",
    "src/jewelcase/data/emulators.toml", "src/jewelcase/data/jewelcase.svg", ".gitattributes", ".python-version",
    "notes.txt", "src/other/thing.py", "src/jewelcase/../../x.py", "tests/test_x.py/..", "", "README.md/x"])
def test_anything_else_waits_for_the_maintainer(path):
    assert not gate.allowed(path)
    d = gate.decide(pr(files=pr()["files"] + files(path)), APPROVE)
    assert d["automated"] is True and d["ok"] is False and d["merge"] is False


def test_the_reason_names_the_file():
    d = gate.decide(pr(files=pr()["files"] + files("pyproject.toml")), APPROVE)
    assert "pyproject.toml" in " ".join(d["reasons"])


def test_moving_a_file_counts_as_touching_both_places():
    moved = [{"path": "src/jewelcase/core/x.py", "previous_path": ".github/workflows/gate.yml", "status": "renamed",
              "additions": 0, "deletions": 0}]
    assert gate.decide(pr(files=pr()["files"] + moved), APPROVE)["merge"] is False


def test_a_fix_may_not_delete_move_or_hollow_out_a_test():
    gone = [{"path": "tests/test_plan.py", "status": "removed", "additions": 0, "deletions": 200}]
    moved = [{"path": "tests/test_plan2.py", "previous_path": "tests/test_plan.py", "status": "renamed",
              "additions": 0, "deletions": 0}]
    hollow = [{"path": "src/jewelcase/core/plan.py", "status": "modified", "additions": 5, "deletions": 0},
              {"path": "tests/test_plan.py", "status": "modified", "additions": 4, "deletions": 300}]
    for change in (gone, moved, hollow):
        assert gate.decide(pr(files=change), APPROVE)["merge"] is False


def test_a_fix_that_changes_code_must_come_with_a_test():
    assert gate.decide(pr(files=files("src/jewelcase/core/plan.py")), APPROVE)["merge"] is False
    assert gate.decide(pr(files=files("README.md")), APPROVE)["merge"] is True


def test_a_large_change_waits_for_the_maintainer():
    big = [{"path": "src/jewelcase/core/plan.py", "status": "modified", "additions": gate.MAX_LINES, "deletions": 1},
           {"path": "tests/test_plan.py", "status": "modified", "additions": 1, "deletions": 0}]
    many = [{"path": f"tests/test_{i}.py", "status": "added", "additions": 1, "deletions": 0}
            for i in range(gate.MAX_FILES + 1)]
    assert gate.decide(pr(files=big), APPROVE)["merge"] is False
    assert gate.decide(pr(files=many), APPROVE)["merge"] is False
    assert gate.decide(pr(files=[]), APPROVE)["merge"] is False


def test_the_fix_must_belong_to_an_open_issue_that_claude_was_given():
    for issue in (None, {}, {"number": 8, "state": "open", "labels": ["claude:working"]},
                  {"number": 7, "state": "closed", "labels": ["claude:working"]},
                  {"number": 7, "state": "open", "labels": ["bug"]},
                  {"number": 7, "state": "open", "labels": ["claude:working", "hold"]},
                  {"number": 7, "state": "open", "labels": ["claude:working", "needs-maintainer"]},
                  {"number": 7, "state": "open", "labels": None}):
        d = gate.decide(pr(issue=issue), APPROVE)
        assert d["automated"] is True and d["merge"] is False, issue


def test_only_a_pull_request_into_main_merges():
    assert gate.decide(pr(base_ref="release"), APPROVE)["merge"] is False


@pytest.mark.parametrize("review", [None, {}, {"verdict": "reject", "reasons": ["adds a network call"]},
                                    {"verdict": "APPROVE!"}, {"reasons": []}, "approve", {"verdict": ["approve"]}])
def test_without_a_clear_approval_from_the_reviewer_nothing_merges(review):
    d = gate.decide(pr(), review)
    assert d["ok"] is False and d["merge"] is False


def test_the_reviewers_reasons_are_passed_on():
    d = gate.decide(pr(), {"verdict": "reject", "reasons": ["adds a network call", 5]})
    assert "adds a network call" in " ".join(d["reasons"])


def test_the_rules_can_be_checked_before_the_review_is_paid_for():
    assert gate.decide(pr(), None, rules_only=True) == {"automated": True, "ok": True, "merge": False, "reasons": []}
    assert gate.decide(pr(labels=["hold"]), None, rules_only=True)["ok"] is False


def test_a_draft_or_a_held_pull_request_does_not_merge():
    assert gate.decide(pr(draft=True), APPROVE)["merge"] is False
    for label in ("needs-maintainer", "hold"):
        assert gate.decide(pr(labels=[label]), APPROVE)["merge"] is False


def test_facts_that_are_missing_or_odd_mean_no_merge():
    for odd in (pr(files=None), pr(files=[{"path": 5}]), pr(labels=None), pr(draft=None), pr(base_ref=None),
                pr(files=[{"path": "tests/test_a.py", "status": "modified", "additions": "many", "deletions": 0}])):
        d = gate.decide(odd, APPROVE)
        assert d["automated"] is True and d["ok"] is False and d["merge"] is False
    for nothing in (None, {}, "x", {"author": "github-actions[bot]"}):
        assert gate.decide(nothing, APPROVE)["merge"] is False


def test_the_facts_are_read_from_what_github_reports():
    """The shape GitHub's API gives (pulls/N, pulls/N/files, issues/N), cut down to the fields that are used."""
    api_pr = {"user": {"login": "github-actions[bot]", "type": "Bot"}, "draft": False, "state": "open",
              "merged": False, "labels": [{"name": "bug"}],
              "head": {"ref": "claude/issue-7", "sha": "b" * 40, "repo": {"full_name": "me/jewelcase"}},
              "base": {"ref": "main", "repo": {"full_name": "me/jewelcase"}}}
    api_files = [{"filename": "src/jewelcase/core/plan.py", "status": "modified", "additions": 2, "deletions": 1},
                 {"filename": "tests/test_b.py", "previous_filename": "tests/test_a.py", "status": "renamed",
                  "additions": 0, "deletions": 0}]
    api_issue = {"number": 7, "state": "open", "labels": [{"name": "claude:working"}]}
    f = gate.facts(api_pr, api_files, api_issue)
    assert f["author"] == "github-actions[bot]" and f["author_type"] == "Bot" and f["same_repo"] is True
    assert f["head_ref"] == "claude/issue-7" and f["head_sha"] == "b" * 40 and f["base_ref"] == "main"
    assert f["labels"] == ["bug"] and f["issue"] == {"number": 7, "state": "open", "labels": ["claude:working"]}
    assert f["files"][1] == {"path": "tests/test_b.py", "previous_path": "tests/test_a.py", "status": "renamed",
                             "additions": 0, "deletions": 0}
    assert gate.decide(f, APPROVE)["merge"] is False                 # the renamed test
    assert gate.issue_number(f["head_ref"]) == 7 and gate.issue_number("claude/issue-x") is None
    fork = dict(api_pr, head={"ref": "claude/issue-7", "sha": "c" * 40, "repo": None})        # a deleted fork
    assert gate.facts(fork, [], None)["same_repo"] is False


def test_the_command_reads_the_facts_file_and_the_review(tmp_path, monkeypatch, capsys):
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(pr()))
    monkeypatch.setenv("REVIEW", json.dumps(APPROVE))
    assert gate.main(["decide", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["merge"] is True
    monkeypatch.setenv("REVIEW", "not json")
    gate.main(["decide", str(path)])
    assert json.loads(capsys.readouterr().out)["merge"] is False
    gate.main(["decide", str(tmp_path / "missing.json")])
    assert json.loads(capsys.readouterr().out)["merge"] is False
    monkeypatch.delenv("REVIEW")
    gate.main(["decide", str(path), "--rules-only"])
    assert json.loads(capsys.readouterr().out) == {"automated": True, "ok": True, "merge": False, "reasons": []}


# ---------------------------------------------------------------- what triage may do to an issue
def test_triage_may_only_use_its_own_labels():
    assert issue_action.label_args("12", ["bug", "claude:ready"], ["needs-info"]) == [
        "gh", "issue", "edit", "12", "--add-label", "bug,claude:ready", "--remove-label", "needs-info"]
    for label in ("claude:working", "hold", "wontfix", "bug,hold", "", " bug"):
        with pytest.raises(ValueError):
            issue_action.label_args("12", [label], [])


def test_triage_can_take_away_only_its_own_question_never_a_hold():
    for label in ("needs-maintainer", "hold", "claude:working", "bug", "out-of-scope", ""):
        with pytest.raises(ValueError):
            issue_action.label_args("12", [], [label])


def test_triage_cannot_hand_an_issue_to_claude_and_to_the_maintainer_at_once():
    with pytest.raises(ValueError):
        issue_action.label_args("12", ["claude:ready", "needs-maintainer"], [])


def test_triage_acts_on_the_issue_it_was_started_for_and_no_other():
    for number in ("", "12 13", "12;rm", "-1", "abc", None):
        with pytest.raises(ValueError):
            issue_action.label_args(number, ["bug"], [])
        with pytest.raises(ValueError):
            issue_action.comment_args(number, "hello")


def test_a_comment_is_plain_text_of_a_sensible_length():
    assert issue_action.comment_args("12", "Thanks. Which emulator? See #4.") == [
        "gh", "issue", "comment", "12", "--body", "Thanks. Which emulator? See #4."]
    for body in ("", "   ", "x" * (issue_action.MAX_COMMENT + 1)):
        with pytest.raises(ValueError):
            issue_action.comment_args("12", body)


@pytest.mark.parametrize("body", [
    "see https://example.com/thing", "go to http://x.y", "ask @someone about it", "token sk-ant-oat01-abcdef",
    "ghs_abcdefghijklmnopqrstuvwxyz0123456789", "github_pat_11ABCDEFG0123456789_abcdefghij", "ghp_" + "a" * 36])
def test_a_comment_carries_no_link_no_mention_and_nothing_shaped_like_a_credential(body):
    assert not issue_action.publishable(body)
    with pytest.raises(ValueError):
        issue_action.comment_args("12", body)


def test_a_comment_never_repeats_a_secret_from_its_own_environment(monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "s3cr3t-value-that-is-long-enough")
    assert not issue_action.publishable("the value is s3cr3t-value-that-is-long-enough ok")
    assert issue_action.publishable("ordinary text with an email-like a.b and a path ~/.local/share/jewelcase")


def test_the_issue_number_comes_from_the_workflow_not_from_the_command(monkeypatch):
    ran = []
    monkeypatch.setenv("ISSUE_NUMBER", "12")
    assert issue_action.main(["label", "--add", "bug"], run=lambda cmd: ran.append(cmd) or 0) == 0
    assert ran == [["gh", "issue", "edit", "12", "--add-label", "bug"]]
    assert issue_action.main(["label", "--add", "wontfix"], run=lambda cmd: ran.append(cmd) or 0) != 0
    assert len(ran) == 1
    monkeypatch.delenv("ISSUE_NUMBER")
    assert issue_action.main(["label", "--add", "bug"], run=lambda cmd: ran.append(cmd) or 0) != 0
    assert len(ran) == 1


def test_text_for_a_pull_request_is_checked_the_same_way(tmp_path):
    good, bad = tmp_path / "good.md", tmp_path / "bad.md"
    good.write_text("Fixes the sort order. `plan.py` compared names without folding case.")
    bad.write_text("here: sk-ant-oat01-abcdefghijklmnop")
    assert issue_action.main(["check", str(good)]) == 0
    assert issue_action.main(["check", str(bad)]) != 0
    assert issue_action.main(["check", str(tmp_path / "missing.md")]) != 0


# ---------------------------------------------------------------- which issue the fixer picks up
def issue(number, *labels, created="2026-11-27T10:00:00Z"):
    return {"number": number, "createdAt": created, "labels": [{"name": n} for n in labels]}


def test_the_oldest_ready_issue_is_picked():
    issues = [issue(9, "bug", "claude:ready", created="2026-11-27T12:00:00Z"),
              issue(4, "bug", "claude:ready", created="2026-11-26T12:00:00Z"),
              issue(2, "bug")]
    assert pick.choose(issues, None) == 4


@pytest.mark.parametrize("blocker", ["claude:working", "needs-maintainer", "needs-info", "duplicate",
                                     "out-of-scope", "hold"])
def test_an_issue_that_is_taken_or_set_aside_is_not_picked(blocker):
    assert pick.choose([issue(4, "claude:ready", blocker)], None) is None
    assert pick.choose([issue(4, "claude:ready", blocker)], 4) is None


def test_a_named_issue_is_picked_only_if_it_is_ready():
    issues = [issue(4, "bug", "claude:ready"), issue(5, "bug")]
    assert pick.choose(issues, 4) == 4
    assert pick.choose(issues, 5) is None
    assert pick.choose(issues, 99) is None


def test_only_claudes_own_fixes_count_as_waiting_not_a_strangers_look_alike_branch():
    prs = [{"headRefName": "claude/issue-4", "isCrossRepository": False},
           {"headRefName": "claude/issue-5", "isCrossRepository": True},          # from a fork
           {"headRefName": "claude/whatever", "isCrossRepository": False},
           {"headRefName": "fix-typo", "isCrossRepository": False}, {}]
    assert pick.count_fixes(prs) == 1


def test_no_new_fix_starts_while_enough_are_waiting_or_enough_were_made_today():
    assert pick.may_start(0, 0) and pick.may_start(pick.MAX_OPEN - 1, pick.MAX_PER_DAY - 1)
    assert not pick.may_start(pick.MAX_OPEN, 0)
    assert not pick.may_start(0, pick.MAX_PER_DAY)


def merged(number, branch, **over):
    base = {"number": number, "headRefName": branch, "isCrossRepository": False, "baseRefName": "main",
            "author": {"login": "app/github-actions", "is_bot": True}}
    return {**base, **over}


def test_an_issue_whose_fix_has_merged_is_done():
    """GitHub does not close the issue when a pull request opened by a workflow merges, so this does."""
    issues = [issue(4, "bug", "claude:working"), issue(5, "bug", "claude:working"), issue(6, "bug")]
    prs = [merged(10, "claude/issue-4"), merged(11, "claude/issue-6"), merged(12, "something-else")]
    assert pick.fixed(issues, prs) == [(4, 10)]         # 5 has no merged fix; nobody was working on 6


def test_only_claudes_own_merged_fix_closes_an_issue_not_a_look_alike():
    issues = [issue(4, "claude:working")]
    for odd in (merged(10, "claude/issue-4", isCrossRepository=True),                       # from a fork
                merged(10, "claude/issue-4", baseRefName="some-branch"),                    # not into main
                merged(10, "claude/issue-4", author={"login": "somebody", "is_bot": False}),
                merged(10, "claude/issue-44"), merged(10, "x/claude/issue-4"), {}):
        assert pick.fixed(issues, [odd]) == []


def test_a_fixed_issue_is_closed_with_a_word_and_no_longer_marked_as_taken(monkeypatch):
    calls = []

    def gh(*args):
        calls.append(args)
        if args[:2] == ("issue", "list"):
            return json.dumps([issue(4, "bug", "claude:working"), issue(5, "claude:working")])
        if args[:2] == ("pr", "list"):
            return json.dumps([merged(10, "claude/issue-4")])
        return ""

    monkeypatch.setattr(pick, "_gh", gh)
    assert pick.tidy() == [(4, 10)]
    changes = [c for c in calls if c[1] in ("close", "edit")]
    assert changes == [("issue", "close", "4", "--comment", "Fixed by pull request #10, which has been merged."),
                       ("issue", "edit", "4", "--remove-label", "claude:working")]


# ---------------------------------------------------------------- the workflow files
def workflow(name):
    doc = yaml.safe_load((WORKFLOWS / name).read_text())
    doc["on"] = doc.pop(True, doc.get("on"))            # YAML reads the key `on` as the boolean true
    return doc


def all_jobs():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = workflow(path.name)
        for name, job in doc["jobs"].items():
            yield path.name, name, job, doc


def steps(job):
    return job.get("steps", [])


def claude_steps(job):
    return [s for s in steps(job) if "claude-code-action" in s.get("uses", "")]


def allowed_tools(step):
    return re.search(r'--allowedTools "([^"]*)"', step["with"]["claude_args"]).group(1).split(",")


def test_every_workflow_starts_with_no_permissions_and_grants_them_per_job():
    for file, name, job, doc in all_jobs():
        assert doc.get("permissions") == {}, f"{file} must start from `permissions: {{}}`"
        assert "permissions" in job, f"{file}: job {name} states no permissions"
        assert "env" not in doc, f"{file}: values are handed to jobs, not to the whole workflow"


def test_every_action_is_pinned_to_an_exact_commit():
    for file, name, job, _ in all_jobs():
        for step in steps(job):
            if "uses" in step:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), f"{file}: {step['uses']}"


def test_text_other_people_wrote_is_never_pasted_into_a_step():
    """Titles, bodies, comments, branch names and Claude's own answers reach a step only through its
    environment, through a file, or through gh: never as part of a script, a prompt or an argument."""
    risky = re.compile(r"\$\{\{[^}]*(\.title|\.body|\.message|head_ref|head\.ref|head\.label|head_branch"
                       r"|\.name\b|\.email|structured_output|outputs\.review)[^}]*\}\}")
    for file, name, job, _ in all_jobs():
        for step in steps(job):
            texts = [step.get("run", "")] + [str(v) for v in (step.get("with") or {}).values()]
            for text in texts:
                assert not risky.search(text), f"{file}: job {name} pastes outside text into a step"


def test_claudes_token_is_only_handed_to_jobs_in_the_environment_tied_to_main():
    """A workflow pushed on another branch must not be able to read the token; the environment refuses it."""
    for file, name, job, _ in all_jobs():
        uses_token = "CLAUDE_CODE_OAUTH_TOKEN" in yaml.safe_dump(job)
        assert (job.get("environment") == "claude") == uses_token, f"{file}: job {name}"
        assert bool(claude_steps(job)) == uses_token, f"{file}: job {name}"


def test_every_job_with_claudes_token_starts_from_an_event_that_runs_on_main():
    """Review events run the workflow file as the pull request has it; those must never get the token."""
    on_main = {"issues", "issue_comment", "pull_request_target", "workflow_dispatch", "schedule"}
    for file, name, job, doc in all_jobs():
        if job.get("environment") == "claude":
            assert set(doc["on"]) <= on_main, f"{file} listens to {sorted(set(doc['on']) - on_main)}"


def test_a_job_where_claude_reads_strangers_text_can_write_nothing_but_issue_labels_and_comments():
    """Triage, the fix writer and the reviewer all read text anyone can write."""
    for file, name in (("triage.yml", "triage"), ("fix.yml", "write"), ("gate.yml", "review")):
        job = workflow(file)["jobs"][name]
        writes = {k for k, v in job["permissions"].items() if v == "write"}
        assert writes <= ({"issues"} if name == "triage" else set()), f"{file}:{name} may write {writes}"
        step, = claude_steps(job)
        assert step["with"]["github_token"] == "${{ secrets.GITHUB_TOKEN }}"
        assert "--permission-mode" in step["with"]["claude_args"]
        assert "Read(//proc/**)" in step["with"]["claude_args"]
        for s in steps(job):
            if "actions/checkout" in s.get("uses", ""):
                assert (s.get("with") or {}).get("persist-credentials") is False, f"{file}:{name}"


def test_triage_can_only_look_and_use_its_one_script():
    step, = claude_steps(workflow("triage.yml")["jobs"]["triage"])
    assert all(t in ("Read", "Grep", "Glob") or t.startswith(("Bash(gh issue view:", "Bash(gh issue list:",
                                                              "Bash(python3 .github/scripts/issue_action.py "))
               for t in allowed_tools(step)), allowed_tools(step)
    assert step["with"]["allowed_non_write_users"] == "*"


def test_the_fix_writer_cannot_push_or_post_and_the_job_that_does_runs_no_claude():
    jobs = workflow("fix.yml")["jobs"]
    step, = claude_steps(jobs["write"])
    tools = allowed_tools(step)
    assert not any(t.startswith(("Bash(git push", "Bash(gh pr", "Bash(gh issue comment", "Bash(gh issue edit",
                                 "Bash(gh api", "Bash(gh workflow", "Bash(curl", "Bash(bash", "Bash(python"))
                   for t in tools), tools
    assert step["with"]["allowed_non_write_users"] == "*"           # this is what switches the sandbox on
    assert jobs["open"]["permissions"]["contents"] == "write" and not claude_steps(jobs["open"])
    assert jobs["open"]["needs"] == ["pick", "write"]


def test_the_reviewer_can_only_look():
    step, = claude_steps(workflow("gate.yml")["jobs"]["review"])
    assert all(t in ("Read", "Grep", "Glob") or t.startswith(("Bash(gh pr view:", "Bash(gh pr diff:",
                                                              "Bash(gh issue view:"))
               for t in allowed_tools(step)), allowed_tools(step)


def test_claude_runs_for_the_workflows_own_bot_where_one_workflow_starts_another_and_for_no_other_bot():
    """Triage starts the fix and the fix starts the review, both as the workflows' own bot, and the action
    refuses a bot it was not told about. Only a job that nobody outside can start may name that bot."""
    ours = "github-actions[bot]"
    named = {(file, name): step["with"].get("allowed_bots", "")
             for file, name, job, _ in all_jobs() for step in claude_steps(job)}
    assert {key for key, bots in named.items() if bots} == {("fix.yml", "write"), ("gate.yml", "review")}
    assert named["fix.yml", "write"] == named["gate.yml", "review"] == ours
    # the fix starts only by hand, from triage or on the clock
    assert set(workflow("fix.yml")["on"]) == {"workflow_dispatch", "schedule"}
    # the review starts only when the gate itself was started that way, never straight from a pull request
    gate = workflow("gate.yml")["jobs"]
    rules = next(s["run"] for s in steps(gate["rules"]) if s.get("id") == "rules")
    assert gate["review"]["if"] == "needs.rules.outputs.next == 'review'"
    assert rules.count("next=review") == 1
    assert re.search(r'elif \[ "\$EVENT" = "workflow_dispatch" \]; then\n(?:(?!\n\s*(?:elif|else|fi)\b).)*next=review',
                     rules, re.S)


def test_the_gate_runs_from_main_and_only_puts_the_pull_requests_files_in_a_side_folder():
    doc = workflow("gate.yml")
    assert set(doc["on"]) == {"pull_request_target", "workflow_dispatch"}
    for name, job in doc["jobs"].items():
        for step in steps(job):
            if "actions/checkout" in step.get("uses", ""):
                with_ = step.get("with") or {}
                assert "ref" not in with_ or with_.get("path") == "pr-head", f"gate.yml:{name}"


def test_the_decision_is_taken_again_from_fresh_facts_after_the_review():
    job = workflow("gate.yml")["jobs"]["merge"]
    assert job["needs"] == ["rules", "review"] and job["if"].startswith("always()")
    text = "\n".join(s.get("run", "") for s in steps(job))
    assert "merge_gate.py facts" in text and "merge_gate.py decide" in text and "--rules-only" not in text
    assert "--match-head-commit" in text


def test_the_job_that_runs_the_tests_holds_no_secrets_and_cannot_write():
    doc = workflow("tests.yml")
    assert doc["jobs"]["tests"]["permissions"] == {"contents": "read"}      # the job that runs the code
    assert set(doc["jobs"]) == {"tests", "report"}
    assert "secrets." not in (WORKFLOWS / "tests.yml").read_text()
    assert "workflow_dispatch" in doc["on"]             # how the tests are started on one of Claude's fixes


def report(tmp_path, result, sha="a" * 40):
    """Run the step that reports the tests' result, with a stand-in for gh. Returns what it asked gh to do."""
    step, = steps(workflow("tests.yml")["jobs"]["report"])
    log = tmp_path / "gh.log"
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_LOG"\n')
    gh.chmod(0o755)
    env = {"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "GH_LOG": str(log), "GH_REPO": "someone/jewelcase",
           "SHA": sha, "RESULT": result, "RUN_URL": "https://example.invalid/run/1"}
    done = subprocess.run(["bash", "-eo", "pipefail", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return log.read_text() if log.exists() else ""


def test_fixed_issues_are_closed_right_after_the_merge_and_again_whenever_a_fix_is_picked():
    """The gate waits for the pull request it approved to merge and then tidies up. A pull request that
    merges later (its tests were run again) is caught the next time the fix workflow runs."""
    merge = workflow("gate.yml")["jobs"]["merge"]
    script = next(s["run"] for s in steps(merge) if "merge_gate.py decide" in s.get("run", ""))
    assert script.index("gh pr merge \"$PR\" --auto") < script.index("pick_issue.py tidy")
    assert merge["permissions"]["issues"] == "write"
    assert "tidy()" in (SCRIPTS / "pick_issue.py").read_text().split("def main()")[1]


def test_the_tests_started_for_one_of_claudes_fixes_report_their_result_as_a_status():
    """GitHub does not count the checks of a run that another workflow started, and it holds the tests of a
    pull request that a workflow opened until a person approves them. So when the fix workflow starts the
    tests, a second job says how they ended as the status `tests`, which is tied to the commit and counts.
    That job runs after the code, on another machine, and runs none of it."""
    jobs = workflow("tests.yml")["jobs"]
    job = jobs["report"]
    assert job["needs"] == "tests"
    assert job["if"] == "always() && github.event_name == 'workflow_dispatch'"
    assert job["permissions"] == {"statuses": "write"}
    assert not any("uses" in s for s in steps(job))     # it checks nothing out
    step, = steps(job)
    assert step["env"]["SHA"] == "${{ github.sha }}"
    assert step["env"]["RESULT"] == "${{ needs.tests.result }}"
    assert not (WORKFLOWS / "report.yml").exists()      # a workflow_run workflow is not started for such a run


def test_tests_that_pass_are_reported_for_their_commit(tmp_path):
    call, = report(tmp_path, "success", sha="0123456789abcdef0123456789abcdef01234567").splitlines()
    assert "repos/someone/jewelcase/statuses/0123456789abcdef0123456789abcdef01234567" in call
    assert "state=success" in call and "context=tests" in call


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_tests_that_fail_or_do_not_finish_are_reported_as_failed(tmp_path, result):
    """A pull request must not wait for ever on tests that were stopped: a later run reports afresh."""
    call, = report(tmp_path, result).splitlines()
    assert "state=failure" in call and "context=tests" in call


def test_nothing_is_reported_for_something_that_is_not_a_commit(tmp_path):
    assert report(tmp_path, "success", sha="main; echo") == ""


def test_only_these_jobs_may_write_to_the_repository_and_none_of_them_runs_a_strangers_text_through_claude():
    writers = {f"{file}:{name}" for file, name, job, _ in all_jobs() if job["permissions"].get("contents") == "write"}
    assert writers == {"fix.yml:open", "gate.yml:merge", "release.yml:release", "claude.yml:claude"}


def test_the_maintainers_claude_answers_only_people_who_belong_to_the_repository():
    job = workflow("claude.yml")["jobs"]["claude"]
    assert set(workflow("claude.yml")["on"]) == {"issue_comment"}
    for who in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert who in job["if"]


def test_a_release_is_built_only_from_a_version_tag():
    assert workflow("release.yml")["on"] == {"push": {"tags": ["v*"]}}


def test_the_checks_main_requires_are_the_ones_the_workflows_report():
    setup = (SCRIPTS / "setup_repo.sh").read_text()
    assert '"context": "tests"' in setup and "tests" in workflow("tests.yml")["jobs"]
    assert "context=tests" in (WORKFLOWS / "tests.yml").read_text()
    assert '"context": "gate"' in setup and "context=gate" in (WORKFLOWS / "gate.yml").read_text()
