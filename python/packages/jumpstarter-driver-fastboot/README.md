# Fastboot Driver

`jumpstarter-driver-fastboot` flashes any device whose bootloader speaks the
Android fastboot protocol: phones, tablets, automotive head units, and U-Boot
boards with `fastboot usb`, over USB or [over TCP](#fastboot-over-tcp) (userspace
`fastbootd` on a network).

A flash is a job on the exporter, not a call: a bundle is staged on the
exporter first, then committed by a job that is journaled, interlocked, and
independent of the client and of the lease. Bundles are `FlashManifest`s, and
the driver adds what is specific to fastboot: finding the device on its bench
port, `getvar`, slots, partition checks, and the critical-partition policy.

The driver is built so that a flash, once it starts writing, finishes:

- **Images are staged on the exporter first.** OCI bundles are pulled by the
  exporter and local files are uploaded, then every blob is digest-verified,
  decompressed, and checked (sparse headers, sizes, free space) before the
  device is touched. The network is never in the write path.
- **The write runs as a task on the exporter.** It is journaled step by step
  and keeps going if the client disconnects or the lease ends. If it is cut
  short anyway (the exporter crashes or stops, host power loss), the device is
  left in fastboot and the next use of the driver resumes the flash from the
  first unfinished step.
- **Power is interlocked during a flash.** While a job is writing, power
  drivers (NOYITO, gpiod, Tasmota, Ykush, …) refuse to switch off, whoever
  asks: the lease holder, an agent, or a lease hook. Session start/end
  power-offs are held back too. By default this covers every power driver in
  the exporter; on an exporter that powers several devices, list the flashed
  device's power so the others stay usable.
- **A running flash blocks the end of the lease and future leases.** No
  configuration needed (see [Lease end](#lease-end)).
- **The runner never touches power.** Failed steps are retried without
  rebooting, a device that drops off USB is waited for, and on unrecoverable
  failure the device is left in fastboot rather than booted half-written.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-fastboot
```

The exporter host needs:

- `fastboot` from Android platform-tools (Fedora: `android-tools`), and `adb` if
  an entry strategy uses it.
- USB access to the device in bootloader **and** fastbootd modes. In a
  container, the device re-enumerates between modes, so pass `/dev` through
  (for example `--privileged -v /dev:/dev -v /run/udev:/run/udev:ro`). Not
  needed for a device addressed [over TCP](#fastboot-over-tcp), which needs
  network access to it instead.
- A persistent `state_dir` (default `/var/lib/jumpstarter/fastboot`). In a
  container it **must** be a host-mounted volume, or jobs can't be resumed after
  a container restart.

## Configuration

The device is pinned the same way as the [ADB driver](adb.md) pins one: by
exactly one of `usb_port`, the bench USB port (preferred), `serial`, or
`address` for a device on [TCP](#fastboot-over-tcp).
`usb_port` is the `usb:` field that `fastboot devices -l` (or `adb devices -l`;
it is the same port) prints, with or without the `usb:` prefix. On macOS, copy
it verbatim, `X` suffix included. The device's serial is looked up from the
port before every command, and commands are addressed with `-s SERIAL`. Because
identity is the bench port, swapping hardware needs no config change. Nothing
ever uses "the first device found".

### Fastboot over TCP

A device whose fastboot listens on TCP, such as userspace `fastbootd` reachable
over a network, is pinned with `address` instead: `host`, `host:port`, or `[ipv6]:port`, with or without a
`tcp:` prefix. The port defaults to 5554, fastboot's own. Commands go out as
`fastboot -s tcp:HOST:PORT …`.

```yaml
export:
  fastboot:
    type: jumpstarter_driver_fastboot.driver.FastbootFlasher
    config:
      address: "192.0.2.20"      # or "192.0.2.20:5554"
```

`fastboot devices` never lists a network device, so the driver asks the device
itself whether it is in fastboot: a `getvar version` that something speaking the
fastboot protocol has to answer, bounded by `probe_timeout`. It does not just
try to connect, so a forwarded port with nothing behind it is correctly "not
present":

```yaml
      # adb forward tcp:15554 tcp:5554   (run by an entry script, or on the bench host)
      address: "127.0.0.1:15554"
```

Everything else works as it does over USB: staging, journaled jobs, the
interlocks, the per-step `serialno` check (a different device answering at the
same address aborts the job), and `enter`/`wait-present`. Entry scripts and the
exit script get `FASTBOOT_ADDRESS`, with `FASTBOOT_USB_PORT` and
`FASTBOOT_SERIAL` empty.

- **The address must survive mode switches.** `reboot: fastboot` and
  `reboot: bootloader` steps (and `fastboot reboot` itself) drop the connection;
  the runner waits up to `reenumerate_timeout` for the device to answer at the
  *same* address again. If the device comes back elsewhere, the job ends
  `interrupted`, exactly as a device that never re-enumerates on USB does.
- **Fastboot over TCP has no authentication.** Anyone who can reach the port can
  flash the device. Keep it on a bench network, loopback, or behind a forward.
- **TCP only.** `udp:` addresses are refused.

### Entry strategies

How a device gets into fastboot differs on every bench: adb, a button held
while power is applied, a command on a serial console, a vendor controller. So
**entry strategies are `j` scripts**, Bash or Python, run on the exporter like
lease hooks. A script can reach every driver in the exporter with
`j <path> <command>` (or `env()` in Python), using the paths from the exporter
config. Strategies are tried in order.

How to reach fastboot belongs to the bench, so it lives here, in the exporter
config, and never in a bundle: the same bundle flashes a phone entered with
adb and a head unit entered with a relay.

```yaml
      entry:
        - name: adb
          script: adb -s "$FASTBOOT_USB_PORT" reboot bootloader
          env: { ANDROID_ADB_SERVER_PORT: "15037" }   # the exporter's AdbServer
          wait: 30
        - name: buttons
          script: |
            set -e
            j $JMP_DRIVER_PATH power off
            sleep 3
            j $JMP_DRIVER_PATH volume_down on
            j $JMP_DRIVER_PATH power on
            j $JMP_DRIVER_PATH wait-present --timeout 30   # hold it until fastboot shows up
          cleanup: j $JMP_DRIVER_PATH volume_down off     # always runs
          timeout: 60
```

| Field | Meaning | Default |
| --- | --- | --- |
| `name` | Used by `enter --strategy NAME` and in errors | required |
| `script` | Inline script, or the path of a script file on the exporter | required |
| `exec` | Interpreter. Without it, a `.py` file runs with the exporter's Python and anything else with `/bin/sh` | |
| `timeout` | Seconds before the script is terminated (whole seconds, as for hooks) | `120` |
| `wait` | After the script exits successfully, seconds to wait for the device to appear in fastboot | `60` |
| `cleanup` | A script that **always** runs after the strategy (success, failure, or timeout), e.g. to release buttons. Inline text (30 s timeout) or a full script config | none |
| `env` | Extra environment variables | none |
| `block_self` | Refuse calls to the flasher while its script runs, except `wait-present`, `getvar`, `stages`, `status`, `jobs` | `true` |
| `self_path` | The flasher's `j` path, if it can't be found in the exporter tree | found |

The script's environment:

| Variable | Value |
| --- | --- |
| `JMP_DRIVER_PATH` | The flasher's own `j` path, e.g. `bench dut1`. `j $JMP_DRIVER_PATH <child> …` reaches its children, so a script works unchanged on every bench and on multi-device exporters. |
| `FASTBOOT_USB_PORT`, `FASTBOOT_SERIAL`, `FASTBOOT_ADDRESS` | The device pinning from the flasher's config (one is set, the others empty). `FASTBOOT_USB_PORT` includes the `usb:` prefix, which `adb -s` and `fastboot -s` both accept; `FASTBOOT_ADDRESS` is the normalized `tcp:HOST:PORT` selector. |
| `JUMPSTARTER_HOST`, `JMP_DRIVERS_ALLOW` | How `j` and `env()` reach the drivers (set for you). |

- **Hold until something happens.** `j $JMP_DRIVER_PATH wait-present --timeout N`
  waits for the device to appear in fastboot and exits 1 if it doesn't, so a
  script can keep a button held exactly as long as needed. A Python script can
  wait on anything else it can observe (a console line, a screen capture).
- **Always release what you hold, in `cleanup`.** A timed-out script is
  terminated before it can clean up after itself; `cleanup` runs regardless.
- **Failure and timeout:** a non-zero exit or a timeout fails the strategy and
  the next one is tried. The script's output is logged on the exporter, and its
  last lines are included in the error. Use `set -e` in shell scripts.
- **No re-entry:** a strategy can't call `enter`, `start`, or `reboot` on its
  own flasher (that would deadlock).
- **Safety:** entry runs before anything is written, so the power interlock
  doesn't apply yet. While a flash job is running, entry is refused. The
  script serves the exporter's live drivers on a private socket for its
  duration only; it doesn't reset or close any driver.
- **Children.** Declare the power, buttons and consoles a strategy uses as
  children of the flasher (directly or with `ref:`). They are then reachable
  as `j $JMP_DRIVER_PATH <child>` and protected during a flash
  ([Power interlock](#power-interlock)).

### Example: Android phone, adb with a button fallback

```yaml
export:
  fastboot:
    type: jumpstarter_driver_fastboot.driver.FastbootFlasher
    config:
      usb_port: "1-4.2"              # the bench USB port, as `fastboot devices -l` reports it
      entry:
        - name: adb
          script: adb -s "$FASTBOOT_USB_PORT" reboot bootloader
          env: { ANDROID_ADB_SERVER_PORT: "15037" }
          wait: 30
        - name: buttons
          script: |
            set -e
            j $JMP_DRIVER_PATH volume_down on
            j $JMP_DRIVER_PATH power_key on; sleep 15; j $JMP_DRIVER_PATH power_key off
            j $JMP_DRIVER_PATH wait-present --timeout 30
          cleanup: |
            j $JMP_DRIVER_PATH power_key off
            j $JMP_DRIVER_PATH volume_down off
          timeout: 90
    children:
      power_key: { ref: relay_power }
      volume_down: { ref: relay_voldown }
  adb:
    type: jumpstarter_driver_adb.driver.AdbServer
  usb_hub:                           # the phone's USB data and charge; interlocked while flashing
    type: jumpstarter_driver_yepkit.driver.Ykush
    config: { serial: "YK112233", port: "1", default: "on" }
  relay_power:
    type: jumpstarter_driver_gpiod.driver.DigitalOutput
    config: { device: /dev/gpiochip0, line: 17 }
  relay_voldown:
    type: jumpstarter_driver_gpiod.driver.DigitalOutput
    config: { device: /dev/gpiochip0, line: 27 }
```

### Example: U-Boot board, serial console

```yaml
      entry:
        - name: uboot
          exec: python3
          script: |
            import os
            from functools import reduce
            from jumpstarter.utils.env import env

            with env() as client:
                dut = reduce(getattr, os.environ["JMP_DRIVER_PATH"].split(), client)
                with dut.console.pexpect() as console:
                    dut.power.cycle()
                    console.expect("Hit any key to stop autoboot", timeout=60)
                    console.send("\n")
                    console.expect("=> ")
                    console.sendline("fastboot usb 0")
          wait: 30
```

### Example: RideSX4, TAC serial controller

A command/acknowledge controller is a short Python loop. This is the SA8775P
fastboot sequence from the RideSX driver:

```yaml
      entry:
        - name: tac
          exec: python3
          script: /opt/jumpstarter/ridesx_fastboot.py
          wait: 30
```

```python
# /opt/jumpstarter/ridesx_fastboot.py
import os
import time
from functools import reduce

from jumpstarter.utils.env import env

SEQUENCE = [
    ("devicePower 0", 0), ("usbDevicePower 1", 0), ("gpio vbusdis1 0", 0),
    ("ttl outputBit 1 0", 0), ("gpio volup 0", 0), ("ttl outputBit 2 1", 0), ("ttl outputBit 4 0", 0.5),
    ("devicePower 1", 0.9), ("usbDevicePower 1", 0), ("gpio vbusdis1 0", 0.03),
    ("ttl outputBit 1 1", 0.8), ("ttl outputBit 1 0", 8),
    ("ttl outputBit 2 0", 0.5),
]

with env() as client:
    flasher = reduce(getattr, os.environ["JMP_DRIVER_PATH"].split(), client)
    with flasher.tac.pexpect() as tac:
        for command, delay in SEQUENCE:
            tac.send(command + "\r")
            tac.expect("ok", timeout=10)
            tac.expect("CMD >> ", timeout=10)
            time.sleep(delay)
```

### Exit script

A bundle's `spec.fastboot.finally` (`reboot`, `continue`, or `stay`) is a fastboot command,
which is all most phones need. Some devices need more to boot normally after a
flash: release a boot-mode strap and power-cycle (RideSX), flip boot switches
(TI AM62x), or type `boot` at the U-Boot prompt (where `fastboot continue` only
returns to the prompt). For those, configure an **exit script**: a `j` script,
like an entry strategy, that runs once the job finishes.

```yaml
      exit:
        script: |
          set -e
          j $JMP_DRIVER_PATH power off
          sleep 2
          j $JMP_DRIVER_PATH power on      # boot straps released: a normal boot
        timeout: 60
        when: success                      # or: always (after failed jobs too)
```

**It runs even if the client is gone.** The script is the last part of the
flash task, so it runs whether or not the lease is still active: a lease that
ends mid-flash waits for both the flash and the exit script, keeping its
session (and so every driver) up for them ([Lease end](#lease-end)). By the
time it runs the job has finished, so the power interlock no longer applies and
the script can switch power.

`flash` (and `j fastboot flash`) returns once the exit script has run, and
fails if it failed. `wait-idle` waits for it too. The job's `status` shows it as
`exit: {state: waiting | running | succeeded | failed | skipped}`, with the
error and last output lines of a failed script.

The script's environment is that of an entry strategy (`JMP_DRIVER_PATH`,
`FASTBOOT_USB_PORT`, `FASTBOOT_SERIAL`, `FASTBOOT_ADDRESS`, the script's `env`), plus
`FLASH_JOB_ID` and `FLASH_JOB_STATE` (`succeeded`, `failed_partial`, …).
With `when: success` (the default) it runs only after successful jobs, leaving
a device whose flash failed in fastboot for the next attempt. If the flash is
cut short (the exporter crashed), the exit script runs after the resumed flash
finishes.

### Config parameters

| Parameter | Description | Default |
| --- | --- | --- |
| `usb_port` | The bench USB port, as `fastboot devices -l` reports it (preferred) | exactly one of `usb_port`/`serial`/`address` |
| `serial` | An explicit device serial, for hardware with no usable USB devpath | exactly one of `usb_port`/`serial`/`address` |
| `address` | A device whose fastboot listens on TCP: `host[:port]` ([Fastboot over TCP](#fastboot-over-tcp)) | exactly one of `usb_port`/`serial`/`address` |
| `entry` | Entry strategies into fastboot (above): a list, or `{fastboot: [...]}` | `[]` (device must already be in fastboot) |
| `exit` | A script that brings the device out of fastboot after a job, run by the exporter (above) | none |
| `variant` | Board variant used to select manifest entries | none |
| `state_dir` | Stages, blobs, and job journals; must be persistent | `/var/lib/jumpstarter/fastboot` |
| `critical_partitions` | Globs for bootloader-chain partitions | `xbl*`, `abl*`, `tz*`, `hyp*`, `cdt`, … |
| `allow_non_ab_critical` | Allow critical writes where no A/B fallback slot exists | `false` |
| `allow_active_slot_critical` | Allow critical writes to the currently active slot | `false` |
| `allowed_oem_commands` | Exact `oem` commands bundles may run (`oem lock/unlock` are never allowed) | `[]` |
| `step_retries` | Re-attempts per step after a transport failure | `3` |
| `stall_timeout` | Seconds without fastboot output before a step attempt is killed and retried | `300` |
| `reenumerate_timeout` | Seconds to wait for the device to come back (on USB, or at its TCP `address`) | `120` |
| `probe_timeout` | For an `address`: seconds to wait for the device to answer when asking whether it is in fastboot | `5` |
| `max_job_duration` | Hard bound on one runner invocation | `7200` |
| `stage_cache_bytes` | LRU cap for staged content not used by an unfinished job | 20 GiB |
| `free_space_reserve_bytes` | Space kept free in `state_dir` | 1 GiB |
| `fastboot_path` | `fastboot` binary | `fastboot` |
| `oci_insecure` | Pull OCI bundles over plain HTTP | `false` |
| `manifest_name` | Default manifest name inside a bundle | `manifest.yaml` |
| `interlock` | Children refused (except `read`/`status`) while a job is active | every child except `adb` |
| `interlock_power` | Which power drivers to protect: `all`, or a list of exporter driver paths (see [Power interlock](#power-interlock)) | `all` |

### Power interlock

Losing power mid-write is the most common way to brick a device. Pressing a
reset or volume button, or interrupting U-Boot's `fastboot usb` on the console,
is just as bad. While a flash job is active, the flasher protects:

- **the power that feeds the device being flashed:** drivers implementing
  `PowerInterface` or `VirtualPowerInterface`, such as NOYITO relays, gpiod
  `PowerSwitch`, Tasmota, Ykush, EnerGenie, HTTP power, DUT Link, and the
  virtual targets' power (QEMU, Cuttlefish, and so on). Which ones is set by
  `interlock_power` (below).
- **its own children** (buttons, consoles, and the real drivers behind their
  `ref:`s), except `adb`.

`interlock_power` chooses the power drivers:

| Value | Protects |
| --- | --- |
| `all` *(default)* | Every power driver in the exporter. Right for an exporter that powers one device, and safe with no configuration. |
| `["pdu.outlet3", …]` | Exactly these exporter driver paths (dotted, as in `j pdu outlet3`), following `ref:`s along the way. Use it on an exporter that powers several devices, so the others stay usable while one flashes. |

Power drivers are usually exported on their own, not under the flasher. On an
exporter that powers several devices, point each flasher at its device's
outlet:

```yaml
export:
  dut1:
    type: jumpstarter_driver_fastboot.driver.FastbootFlasher
    config:
      usb_port: "1-4.1"
      interlock_power: ["pdu.outlet1"]   # only outlet1 is held while dut1 flashes
  dut2:
    type: jumpstarter_driver_fastboot.driver.FastbootFlasher
    config:
      usb_port: "1-4.2"
      interlock_power: ["pdu.outlet2"]
  pdu:
    type: jumpstarter_driver_composite.driver.Composite
    children:
      outlet1: { type: jumpstarter_driver_noyito_relay.driver.NoyitoPowerSerial, config: { port: /dev/ttyUSB0, channel: 1 } }
      outlet2: { type: jumpstarter_driver_noyito_relay.driver.NoyitoPowerSerial, config: { port: /dev/ttyUSB0, channel: 2 } }
```

A path that doesn't exist in the exporter fails the session with an error, so a
typo never silently leaves a device unprotected. Anything that is a child of
the flasher (a power or button driver its entry scripts use, directly or via
`ref:`) is protected as well, whatever `interlock_power` says.

Protection covers both ways power can be cut:

| Path | While a job is active |
| --- | --- |
| A call such as `j power off`, from the lease holder, an agent, or a lease hook | Refused with `FAILED_PRECONDITION`; only `read` and `status` are allowed |
| `reset()` when a session starts (Ykush `default: off` and Tasmota power off here) | Skipped |
| `close()` when a session ends (Tasmota powers off here) | Deferred until the flash is safe, then run, so cleanup still happens |

```console
$$ j power off
Error: TasmotaPower.off refused: flash job 4c1e0a9b2d17 is writing to the device on usb:1-4.2.
Cutting power, pressing buttons, or using its console mid-flash can brick it. Wait for the job with 'wait-idle'.
```

So nothing, from the lease holder to an agent, can cut power mid-write, and a
lease ending doesn't switch off a Tasmota plug mid-write (an `afterLease` hook
doesn't even run until the flash is done; see [Lease end](#lease-end)).

**Scope is narrow on purpose.** Nothing in the core `Driver` class or any
driver's code changes, and exporters without a fastboot driver are unaffected.
Only the protected driver *instances* in a fastboot exporter are wrapped, and
only while a job is active do the wrappers change anything.

#### Configuring power so it is protected

- **On an exporter that powers several devices, list each device's power** in
  its flasher's `interlock_power`. With the default `all`, every power driver
  in the exporter is held while any device flashes, which is safe but blocks
  the other devices' power in the meantime.
- **Declare anything that switches the DUT's supply as a power driver.** A
  relay channel that feeds the DUT should be a gpiod `PowerSwitch` (which
  implements `PowerInterface`), not a plain `DigitalOutput`, so it is recognized
  as power.
- **Make button relays children of the flasher.** A `DigitalOutput` that
  presses volume-down or reset is protected because it is a child of the
  flasher. Declare it under the flasher's `children` (directly or with `ref:`)
  and use it from entry scripts as `j $JMP_DRIVER_PATH <child>`.
  A button relay exported elsewhere and not referenced isn't protected.
- **Use one driver per piece of hardware.** Two drivers for the same outlet or
  relay channel are protected separately only if both are power drivers in the
  same exporter. Power switched from outside the exporter (a PDU's web UI,
  another exporter on the same PDU) can't be seen.
- **Options:** `interlock_power` scopes power protection (table above);
  `interlock: [...]` names the protected children explicitly.

### Lease end

A lease can end while a flash is running: it expires, is released, or the
client goes away. The exporter keeps the flash safe from the lease lifecycle
until it is done, **with nothing to configure**:

- **The ending lease waits for the flash.** The exporter runs the `afterLease`
  hook and tears down the lease's session only after the flash, and its
  [exit script](#exit-script), have finished. An `afterLease` hook such as
  `j power off` is safe as is, and doesn't need to wait for the flash itself.
  `jmp shell` shows a `draining: fastboot flash job <id> on <port>` status
  while it waits (Ctrl+C stops waiting; the flash goes on).
- **No new lease.** The exporter reports `AFTER_LEASE_HOOK` (`draining: ...`)
  instead of `AVAILABLE` until the flash is done.

The flash is an exporter task (`jumpstarter.driver.tasks.start_task`, the
exporter's generic API for long-running driver work) with `on_lease_end: wait`,
so it is never stopped because a lease ended. Its timeout is set well past
`max_job_duration`, so only a hung flash ever reaches it.

The task runs in the exporter process. If the exporter stops or crashes
mid-flash, the write stops with it, but nothing powers the device off: it
stays in fastboot (a stopping exporter defers its power drivers' `close()`),
and the next use of the driver (`flash`, `status`, `wait-idle`, `enter`)
resumes the job from its journal, skipping the steps already done.

## Bundles

A bundle is a `FlashManifest` at the root of an OCI artifact, a tar archive,
or a local directory, with the files it references (conventionally under
`data/`). Steps and options are namespaced by tool, so the format can grow other
tools without changing what a fastboot bundle looks like. `fastboot` is the only
tool this driver reads; a step for any other is refused when the bundle is
staged:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: FlashManifest
metadata:
  name: headunit-aaos-userdebug
spec:
  requires:                          # checked against getvar before any write
    product: [headunit, headunit_evt]
    unlocked: "yes"
    battery-soc-ok: { equals: "yes", optional: true }
  fastboot:
    slot: inactive                   # current | inactive | a | b | all
    finally: reboot                  # continue | reboot | stay
    android_info: { file: data/android-info.txt }   # the AOSP build's own requirements
  steps:
    - name: Bootloader chain
      critical: true
      fastboot:
        flash:
          - { partition: abl, file: data/abl.elf }
          - { partition: cdt, file: data/cdt-evt1.bin, variant: evt1 }
          - { partition: cdt, file: data/cdt-evt2.bin, variant: evt2 }
    - fastboot:
        flash:
          - { partition: boot, file: data/boot.img }
          - { partition: vbmeta, file: data/vbmeta.img }
    - fastboot: { reboot: fastboot }       # fastbootd, for logical partitions
    - fastboot:
        flash:
          - { partition: system, file: data/system.img }
    - when: wipe                          # only with --wipe
      fastboot: { erase: userdata }
    - fastboot: { reboot: bootloader }
    - fastboot: { set_active: inactive }  # A/B commit point
```

- Steps run in exactly the order written. Nothing is sorted.
- `android_info` points at the `android-info.txt` an AOSP build produces. Its
  lines are checked with `requires`, exactly as `fastboot flashall` checks them:
  `require` and `reject`, `require-for-product:`, `board` meaning `product`,
  `*` prefix matches, and `partition-exists`.
- Common to every step: `requires`, step `name`/`critical`/`when: wipe`,
  `sleep: <seconds>` steps, `file:` references, and `variant:` on entries.
- A `fastboot:` step is one of `flash`, `erase`, `set_active`, `reboot`, or
  `oem`. `flashing lock/unlock` doesn't exist as a step, and `oem lock/unlock`
  is always refused; other `oem` commands must be in `allowed_oem_commands` and
  are never retried.
- Partitions that have slots are resolved to an explicit slot (`boot_b`) from
  `current-slot` before anything is written, and the resolved plan is what gets
  journaled.
- Files may be gzip, xz, zstd, or bz2 compressed; they are decompressed during
  staging.

Push a bundle with ORAS, or pack it as a tar archive:

```console
$ cd bundle/
$ oras push quay.io/acme/headunit-aaos:1234 --artifact-type application/vnd.oci.bundle.v1 \
    manifest.yaml:application/yaml data/*:application/octet-stream
$ tar -C bundle -cJf headunit-aaos-1234.tar.xz .
```

**Annotation-only images** built for `fls fastboot` also work. They carry
`dev.jumpstarter.fls/partition` or `automotive.sdv.cloud.redhat.com/partition`
layer annotations and no manifest. The driver flashes them in
`default-partitions` order (lexical otherwise) to the current slot, then
`continue`s. A synthesized manifest may not write critical partitions;
bootloaders need an explicit manifest.

## Usage

### CLI

```console
$ jmp shell -l board=headunit
# Stage, enter fastboot, preflight, and flash. Ctrl-C detaches; the flash continues.
$$ j fastboot flash oci://quay.io/acme/headunit-aaos:1234
$$ j fastboot flash ./headunit-aaos-1234.tar.xz            # or https://.../headunit-aaos-1234.tar.xz
$$ j fastboot flash ./bundle --wipe                      # a local bundle directory (or its manifest.yaml)
$$ j fastboot flash -t boot:out/boot.img -t vbmeta:out/vbmeta.img

# Stage without touching the device, then flash the stage
$$ j fastboot stage oci://quay.io/acme/headunit-aaos:1235
$$ j fastboot flash --stage 3f9c0d1e2a4b5c6d

$$ j fastboot jobs                 # recent jobs on this device, across leases
$$ j fastboot watch [JOB]          # reattach to a job's progress
$$ j fastboot status [JOB]
$$ j fastboot cancel JOB           # only before the first write
$$ j fastboot resume JOB           # rerun an interrupted job (device back in fastboot)
$$ j fastboot wait-idle
$$ j fastboot enter --strategy buttons
$$ j fastboot wait-present --timeout 30   # exit 1 if the device isn't in fastboot by then
$$ j fastboot getvar current-slot          # exit 1 if no device is in fastboot; empty if it isn't defined
$$ j fastboot reboot bootloader
```

### Python

The client is a `FastbootFlasherClient`:

```{code-block} python
fb = client.fastboot

for status in fb.flash_stream("oci://quay.io/acme/headunit-aaos:1234"):   # FlashStatus updates
    print(status.phase, status.message)

info = fb.flash("./headunit-aaos-1234.tar.xz", wipe=True)       # raises unless the job succeeds
assert info["state"] == "succeeded"

job = fb.flash({"boot": "out/boot.img"}, wait=False)         # detached
for status in fb.watch(job["job_id"]):                       # FlashStatus-shaped dicts
    print(status["message"])

stage_id = fb.stage("bundle/")                                # stage ahead of time
fb.start(stage_id, job_id="ci-run-42")                        # idempotent on job_id
```

## Safety model

| Event | What happens |
| --- | --- |
| Client Ctrl-C, crash, or network loss | Job continues; `watch` reattaches |
| Lease ends or is released mid-flash | The flash continues; the exporter waits for it (`draining`) before the `afterLease` hook and session teardown, and takes no new lease until it finishes |
| Exporter crash, stop or restart; host power loss | The write stops with the exporter; the device stays powered and in fastboot (if it has power); the next use of the driver resumes from the first unfinished step, or reports `interrupted` if the device isn't in fastboot |
| USB glitch / re-enumeration | Runner waits `reenumerate_timeout`, checks `serialno`, retries the step |
| `fastboot` hangs | Killed after `stall_timeout` without output, step retried |
| Different device appears on the port | `serialno` check fails, job stops before the next write |
| Retries exhausted | `failed_partial`; device left in fastboot, `finally` skipped |
| `cancel` after the first write | Refused; the job runs to completion |
| Anyone calls `off` on any power driver, presses a switch, or opens a console mid-flash | Refused (`FAILED_PRECONDITION`) until the job ends |
| Session ends with a flash unfinished (exporter stopping) | Power drivers' `close()` deferred until the flash is safe |

Job results: `succeeded`, `failed_clean` (nothing written), `failed_partial`
(names the failed step and whether a critical step was incomplete),
`interrupted`, and `cancelled`.

Every safety rule in this table is pinned by a test; `safety_test.py` lists
each one next to the test that enforces it.

Killing the host `fastboot` process mid-step is protocol-safe; the device either
discards the partial download or finishes a write it already accepted. Losing
device power mid-write is not, and that is why the runner never touches power.

Only the exporter host can stop a job that has started writing, and only
between steps:

```console
# on the exporter host
$ python -m jumpstarter_driver_fastboot.runner abort /var/lib/jumpstarter/fastboot/jobs/<job_id> --force-unsafe
```

### Limitations

- Power is only protected if it is declared as a power driver in the same
  exporter (see [Configuring power](#configuring-power-so-it-is-protected)).
- A flash runs in the exporter process, so it lasts as long as the exporter
  does. Avoid restarting exporters that report `draining`; one that restarts
  anyway leaves the device in fastboot, and the job resumes the next time the
  driver is used (other calls are refused until it has finished).

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_fastboot.client.FastbootFlasherClient()
    :members: flash, flash_stream, stage, start, watch, status, jobs, cancel, resume, wait_idle, enter, getvar, reboot
```
