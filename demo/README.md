# Jumpstarter demo recordings

`board_demo.py` drives a real board through the full Jumpstarter workflow and
records it as an [asciicast](https://docs.asciinema.org/manual/asciicast/v2/).

Every command in the recording is real, runs against lab hardware, and is
checked against what it printed. The only thing that is synthesised is the
keystroke timing, so the result looks like someone working at a terminal
instead of a CI log. Because the assertions are real, the same script doubles
as a lab smoke test.

## The scenario

| Step | What it shows |
| --- | --- |
| 1 | `jmp get exporters -l board-type=…` — the boards of one type, and which are free |
| 2 | `jmp get leases`, `jmp create lease` — taking a board for a fixed duration |
| 3 | `j storage flash oci://…` — flashing a disk image from a registry |
| 4 | `j power cycle` — rebooting into the image that was just written |
| 5 | `j ssh -- cat /etc/os-release` — proving the new image is what booted |
| 6 | `j ssh -- systemd-analyze time` — how long the cold boot took |
| 7 | `j mount /tmp/dut -r /` — mounting the board's filesystem so a small program can be copied onto it with plain `cp` |
| 8 | `pytest demo/tests -v --no-header` — testing the board through the Python driver API, over the same lease |
| 9 | `jmp delete lease` — giving the board back |

Step 8 is the one that shows what Jumpstarter is for. `demo/tests/test_board.py`
subclasses `JumpstarterTest`, whose `client` fixture picks up
`JUMPSTARTER_HOST` from the surrounding `jmp shell`, so the tests drive the
same leased board the CLI was just using. The suite is shaped like something
a team would gate a build on rather than a smoke test:

| Test | Asserts |
| --- | --- |
| `test_booted_the_image_we_flashed` | `bootc` reports the image the demo flashed |
| `test_system_came_up_clean` | no systemd units failed during boot |
| `test_cold_boot_is_within_budget` | cold boot ≤ `DEMO_BOOT_BUDGET`, reporting the slowest units if not |
| `test_workload_was_deployed` | `j mount` really wrote the program to the board |
| `test_workload_runs` | it executes and records the boot it ran under |
| `test_stays_healthy_under_load` | `stress-ng` on every core plus memory: no failures, no OOM kills, no failed units, workload still good |
| `test_survives_a_power_cycle` | `client.power.cycle()`, wait for ssh, deployment intact and boot still within budget |

The last two are the ones an ordinary test framework cannot do, because they
need control over the hardware rather than just a shell on it. Boot time is a
hard requirement on automotive targets, and a board only shows marginal
power, cooling or memory once it is saturated.

`test_survives_a_power_cycle` also pins down a real property of this image:
`/var` persists across a boot while `/etc` does not, because ostree keeps
`/etc` on a transient overlay under `/run`. That is why the workload is
deployed to `/var/lib/jumpstarter-demo` rather than installed as a systemd
unit in `/etc`, which would silently vanish on the next boot.

Note that `power.cycle()` cuts the power rather than shutting the board down,
so the test calls `sync` first: anything the mount step left in the page
cache would otherwise be lost.

The lease is always released, including when a step fails or the script is
interrupted.

## Usage

```console
# record docs/source/_static/demo.cast
./demo/board_demo.py

# verify the flow in CI, no recording, typed as fast as possible
./demo/board_demo.py --no-cast --speed 8

# iterate on the script without reflashing (needs an already booted board)
./demo/board_demo.py --no-flash --no-cast --speed 8
```

Play a recording back with:

```console
asciinema play docs/source/_static/demo.cast
```

## What it runs against

The board type, the image and the string used to recognise that image once it
boots belong together, and are configured in one place:

| Setting | Flag | Environment | Default |
| --- | --- | --- | --- |
| board type | `--selector` | `DEMO_SELECTOR` | `board-type=j784s4evm` |
| disk image | `--image` | `DEMO_IMAGE` | `oci://quay.io/jumpstarter-dev/autosd-demo-disk:asciinema` |
| expected OS | `--os-name` | `DEMO_OS_NAME` | `Automotive Stream Distribution` |
| registry credentials | `--registry-creds` | `DEMO_REGISTRY_CREDS` | none, pull anonymously |

A flag beats the environment, which beats the default. Empty environment
variables count as unset, so CI can pass them through unconditionally.

`--registry-creds` points at a JSON file with a `token` field, for an image
the registry will not serve anonymously. The token is put into the recorded
shell's environment and the flash step then types
`j storage flash --bearer "$REGISTRY_TOKEN" oci://…`, so the recording shows
that the pull is authenticated without showing the credential.

The test suite reads three more, which have no flags because only the tests
use them:

| Setting | Environment | Default | Notes |
| --- | --- | --- | --- |
| boot budget | `DEMO_BOOT_BUDGET` | `30` seconds | measured boot on a J784S4 EVM is about 14s |
| soak length | `DEMO_SOAK_SECONDS` | `20` seconds | |
| soak memory | `DEMO_SOAK_VM_BYTES` | `512M` | per `stress-ng` vm worker, of which there are two |

`board_demo.py` passes `DEMO_SELECTOR`, `DEMO_IMAGE` and `DEMO_OS_NAME` into
the recorded shell, so the tests always run against the board and image the
demo just used.

```console
DEMO_IMAGE=oci://quay.io/my-org/my-disk:v3 \
DEMO_OS_NAME="My Distribution" \
  ./demo/board_demo.py
```

In CI the same values come from repository variables `DEMO_SELECTOR`,
`DEMO_IMAGE` and `DEMO_OS_NAME`, overridable per run from the workflow
dispatch form. The workflow holds no copy of them.

Other useful options:

| Option | Purpose |
| --- | --- |
| `--cast` | where to write the recording, default `docs/source/_static/demo.cast` |
| `--duration` | lease duration, default `1h` |
| `--lease-id` | lease name, default `demo-MMDD`; it appears in the recording |
| `--boot-wait` | dead air after a power cycle before the first ssh, default 45s |
| `--boot-timeout` | seconds the board may take to answer after a power cycle, default 900 |
| `--speed` | typing speed multiplier; `1.0` is human pace |
| `--idle-limit` | cap idle gaps in the recording, default 2s |
| `--seed` | RNG seed for keystroke timing, so runs stay comparable |
| `--comment-style` | SGR parameters colouring the narration, default `1;36` (bold cyan) |
| `--cols` / `--rows` | recorded terminal size, default 130x32. The flasher's
  progress bar is about 125 columns wide and wraps below that |
| `--serial` | also watch the board boot on the serial console, see below |
| `--flash-timeout` | seconds allowed for flashing, default 2700 |
| `--test-timeout` | seconds allowed for the pytest run, default 900 |
| `--keep-lease` | leave the board leased after the run, for debugging |

`j storage flash` is occasionally flaky. Across seven recorded runs, five
flashed first time, one needed the driver's own internal retry
(`succeeded on attempt 2`), and one wedged at 94% of the download with no
further output and no timeout of its own. There is no stall detection in the
harness yet, so a wedge costs the whole `--flash-timeout`: the run that hung
burned the full 45 minutes before failing. Passing something like
`--flash-timeout 900` bounds the damage until that is addressed.

## Pacing

The recording is meant to be read while it plays, not skimmed, so it is paced
rather than merely fast:

- commands are typed at about 170 characters a minute, narration slightly
  faster, both with jittered per-keystroke delays;
- every line of narration is then left on screen for roughly 0.045s per
  character, around 110 words per minute, which is slow for reading alone but
  not for reading while also watching a terminal;
- command output is left to settle for a moment before the next step starts;
- idle gaps are otherwise capped at `--idle-limit` (2s), so flashing and
  booting do not become dead air. Deliberate pauses are exempt from that cap,
  via `Asciicast.pause()`.

Everything above scales with `--speed`, so `--speed 8` verification runs do
not spend the time. The docs player plays the cast back at speed 1: the pacing
lives in the recording, so changing it there would undo this.

## Lease names

The lease name shows up on screen, so it is kept short and readable, but it
cannot be a fixed string. Releasing a lease leaves the object behind with its
name still reserved: `jmp create lease --lease-id demo` then fails with
`leases.jumpstarter.dev "demo" already exists`, while `jmp delete lease demo`
refuses with `has already been released`. Only deleting the custom resource
from the cluster frees the name, which a plain client config cannot do.

So the default is the date, `demo-1005`, and the script checks with
`jmp get leases <name>` before recording: if that name is taken, because the
demo already ran today, it tries `demo-1005b`, `demo-1005c`, and so on. Pass
`--lease-id` to choose one yourself.

## The serial console step

The plan for this demo includes showing the boot log on the serial console,
and the harness supports it (`HumanShell.watch()` runs a streaming command,
waits for a pattern, then interrupts it). It is off by default because
`j serial pipe` returns a single NUL byte and then nothing on the TI Jacinto
board, across a whole boot.

The port itself is fine: reading `client.serial` directly through the pexpect
adapter over the same power cycle captures the full log, from `U-Boot SPL` and
`BL31` through to `Model: Texas Instruments J784S4 EVM`. So this looks like a
bug in `j serial pipe` rather than in the exporter or the wiring. Once that is
fixed, `./demo/board_demo.py --serial` puts the step back into the recording,
with `--boot-marker` setting the regex that ends the wait.

Until then the demo waits for the board by retrying `j ssh` until it answers,
which is also what someone would do by hand.

## Requirements

- A client config that can reach the controller (`jmp get exporters` works).
- `sshfs` and `fusermount3`, for the `j mount` step.
- `pytest` and `jumpstarter-testing`, for the test step. The demo finds them
  next to `jmp` if they are not on `PATH`.
- Python 3.12+, which is what the Jumpstarter packages require. The recording
  harness itself only uses the standard library.

## How it works

`human_shell.py` runs bash in a pty and types into it one character at a time,
with jittered delays drawn from a seeded RNG. Output is mirrored to stdout and
written to an asciicast file as it arrives.

The outer shell's `PS1` carries invisible
[OSC 133](https://gitlab.freedesktop.org/Per_Bothner/specifications/blob/master/proposals/semantic-prompts.md)
semantic prompt markers, which lets the harness tell prompts apart from command
output and read every command's exit code without printing anything extra.
Nested shells such as `jmp shell` and `j mount` set their own prompts, so
`enter()` / `leave()` switch prompt detection for the duration.

The narration is typed as ordinary shell comments, which bash ignores and
which leave `$?` alone, so it never disturbs the exit-code checking. Its
colour is written into the recording rather than typed: `ESC` is readline's
meta prefix, so an escape sequence sent as input would trigger key bindings
instead of styling the text.

Steps assert with `expect` / `refute` regexes matched against the output with
escape sequences stripped, which is what makes the recording a real test:
if the flashed image does not boot, or a test fails, the script
fails instead of recording a broken demo.

## Other recordings

`demo/casts/caib.cast` is unrelated to this script. It shows how the disk
image used by the demo is built.
