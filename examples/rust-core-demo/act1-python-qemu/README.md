# Act 1 — "Nothing changed": Python pytest against a QEMU DUT

**Story:** A longtime Jumpstarter user runs an ordinary `pytest` against a virtual DUT. Power it
on, log in over the serial console, run a command. It looks exactly like Jumpstarter always has —
because the public API is unchanged. What they don't see: the lease and the entire transport now
run through the **Rust core**, and (revealed in Act 4) a **Rust controller**.

Everything is Python here: the QEMU driver, its `power`/`console` children, and the test.

## Prereqs

- `cluster/up.sh` has been run (controller up, `demo-client` + `demo-qemu` created).
- The demo venv is active: `source python/.venv/bin/activate` (gives you `jmp`, `j`, `pytest`,
  and all `jumpstarter-*` packages, including `jumpstarter-driver-qemu`).
- QEMU boot assets fetched:

  ```bash
  bash examples/rust-core-demo/act1-python-qemu/fetch-image.sh
  ```

  Downloads a small aarch64 cloud image + UEFI firmware into `act1-python-qemu/assets/`
  (gitignored). Re-run with `--reset` for a fresh VM between takes. QEMU runs under **TCG**
  (software) on macOS, so first boot takes a minute or two — keep the image small.

## Run

Two terminals. The test itself is right here — `test_qemu_boot.py` — and the scripts show
exactly what runs:

```bash
./serve.sh                 # terminal A: host the QEMU exporter (spawns qemu-system-aarch64)
DEBUG_CONSOLE=1 ./run.sh   # terminal B: lease the DUT + run test_qemu_boot.py
```

`DEBUG_CONSOLE=1` mirrors the guest's serial output live — great for the audience to watch the
kernel boot and the login happen. Extra `run.sh` args go to pytest.

Equivalent by hand (venv active, from the repo root):

```bash
JMP_DRIVERS_ALLOW=UNSAFE jmp run --exporter demo-qemu                     # terminal A
jmp shell --client demo-client --selector example.com/dut=qemu -- \
    pytest -s examples/rust-core-demo/act1-python-qemu/test_qemu_boot.py  # terminal B
```

## What to say

- "This is a stock Jumpstarter test — `JumpstarterTest`, a label selector, `client.dut.power.on()`,
  a pexpect console. Nothing about it is new."
- "The exporter, the drivers, the client API: all the same. The only thing that changed is that
  the lease and the byte transport underneath are now Rust." (Save the controller reveal for Act 4.)

## Notes / gotchas

- `arch: aarch64` is set explicitly in `exporter.yaml` — on Apple Silicon `platform.machine()`
  is `arm64`, which would build a nonexistent `qemu-system-arm64`.
- `QemuPower.read()` raises `NotImplementedError`; this test only uses `power.on()/off()` + console.
- If no `login:` appears, the image may not put a getty on the virt serial port — try another
  cloud image via `DEMO_IMAGE_URL=... bash fetch-image.sh` (delete `assets/base.qcow2` first).
- vsock/ssh child is Linux-only, so `client.dut.ssh` is absent on macOS; the console is the way in.
