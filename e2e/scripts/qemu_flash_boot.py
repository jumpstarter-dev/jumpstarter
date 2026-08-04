#!/usr/bin/env python3
# Copyright 2026 The Jumpstarter Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Flash a guest image to a leased ExporterSet QEMU target and wait for boot.

Intended to run under `jmp shell`, which sets JUMPSTARTER_HOST so env() works:

    jmp shell --client … --selector board=… -- \\
        python3 e2e/scripts/qemu_flash_boot.py /path/to/image.qcow2

Verification is serial-console based (Alpine boot markers). SSH/vsock are
intentionally not required for this minimal smoke path.
"""

from __future__ import annotations

import argparse
import os
import sys

import pexpect
from jumpstarter_driver_network.adapters import PexpectAdapter

from jumpstarter.utils.env import env

DEFAULT_BOOT_MARKERS = (
    "login:",
    "Welcome to Alpine",
    "Alpine Linux",
    "localhost login:",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Path or URL to the guest disk image to flash")
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("JUMPSTARTER_E2E_CONSOLE_TIMEOUT", "900")),
        help="Seconds to wait for a console boot marker (default: 900)",
    )
    parser.add_argument(
        "--disk-size",
        default=os.environ.get("JUMPSTARTER_E2E_DISK_SIZE", "2G"),
        help="Disk size passed to qemu.set_disk_size before power on",
    )
    parser.add_argument(
        "--skip-flash",
        action="store_true",
        help="Skip flashing (use an already-written root disk)",
    )
    args = parser.parse_args()

    markers = list(DEFAULT_BOOT_MARKERS)

    with env() as client:
        qemu = client.qemu

        if not args.skip_flash:
            print(f"flashing {args.image!r}...", flush=True)
            qemu.flasher.flash(args.image)
            print("flash complete", flush=True)

        if args.disk_size:
            print(f"set_disk_size({args.disk_size!r})", flush=True)
            qemu.set_disk_size(args.disk_size)

        print("power on...", flush=True)
        qemu.power.on()

        print(f"waiting up to {args.timeout}s for console markers {markers!r}...", flush=True)
        try:
            with PexpectAdapter(client=qemu.console) as p:
                p.logfile = sys.stdout.buffer
                idx = p.expect(markers + [pexpect.TIMEOUT, pexpect.EOF], timeout=args.timeout)
                if idx >= len(markers):
                    print("FAILED: timed out / EOF waiting for Alpine boot marker", flush=True)
                    return 1
                print(f"OK: matched marker {markers[idx]!r}", flush=True)
        finally:
            # Always power off (success and failure). Failures here propagate so
            # a broken success-path power.off is not silently ignored.
            print("power off...", flush=True)
            qemu.power.off()

    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
