# How Jewelcase is looked after

Jewelcase was written by Claude, Anthropic's AI model, and Claude does most of the upkeep too. This page
says what happens when you open an issue or a pull request, and where a person steps in.

## You open an issue

1. **Claude reads it within a few minutes**, labels it and answers. It may ask for details: the version,
   your distribution, the handheld, a few lines of the log.
2. **A small, clear bug or improvement is fixed automatically.** Claude writes a test that shows the
   problem, fixes it, and a pull request is opened that names your issue.
3. **Anything else waits for the maintainer**: large changes, changes of design, anything that needs a
   real handheld to check, and anything about security, releases, the licence, dependencies, data files
   or the network. Such issues are labelled `needs-maintainer`.

What you write is read as a report. Text that tries to instruct the AI is not followed; it sends the
issue to the maintainer.

Please do not ask for, or post, links to games, BIOS files or firmware. Jewelcase does not deal in them.

## What merges by itself

A pull request merges without a person only if every one of these holds:

- It is Claude's fix for an open issue that triage accepted.
- It changes only Python code under `src/jewelcase`, test files, and the README. Everything else is the
  maintainer's: the automation, the packaging, the dependencies, the data files, the licence and the
  documents about how the project is run.
- It is small, it comes with a test, and it takes nothing out of the existing tests.
- A second Claude, which did not write it, reviewed it and approved.
- The whole test suite passes.

The rules are a script with tests of its own (`.github/scripts/merge_gate.py`,
`tests/test_automation.py`), and the check always runs from `main`, so a pull request cannot change the
rules that judge it. If any rule fails, the pull request is labelled `needs-maintainer` and stays open.
Any later change to such a pull request takes the approval back until it has been reviewed again.

## You open a pull request

Thank you. The tests run on it once the maintainer has let them. Pull requests from people are merged by
the maintainer, never automatically. To get it merged quickly:

- Write the failing test first, then the fix. Name tests as plain sentences.
- Keep `src/jewelcase/core` free of Qt; reach the handheld only through the `Device` interface.
- Keep to the rules in the README under "The rules it follows".

```sh
uv sync
uv run jewelcase --demo gui     # the window on made-up games, no handheld needed
uv run pytest                   # everything that needs no handheld
```

## Releases

The AppImage on the Releases page is built and published by a workflow when the version number on
`main` is raised. Raising it is the maintainer's decision: that change never merges by itself.

## For the maintainer

- Write `@claude` in a comment on an issue or in a pull request's conversation to ask Claude for
  something there. Read the thread first: Claude reads all of it, whoever wrote it.
- The label `hold` on a pull request or on its issue stops it merging by itself, also after it was approved.
- On one of Claude's pull requests GitHub shows a run of the tests that waits for your approval. Leave
  it: the fix workflow has started the tests already, and their result is the status `tests`.
- `main` only changes through pull requests. You can merge one that the gate turned down by using your
  bypass on the pull request page.
- A release: on the Actions page choose **release**, **Run workflow**, and enter the new version (for
  example `0.2.0`). A pull request appears that raises the version and lists what has merged since the
  last release. Merging it builds and publishes the release. To release `main` as it is, without a new
  version (the first release, or a second try), tick **publish** instead. With neither, the run is a
  trial: it builds and checks the AppImage and publishes nothing.
- Setting up a fresh copy of the repository: push `main`, run `.github/scripts/setup_repo.sh OWNER/REPO`,
  and follow the three steps it prints. Claude's token lives in an environment tied to `main`, so a
  workflow on any other branch cannot read it.
