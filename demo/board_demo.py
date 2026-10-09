#!/usr/bin/env python3
"""Record and verify the Jumpstarter board demo.

The script walks a real board through the whole workflow:

  1. list the exporters of a board type
  2. list owned leases, then lease a board
  3. flash a disk image onto it
  4. power cycle it
  5. verify over ssh that the expected OS came up
  6. mount the board's filesystem and deploy a workload onto it
  7. run a pytest suite that drives the board through the same lease

Every command is real and every step asserts on what it printed, so the same
run both produces `docs/source/_static/demo.cast`, which the documentation
landing page plays, and serves as a lab smoke test.
The lease is always released, including when a step fails.

    ./demo/board_demo.py                       # record the cast
    ./demo/board_demo.py --no-cast --speed 8   # verify only, as fast as possible
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import string
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from human_shell import (
    JMP_PROMPT,
    MOUNT_PROMPT,
    Asciicast,
    DemoFailure,
    HumanShell,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The landing page plays this file, so record straight into it.
DEFAULT_CAST = os.path.join(REPO_ROOT, "docs", "source", "_static", "demo.cast")

WORKLOAD = "demo/workload"
TESTS = "demo/tests"
MOUNTPOINT = "/tmp/dut"
REMOTE_PATH = "/"
STATE = "var/lib/jumpstarter-demo"
#: Shown before the suite runs, so a viewer who wants the detail can go and
#: read it rather than trying to infer it from the pytest output scrolling by.
TESTS_URL = "https://github.com/jumpstarter-dev/jumpstarter/blob/main/demo/tests/test_board.py"

def find_pytest():
    """Find a pytest that can import the Jumpstarter test helpers.

    The pytest on PATH is usually the system one, which cannot: the packages
    live in the venv that also provides `jmp`, and the installer only
    symlinks `jmp` and `j` out of it. So look next to `jmp` first, and only
    accept a candidate whose interpreter can actually import what the tests
    need.
    """
    directories = []
    jmp = shutil.which("jmp")
    if jmp:
        directories.append(os.path.dirname(os.path.realpath(jmp)))
    on_path = shutil.which("pytest")
    if on_path:
        directories.append(os.path.dirname(on_path))

    for directory in directories:
        pytest_bin = os.path.join(directory, "pytest")
        python_bin = os.path.join(directory, "python3")
        if not (os.access(pytest_bin, os.X_OK) and os.access(python_bin, os.X_OK)):
            continue
        probe = subprocess.run(
            [python_bin, "-c", "import jumpstarter_testing, jumpstarter_driver_ssh"],
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return pytest_bin
    return None


def from_env(name, fallback):
    """Read a DEMO_* override, treating an empty value as unset.

    CI passes these through unconditionally, so they arrive as empty strings
    when nothing is configured.
    """
    return os.environ.get(name, "").strip() or fallback


# What the demo runs against. These belong together: the image has to boot on
# the board type, and os_name has to match what that image reports. Override
# on the command line, or with DEMO_* in the environment so that CI and the
# Makefile do not have to repeat the values.
DEFAULT_SELECTOR = from_env("DEMO_SELECTOR", "board-type=j784s4evm")
DEFAULT_IMAGE = from_env(
    "DEMO_IMAGE", "oci://quay.io/jumpstarter-dev/autosd-demo-disk:asciinema"
)
DEFAULT_OS_NAME = from_env("DEMO_OS_NAME", "Automotive Stream Distribution")
# A JSON file with a "token" field, for registries that do not serve the image
# anonymously. Nothing of it reaches the recording: see registry_token().
DEFAULT_REGISTRY_CREDS = from_env("DEMO_REGISTRY_CREDS", "")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cast", default=DEFAULT_CAST, help="asciicast output path")
    parser.add_argument(
        "--no-cast", action="store_true", help="verify only, do not write a recording"
    )
    parser.add_argument(
        "--selector",
        default=DEFAULT_SELECTOR,
        help="label selector picking the board type to lease, $DEMO_SELECTOR"
        f" (default: {DEFAULT_SELECTOR})",
    )
    parser.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help=f"disk image to flash, $DEMO_IMAGE (default: {DEFAULT_IMAGE})",
    )
    parser.add_argument(
        "--registry-creds",
        default=DEFAULT_REGISTRY_CREDS,
        help="JSON file holding a {\"token\": ...} for a registry that needs"
        " authentication to serve --image, $DEMO_REGISTRY_CREDS. The token"
        " itself never appears in the recording",
    )
    parser.add_argument("--duration", default="1h", help="lease duration")
    parser.add_argument(
        "--lease-id",
        default=None,
        help="name for the lease. Defaults to demo-MMDD, with a letter added"
        " if that name is already taken: a released lease keeps its name"
        " reserved, so the same one cannot be used twice",
    )
    parser.add_argument(
        "--os-name",
        default=DEFAULT_OS_NAME,
        help="string expected in /etc/os-release once the image has booted,"
        f" $DEMO_OS_NAME (default: {DEFAULT_OS_NAME})",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="watch the board boot on the serial console instead of simply"
        " waiting out --boot-wait. Off by default: it is useful when a boot"
        " needs debugging, but it puts a long unscripted log in the recording",
    )
    parser.add_argument(
        "--boot-marker",
        default=r"login:|[Ss]tartup finished|Reached target.*[Mm]ulti-[Uu]ser",
        help="regex marking the end of boot on the serial console, with --serial",
    )
    parser.add_argument("--cols", type=int, default=130)
    parser.add_argument("--rows", type=int, default=32)
    parser.add_argument(
        "--speed", type=float, default=1.0, help="typing speed multiplier"
    )
    parser.add_argument(
        "--idle-limit",
        type=float,
        default=2.0,
        help="cap idle gaps in the recording, in seconds",
    )
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument(
        "--comment-style",
        default="1;36",
        help="SGR parameters used to colour the narration, e.g. 1;36 for bold"
        " cyan, 2;37 for dim grey, 33 for yellow",
    )
    parser.add_argument(
        "--flash-timeout", type=int, default=2700, help="seconds allowed for flashing"
    )
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=900,
        help="seconds allowed for the pytest run, which power cycles the board",
    )
    parser.add_argument(
        "--boot-wait",
        type=int,
        default=45,
        help="seconds to wait after a power cycle before the first ssh, so the"
        " recording does not show a failed attempt",
    )
    parser.add_argument(
        "--boot-timeout",
        type=int,
        default=900,
        help="seconds allowed for the board to come back up after a power cycle",
    )
    parser.add_argument(
        "--no-flash",
        action="store_true",
        help="skip flashing, power cycling and the boot watch, and use whatever"
        " the board is already running (for iterating on the script)",
    )
    parser.add_argument(
        "--keep-lease",
        action="store_true",
        help="do not release the lease when the demo ends",
    )
    return parser.parse_args(argv)


def registry_token(path):
    """Read a registry bearer token out of a JSON credentials file."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as creds:
            token = json.load(creds).get("token")
    except (OSError, ValueError) as err:
        raise SystemExit(f"could not read registry credentials from {path}: {err}") from err
    if not token:
        raise SystemExit(f"{path} has no 'token' field")
    return token


def taken_lease_names():
    """Every lease name this client has used that is still in the cluster.

    `-a` includes expired and released ones, which matters: those keep their
    names reserved. A lease someone else owns is not listed, so a clash with
    one of those can still happen; it just is not worth cluster credentials
    to rule out.
    """
    result = subprocess.run(
        ["jmp", "get", "leases", "-a", "-o", "json"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return set()
    try:
        leases = json.loads(result.stdout)["leases"]
    except (ValueError, KeyError, TypeError):
        return set()
    return {lease.get("name") for lease in leases}


def available_exporter(selector):
    """Name of an online, unleased exporter matching `selector`, or None.

    Worth checking before anything else starts: `jmp create lease` only
    creates the Lease object and returns immediately, so a selector that
    matches no free board does not fail there. It fails later, in `jmp shell
    --lease`, which polls for the lease to be scheduled for up to two hours.
    That is a slow way to find out the lab had nothing free.
    """
    result = subprocess.run(
        ["jmp", "get", "exporters", "-l", selector, "--with", "online,leases", "-o", "json"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"`jmp get exporters -l {selector}` failed: {result.stderr.strip()}"
        )
    try:
        exporters = json.loads(result.stdout)["exporters"]
    except (ValueError, KeyError, TypeError) as err:
        raise SystemExit(f"could not parse `jmp get exporters` output: {err}") from err
    for exporter in exporters:
        if exporter.get("online") and not exporter.get("lease"):
            return exporter.get("name")
    return None


def pick_lease_name():
    """A short, readable lease name that is still free.

    The name shows up on screen, so `demo-1005` reads better than four random
    hex characters. It cannot simply be reused though: releasing a lease
    leaves the object behind with its name reserved. So on a day the demo is
    recorded more than once, a letter is added: demo-1005b, demo-1005c.
    """
    stem = f"demo-{time.strftime('%m%d')}"
    taken = taken_lease_names()
    for suffix in ("", *string.ascii_lowercase[1:]):
        if stem + suffix not in taken:
            return stem + suffix
    raise SystemExit(f"every lease name from {stem} to {stem}z is taken")


def release_lease(lease_id, *, quiet=False):
    """Release the lease outside the recorded shell, best effort.

    With quiet, a missing lease is not worth reporting: this is also used to
    clear the way before the run starts.
    """
    try:
        result = subprocess.run(
            ["jmp", "delete", "lease", lease_id],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        if not quiet:
            print(f"warning: could not release lease {lease_id}: {err}", file=sys.stderr)
        return
    if result.returncode != 0 and not quiet:
        print(
            f"warning: could not release lease {lease_id}: {result.stderr.strip()}",
            file=sys.stderr,
        )


def demo(sh, args, lease_id, state=None):
    """The scenario itself. Each `run` both drives and checks a step.

    `state` is how the caller learns whether this run got as far as creating
    the lease, so that teardown only ever releases a lease this run took.
    """

    state = {} if state is None else state

    # These are typed into a real shell, so anything that came from the
    # command line or the environment gets quoted on the way in.
    selector = shlex.quote(args.selector)
    duration = shlex.quote(args.duration)
    image = shlex.quote(args.image)

    sh.marker("list boards")
    sh.comment("what boards of this type does the lab have, and are any free?")
    sh.run(
        f"jmp get exporters -l {selector} --with online,leases",
        expect=[re.escape(args.selector.split("=")[-1]), r"\byes\b"],
        timeout=120,
    )

    sh.marker("lease a board")
    sh.comment("nothing of ours is out on loan right now")
    sh.run("jmp get leases", timeout=120)
    sh.comment("so take one. it is ours until the lease expires or we hand it back")
    sh.run(
        f"jmp create lease -l {selector} --duration {duration}"
        f" --lease-id {lease_id}",
        expect=lease_id,
        timeout=600,
    )
    state["created"] = True
    sh.run("jmp get leases", expect=lease_id, timeout=120)

    sh.comment("drop into a shell wired to that board. j now talks to the hardware")
    sh.enter(f"jmp shell --lease {lease_id}", prompt=JMP_PROMPT, timeout=300)
    sh.comment("j is the board. each subcommand is an interface it exposes")
    sh.run("j --help", expect=[r"\bstorage\b", r"\bpower\b", r"\bssh\b"], timeout=60)

    if not args.no_flash:
        sh.marker("flash image")
        sh.comment("write a disk image onto the board, straight from a registry")
        # The token is passed as a shell variable rather than a literal, so a
        # private registry does not put a credential in the recording. It is
        # in the shell's environment, which nothing here prints.
        auth = ' --bearer "$REGISTRY_TOKEN"' if args.registry_creds else ""
        sh.run(
            f"j storage flash{auth} {image}",
            timeout=args.flash_timeout,
            refute=r"(?i)\btraceback\b",
        )

        sh.marker("power cycle")
        sh.comment("pull the power and bring it back on the new image")
        sh.run("j power cycle", timeout=180)

        if args.serial:
            sh.marker("watch it boot")
            sh.watch(
                "j serial pipe",
                until=args.boot_marker,
                timeout=args.boot_timeout,
            )

    # Give the board time to boot before asking it anything. This is dead
    # air rather than a typed command, so the recording shows one clean ssh
    # instead of a failed attempt and a retry; the idle gap is compressed on
    # playback. The retries below remain as a safety net for a slow boot.
    if not args.no_flash:
        sh.sleep(args.boot_wait)

    sh.marker("verify the OS")
    sh.comment("ask the board what it is running now")
    # --boot-timeout is a deadline, so spend it as one: keep retrying the ssh
    # until the budget is gone, rather than turning it into an attempt count.
    # Some of it has already gone on --boot-wait above, which is deliberate -
    # both are the same budget for "the board is back".
    sh.run(
        'j ssh -- "cat /etc/os-release; uname -rm"',
        expect=args.os_name,
        timeout=180,
        think=3.0,
        retry_budget=args.boot_timeout,
        retry_delay=15.0,
    )

    sh.marker("check boot time")
    sh.comment("cold boot time is a hard requirement on automotive targets")
    sh.run(
        'j ssh -- "systemd-analyze time"',
        expect="Startup finished",
        timeout=120,
    )

    # Mount the board's filesystem locally and deploy the workload onto it
    # with plain cp, rather than scp or a package.
    sh.marker("copy a program onto the board")
    sh.comment("mount the board's filesystem here, so plain cp can reach it")
    sh.enter(f"j mount {MOUNTPOINT} -r {REMOTE_PATH}", prompt=MOUNT_PROMPT, timeout=180)
    sh.run(f"mkdir -p {MOUNTPOINT}/{STATE}", timeout=60)
    sh.comment("run.sh stands in for an application: it writes a file saying it ran")
    sh.run(f"cp {WORKLOAD}/run.sh {MOUNTPOINT}/{STATE}/", timeout=120)
    sh.run(f"ls -l {MOUNTPOINT}/{STATE}/", expect=r"^-rwx.*run\.sh", timeout=60)
    sh.comment("leaving the subshell unmounts it again")
    sh.leave()

    # The same lease, now driving the Python driver API instead of the CLI.
    sh.marker("test it with pytest")
    sh.comment("same board, same lease, now driven from python instead of the CLI")
    # The link comes before the run, not after it: this is the point where a
    # viewer wants to know what is about to happen to the board, and the
    # pytest output alone does not tell them.
    sh.comment("these are the tests, if you want to read along:")
    sh.comment(TESTS_URL, dwell=6.0)
    sh.comment("they check the right image booted, and booted fast enough,")
    sh.comment("then load every cpu core, then cut the power for real")
    sh.comment("and check the board comes back with our file still there")
    # The soak test skips itself when the image has no stress-ng, so accept
    # that outcome too rather than failing the recording over a missing tool.
    # --no-header keeps the recording free of the absolute paths pytest
    # otherwise prints: the rootdir of whoever recorded it, and the
    # interpreter inside their Jumpstarter venv.
    sh.run(
        f"pytest {TESTS} -v --no-header",
        expect=r"7 passed|6 passed, 1 skipped",
        refute=r"\b(failed|error)\b",
        timeout=args.test_timeout,
    )
    # What the suite covers was said before the run, so do not repeat it here;
    # land the point that none of it was simulated.
    sh.comment("every one of those ran against the board on the bench")

    sh.leave()


def check_prerequisites(args):
    """Fail fast on anything that would doom the run, before it starts.

    Each of these would otherwise be discovered minutes into a recording:
    a missing workload script when it is deployed, a missing pytest when the
    suite is typed, and a busy lab only once `jmp shell --lease` is already
    two hours into polling for a lease to be scheduled. Returns the pytest
    binary to put on the recorded shell's PATH.
    """
    if not os.access(f"{WORKLOAD}/run.sh", os.X_OK):
        raise SystemExit(f"{WORKLOAD}/run.sh must exist and be executable")

    # The demo types a bare `pytest`, and the one that can import
    # jumpstarter_testing usually lives in the Jumpstarter venv rather than on
    # PATH. Find it up front and make it resolvable in the recorded shell.
    pytest_bin = find_pytest()
    if pytest_bin is None:
        raise SystemExit(
            "no pytest able to import jumpstarter_testing was found; install"
            " pytest and jumpstarter-testing into the Jumpstarter venv"
        )

    if available_exporter(args.selector) is None:
        raise SystemExit(
            f"no online, unleased exporter matches {args.selector!r}; nothing"
            " in the lab is free to lease right now"
        )

    return pytest_bin


def main(argv=None):
    args = parse_args(argv)
    os.chdir(REPO_ROOT)

    pytest_bin = check_prerequisites(args)

    shell_env = {
        "PATH": os.pathsep.join([os.path.dirname(pytest_bin), os.environ["PATH"]]),
        # Keep the tests pointed at the same board and image as the demo.
        "DEMO_SELECTOR": args.selector,
        # With --no-flash nothing was written to the board, so there is no
        # image to hold it against: leave this unset and let
        # test_booted_the_image_we_flashed skip rather than assert against
        # whatever the board happened to be running already.
        "DEMO_IMAGE": "" if args.no_flash else args.image,
        "DEMO_OS_NAME": args.os_name,
    }

    token = registry_token(args.registry_creds)
    if token:
        shell_env["REGISTRY_TOKEN"] = token

    lease_id = args.lease_id or pick_lease_name()

    cast = None
    if not args.no_cast:
        os.makedirs(os.path.dirname(args.cast), exist_ok=True)
        cast = Asciicast(
            args.cast,
            cols=args.cols,
            rows=args.rows,
            idle_limit=args.idle_limit,
            title="Jumpstarter: lease, flash and test a board",
        )

    # Only a lease this run created is ours to clean up: with --lease-id the
    # lease may well have been someone else's before the run started.
    state = {"created": False}
    released = False
    status = 0
    sh = HumanShell(
        cast=cast,
        cols=args.cols,
        rows=args.rows,
        seed=args.seed,
        speed=args.speed,
        env=shell_env,
        comment_style=f"\033[{args.comment_style}m",
    )
    try:
        demo(sh, args, lease_id, state)
        if not args.keep_lease:
            sh.marker("release the board")
            sh.comment("hand the board back so the next person can have it")
            sh.run(f"jmp delete lease {lease_id}", timeout=120)
            released = True
        # Handing the board back is the end of the story, so it is also the
        # end of the recording. The link to the tests is shown earlier, where
        # it is still useful. The player loops, so leave a beat on the last
        # frame rather than cutting straight back to the start.
        sh.hold(4.0)
    except DemoFailure as err:
        print(f"\ndemo failed: {err}", file=sys.stderr)
        status = 1
    finally:
        sh.close()
        if cast is not None:
            cast.close()
        if state["created"] and not released and not args.keep_lease:
            release_lease(lease_id)

    if cast is not None and status == 0:
        print(
            f"\nrecorded {args.cast} ({cast.duration:.0f}s)\n"
            f"play it with: asciinema play {args.cast}",
            file=sys.stderr,
        )
    return status


if __name__ == "__main__":
    sys.exit(main())
