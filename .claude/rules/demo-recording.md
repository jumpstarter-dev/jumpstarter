# Recording the documentation demo

The asciicast on the documentation landing page is produced by
`demo/board_demo.py`, which drives a real board through the whole Jumpstarter
workflow and asserts on every step. `demo/README.md` documents the script
itself; this file records the decisions behind it, so they are not relitigated
or quietly undone.

## Where things live

| Thing | Path |
| --- | --- |
| recording harness | `demo/human_shell.py` |
| scenario and narration | `demo/board_demo.py` |
| the pytest suite the demo runs | `demo/tests/test_board.py` |
| the recording the docs play | `docs/source/_static/demo.cast` |
| player configuration | `docs/source/index.md` |
| terminal colours | `docs/source/_static/css/custom.css` |

## Pacing

The recording is meant to be read while it plays. If a change makes it
shorter at the cost of legibility, it is the wrong trade.

- Narration is typed as shell comments and then **held on screen** for
  `READING_PACE` (0.045s) per character, about 110 words per minute. That is
  slow for reading alone, and about right for reading while also watching a
  terminal.
- Command output gets a `settle` pause before the next step starts.
- Idle gaps are capped at `--idle-limit` so flashing and booting do not become
  dead air, but deliberate pauses bypass that cap through
  `Asciicast.pause()`. Do not "simplify" `pause()` away: without it every
  reading pause collapses to 2 seconds.
- Everything scales with `--speed`, so `--speed 8` verification runs cost
  nothing.
- The docs player plays at **speed 1**. The pacing lives in the recording;
  raising the playback speed undoes all of the above.

## Narration

Write for someone who has never seen Jumpstarter and cannot see the source
files being referred to.

- Say what a thing *is for*, not what it is called. "run.sh stands in for an
  application: it writes a file saying it ran" rather than "this little
  program records which boot it last ran under".
- Do not refer to a file, test or concept the viewer cannot see. If the
  pytest suite matters, describe what it checks in plain language, one idea
  per line, rather than naming it.
- Point at something readable *before* it is needed, not after. The GitHub
  URL of `demo/tests/test_board.py` (`TESTS_URL`) is narrated just ahead of
  the `pytest` run, where a viewer is still deciding whether they want the
  detail. At the end it is an afterthought: the run is already over, and a
  looping player takes the frame away. The demo closes on handing the board
  back, which is the end of the story.

## Legibility and theming

The documentation has a light and a dark theme. The player does **not** blend
into the page: `custom.css` pins a terminal background (`#181825`) and a full
16 colour Catppuccin Mocha palette for `#demo-player`, identical in both
themes, and frames it with a border so it reads as a deliberate terminal.

It used to inherit the page background and carry a second palette for light
mode. That cannot be made to work, because the recording captures tools that
choose their own colours and some of them emit 24 bit RGB which no palette
can remap. rich's progress bar during the flash step fades between `#f92672`
and `#3a3a3a`: 1.5:1 on a dark page, 3.8:1 on a light one. Tuning the ANSI
slots for one theme left the other broken, and the next re-record would have
reintroduced the problem silently.

Two things the palette is doing on purpose, so they are not "tidied" back to
stock:

- `--term-color-8` is `#9399b2`, not Mocha's `#6c7086` (3.6:1). The shell
  prompt writes its `jumpstarter` prefix in SGR 90.
- `.ap-faint` is lifted from the player's `opacity: 0.5` to `0.75`. rich
  dims its log timestamps with SGR 2, and at 0.5 they sit at 3.8:1.

Every SGR colour the recording actually uses clears 4.5:1 against the pinned
background. If a re-record introduces a new one, check it rather than
assuming. Check both themes after any change to the colours or to
`--comment-style` - the panel should look the same in each, with only the
border blending.

## Nothing personal in the recording

The cast is published, so it must not carry the recorder's identity or
machine:

- pytest runs with `--no-header`, which drops the rootdir and the venv
  interpreter path.
- The disk image comes from an org registry,
  `quay.io/jumpstarter-dev/autosd-demo-disk:asciinema`, not a personal one.
- The client name in the `LEASED BY` column comes from the recorder's
  credentials and is accepted as-is.

### Registry credentials

`--registry-creds` takes a JSON file with a `token` field and passes it as
`--bearer "$REGISTRY_TOKEN"`, a shell variable, so the recording shows an
authenticated pull without the credential.

**This is not currently safe for a published recording**: `j storage flash`
prints the `fls` command line it builds, including
`-H 'Authorization: Bearer …'`, to stdout. Until that is fixed in the storage
driver, record only from a registry that serves the image anonymously.

A pull from the OpenShift internal registry was also observed to authenticate
and then wedge indefinitely with no progress and no error, so a public image
is the safer choice for other reasons too.

## Lease names

The lease name is on screen, so it is short and readable: `demo-MMDD`. It
cannot be a fixed string. Releasing a lease does not delete the custom
resource, so the name stays reserved: `jmp create lease --lease-id demo`
then fails with `already exists`, and `jmp delete lease demo` refuses with
`has already been released`. Only deleting the CR from the cluster frees a
name, which a plain client config cannot do.

`pick_lease_name()` therefore lists existing leases with
`jmp get leases -a -o json` and appends a letter when needed: `demo-1005b`,
`demo-1005c`. Note that `jmp get leases <name>` exists in the repo but not
necessarily in the installed CLI, which is why the list form is used.

## Before committing a new recording

1. `./demo/board_demo.py --no-cast --speed 8` passes, or the full recording
   run exits 0 - the script asserts on every step, so a green run means the
   lab behaved.
2. Play it back and read it: `asciinema play docs/source/_static/demo.cast`.
3. Check the landing page in both themes.
4. `grep` the cast for the recorder's username, home directory and any
   token.
