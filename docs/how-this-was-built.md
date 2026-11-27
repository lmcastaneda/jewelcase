# How Jewelcase was built

Jewelcase was written by Claude, Anthropic's AI model, working in Claude Code. All of it: the app, the
tests, the packaging scripts, the README and this page. The person who owns the handheld did not write
or edit a line. This page exists so nobody has to guess about that.

I'm Claude, and this is my account of how it went.

## Who did what

The owner has an Anbernic RG477V and had a routine for looking after it: ask an AI assistant to add or
remove games over adb, one chore at a time, and watch every step. It worked, and it was slow. They
wanted an app that does those chores the way iTunes looked after an iPod.

Their part was this:

- Saying what they wanted, in ordinary sentences in a chat.
- Choosing whenever there was a real choice: what to build next, which of two designs, which licence.
- Telling me when I had it wrong.
- Trying each feature on the real handheld and reporting what happened.
- Deciding when something was good enough to keep.

Everything else was mine: the design, the code, the tests, the reviews, the packaging and the writing.

## How the work went

The first commit was made late on 25 November 2026 and the features were finished early on the 27th,
about 130 commits later. The app is roughly 15,800 lines of Python, with about 15,000 lines of tests
(1,243 of them) beside it.

The routine was the same for every piece of work:

1. **Talk first.** For the first large pieces I wrote a short design and the owner approved it before
   any code existed. Later, smaller features were agreed in a few messages.
2. **Test first.** For each behaviour I wrote a test, ran it to see it fail, and only then wrote the
   code. The tests run against a pretend handheld, so they need no hardware.
3. **A second reader.** Each piece was built on its own branch and then reviewed by a fresh Claude
   that had not seen my reasoning. What it found was fixed, each fix starting with a test that failed.
4. **Then the real thing.** The owner tried it on the handheld and told me what they saw.

The last batch of features was built while the owner was away. I ran several Claudes side by side:
nine building, then five reviewing, then five fixing the 55 things the reviewers found. While the
owner was gone I only ran commands that read from the handheld. Nothing on it was changed.

## What the real handheld taught us

More than once the tests passed and the app was still wrong.

- **The mouse wheel.** Scrolling a page with the pointer resting over a drop-down quietly changed which
  emulator ES-DE used for a system. No test had imagined that. The drop-downs now ignore the wheel.
- **Conversion.** My first design unpacked an archive only on its way to the handheld and kept nothing
  on the PC. The owner wanted the playable game kept beside the archive, so I did it again.
- **A game that copied and would not start.** A PlayStation 3 game arrived intact and failed with a
  permission error, because the emulator had never been given its games folder. Jewelcase cannot grant
  that, so it learned to notice and say so.
- **Covers.** The tick box sat on top of the box art until the owner saw it on screen. And the place I
  had planned to fetch missing covers from answered "not found" to everything, so I changed the source.

## Rules I worked under

- Change nothing on the owner's handheld unless they are there and have agreed. It has a screen lock
  only they can open, and I never tried to get around it.
- Never download, bundle or point to games, BIOS files or firmware.
- Use the network only when asked. Cover downloads are off unless the user switches them on, and I
  did not run one against the owner's library.
- Say what was checked and what was not.

## What this means for you

- Jewelcase has been used on one handheld. Some parts have never met real hardware: making RVZ files,
  a real SD card in a card reader, and the Flatpak. The README says so where it applies.
- The code was written by an AI and reviewed by an AI, and one person tried it on their own device.
  It is built to be careful: every copy is checked, and nothing is deleted without a proven copy
  elsewhere. Still, read it before you trust it with something you cannot replace, and keep a backup.
- The public history begins with a single commit. The working history carries personal details, such
  as a device serial and home folders, so it stayed private.
