# Security

## Reporting a problem

Please report a security problem privately: on the repository's **Security** tab choose **Report a
vulnerability**. Do not open a public issue for it. Public issues are read and answered by an AI, which
is the wrong place for something that should stay quiet until it is fixed.

Jewelcase talks to your handheld over adb and writes into your game library, so these matter most:

- anything that could delete or overwrite files it should not touch, on the handheld or on the PC;
- anything that could make it run a command of an attacker's choosing, for instance through a file
  name, an archive, a game list or a settings file;
- anything that sends data off your computer without you asking.

## How the automation is kept in check

Most upkeep is done by Claude, Anthropic's AI model, and anyone can write an issue that Claude reads. The
automation is built on the assumption that such text may be hostile:

- The job that reads new issues can only label and comment on that one issue, through a script that
  refuses links, mentions and anything shaped like a credential.
- The job in which Claude writes a fix and runs the tests can only read the repository. A separate job
  that runs no code and no AI turns its work into a pull request.
- `main` changes only through pull requests whose tests pass.
- A pull request merges by itself only under fixed rules: Claude's fix for an accepted issue, touching
  only code, tests and the README, small, with a test, and approved by a second Claude that did not write
  it. The rules are a tested script that always runs from `main`.
- A release is published only when the version number on `main` is raised, or when the maintainer starts
  one by hand for the version `main` states. Raising the version never merges by itself, and the
  automation's own jobs cannot start a release, so a person decides on every one. The job that builds a
  release can only read; the job that publishes it runs no code. A tag, once made, cannot be moved or
  deleted by the automation, and a tag that stands on another commit stops the release.
- The credential the AI runs on is available only to workflows on `main`.

This lowers the risk; it does not remove it. The second review is done by an AI too, and it reads a change
that began as a stranger's words. A change that passes every rule and still does harm is possible, which is
why releases stay with a person. If you find a way through, please report it as above.
