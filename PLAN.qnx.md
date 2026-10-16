# QNX + QVM Driver Plan

## Overview

Single package `jumpstarter-driver-qnx` containing two composite drivers:

| Driver | Role |
|--------|------|
| **`Qnx`** | Controls the physical QNX board: hard power (via external child), IPL boot menu interaction, serial console, TCP/SSH into QNX |
| **`Qvm`** | Controls a QNX Virtual Machine running *inside* QNX: storage flash (fls with ssh:// target), power on/off (via serial commands), serial console (same physical UART — transitions from QNX to QVM output) |

## Architecture

QNX and QVM are **sibling top-level drivers**. QVM references QNX via `ref:`
because QVM depends on QNX (not the other way around).

```
Exporter
├── qnx            (Qnx composite — standalone, can be used without QVM)
│   ├── power_ctl  (user-provided: dutlink, yepkit, relay — raw hardware power)
│   ├── power      (QnxPower — auto-created, orchestrates: power_ctl + IPL boot + init_script)
│   ├── serial     (PySerial — physical UART to the board)
│   ├── tcp        (TcpNetwork — QNX IP, port 22)
│   └── ssh        (SSHWrapper — refs tcp)
│
└── qvm            (Qvm composite — depends on qnx)
    ├── qnx        (ref: "qnx" — access to serial, ssh, power)
    ├── power      (QvmPower — auto-created, uses qnx.serial)
    ├── storage    (QvmFlasher — auto-created, runs fls via qnx ssh)
    └── serial     (ref to qnx.serial — same UART)
```

### Key insight: shared serial port

QVM serial IS the QNX serial port. When QVM boots, its output appears on the
same UART. The console transitions from QNX → QVM like U-Boot → Linux.

### Design rationale

- **QNX is self-contained** — usable on boards without QVM
- **QVM depends on QNX** — so QNX is a child (via ref) of QVM, not the other way around
- **Flat exporter layout** — both are top-level, avoids deep nesting
- **Follows existing patterns** — same as SSH referencing TCP via `ref:`

---

## Qnx Driver

### Config

```yaml
type: "jumpstarter_driver_qnx.driver.Qnx"
config:
  # IPL boot menu — what to wait for and what to press
  boot_prompt: "Press 'M' for eSD download"   # default
  boot_key: "M"                                # default
  # Shell prompt after QNX finishes booting
  final_prompt: "#"                            # default
  # Timeouts (seconds)
  boot_timeout: 60                             # wait for boot_prompt after power cycle (default 60)
  prompt_timeout: 120                          # wait for final_prompt after boot_key (default 120)
  # Optional shell scriptlet to run after QNX boot
  # (e.g. inject SSH keys, configure networking)
  init_script: |
    mkdir -p /root/.ssh
    echo "ssh-ed25519 AAAA..." >> /root/.ssh/authorized_keys
```

### Required children (from exporter YAML)

- `power_ctl` — raw hardware power driver (dutlink, yepkit, relay, etc.)
- `serial` — PySerial to the board's UART

### Auto-created children

- `power` → `QnxPower` — orchestrated power interface that wraps `power_ctl` with IPL boot and init script logic

### Optional children (from exporter YAML)

- `tcp` — TcpNetwork to QNX IP (for SSH)
- `ssh` — SSHWrapper (refs tcp)

### QnxPower — orchestrated power interface

`j qnx power on` calls `QnxPower.on()`, which orchestrates the full boot:

**Power on:**
1. Call `power_ctl.cycle()` (external hard power), cycle to ensure known state
2. Watch serial for `boot_prompt` string, press " " to make it re-prompt when we open the serial port
3. Send `boot_key` character
   Note: empty `boot_prompt`/`boot_key` skips steps 2 + 3
4. Wait for `final_prompt` (QNX shell prompt)
5. If `init_script` is set, send it line by line over serial
6. Wait for `final_prompt` again

**Power off:**
1. Call `power_ctl.off()` (external hard power)

**Power cycle:**
1. `power_ctl.off()` → delay → full power on sequence

Users can still access `j qnx power_ctl on/off` for raw hardware power
without the boot orchestration (useful for recovery/debugging).

**Note:** `j qnx serial console` provides raw serial access regardless of
state (no gating). This is intentional — it's the escape hatch for debugging
boot issues. The state-gated console is on the QVM side (`j qvm serial console`).

### IPL boot menu reference

```
Welcome to QNX Neutrino Initial Program Loader for NXP i.MX8QXP CPU Board (ARM Cortex-A35)
Command:
Press 'D' for serial download, using the 'sendnto' utility
Press 'M' for eSD download, IFS filename MUST be 'QNX-IFS'.
Press 'E' for eMMC download, IFS filename MUST be 'QNX-IFS'.
Press 'F' for NOR flash download, IFS file MUST be at 0x100000 offset.
```

Boot prompt and key are fully configurable to support all board variants.

---

## Qvm Driver

### Config

```yaml
type: "jumpstarter_driver_qnx.driver.Qvm"
config:
  emmc_device: "/dev/emmc0"         # default block device for flashing
  ssh_password: "root"              # password for fls ssh:// target (optional)

  # QVM binary and launch configuration
  qvm_path: "/sbin/qvm"                       # path to qvm binary (default "/sbin/qvm")
  working_directory: "/tmp"                    # cwd for launch commands (default "/tmp")
  qvm_config_path: "/tmp/qvm-autosd10.conf"   # absolute path to .conf on QNX filesystem

  # virtio-net MAC address — substituted into the config template as {virtio_net_mac}
  virtio_net_mac: "52:54:00:ab:cd:ef"

  # Optional: U-Boot bootcmd to inject into the QVM guest's U-Boot console.
  # If set, the driver interrupts U-Boot after QVM starts and sends this command.
  # If not set, U-Boot uses its default boot sequence.
  uboot_bootcmd: "bootefi bootmgr ${fdtcontroladdr}"

  # Timeouts (seconds)
  qvm_start_timeout: 30                       # wait for QVM boot confirmation (default 30)
  flash_timeout: 1800                          # fls flash timeout (default 1800 = 30 min)

  # Default QVM config — template with {variable} substitution (Python .format()).
  # Written to qvm_config_path on reset_qvm_config() or when no config
  # exists on the board. Available variables:
  #   {virtio_net_mac}  — from config above
  #   {emmc_device}     — from config above
  qvm_default_config: |
    system uboot-guest
    cpu sched 80
    ram 0x40000000,2024m
    vdev pl011 loc 0x09000000 intr gic:33
    vdev virtio-blk loc 0x0a000000 intr gic:48 hostdev {emmc_device}
    vdev virtio-net loc 0x1c0c0000 intr gic:40 mac {virtio_net_mac} name p2p peer /dev/vdevpeers/vp0 peerfeats 0x0000
    guest load 0x40200000,u-boot-with-prior-stage-dt.bin
```

### SSH connection details

The QvmFlasher derives SSH host/port/user from the referenced `qnx` driver's
children — no duplication needed:

- **host** and **port** from `qnx.tcp.address()` (parsed from the TcpNetwork config)
- **username** from `qnx.ssh.get_default_username()`
- **password** from `ssh_password` config field (optional, for fls ssh:// auth)

### Required children (from exporter YAML)

- `qnx` — ref to the Qnx driver (provides serial, ssh, power)

The driver validates this in `__post_init__` and auto-creates:
- `power` → `QvmPower(qnx=self.children["qnx"])`
- `storage` → `QvmFlasher(qnx=self.children["qnx"])`
- `serial` → ref to `qnx.serial` (same UART)

### QVM config management

The driver exposes methods to inspect and replace the QVM config at runtime:

| Method | Description |
|--------|-------------|
| `get_qvm_config() → str` | Read the current QVM config from the board (via SSH: `cat <qvm_config_path>`) |
| `set_qvm_config(content: str)` | Write a new QVM config to the board (via SSH: write to `<qvm_config_path>`). QVM must be stopped. |
| `reset_qvm_config()` | Restore the config from `qvm_default_config` (if set in driver config). QVM must be stopped. |

**Workflow example — runtime config override:**

```python
from jumpstarter.utils.env import env

with env() as client:
    # Read current config
    current = client.qvm.get_qvm_config()
    print(current)

    # Push a modified config (e.g. change RAM size)
    new_config = current.replace("2024m", "4096m")
    client.qvm.set_qvm_config(new_config)

    # Flash and boot with new config
    client.qvm.storage.flash("oci://quay.io/org/image:latest")
    client.qvm.power.on()
```

**CLI usage:**

```bash
# Dump current config
j qvm config show

# Push a new config from a local file
j qvm config set ./my-qvm.conf

# Reset to the default from the exporter YAML
j qvm config reset
```

### QVM launch mechanism

The driver launches QVM directly via serial console:

1. Writes the rendered config (with `{virtio_net_mac}` and `{emmc_device}` substituted) to `qvm_config_path` on the board via SSH
2. Sends the launch command over `qnx.serial`:
   ```
   cd <working_directory> && <qvm_path> @<qvm_config_path>
   ```
3. QVM runs in the foreground so the serial port stays in control of the QVM console — serial output transitions from QNX shell to QVM I/O

### QVM config file reference

The `.conf` file defines the VM hardware layout:

```
system uboot-guest
cpu sched 80
ram 0x40000000,2024m

vdev pl011 loc 0x09000000 intr gic:33
vdev virtio-blk loc 0x0a000000 intr gic:48 hostdev {emmc_device}
vdev virtio-net loc 0x1c0c0000 intr gic:40 mac {virtio_net_mac} ...

guest load 0x40200000,u-boot-with-prior-stage-dt.bin
```

Key details:
- `hostdev {emmc_device}` — the block device passed to the VM (this is what fls flashes to)
- `guest load` — the VM firmware/bootloader binary
- `{virtio_net_mac}` — substituted by Python `.format()` before writing to disk, so the QVM config file contains the literal MAC address

### Power on sequence (via qnx.serial)

1. Ensure QNX is booted (call `self.children["qnx"].power.on()` if needed)
2. Upload rendered config to `qvm_config_path` via SSH
3. Send launch command over `qnx.serial`:
   ```
   cd <working_directory> && <qvm_path> @<qvm_config_path>
   ```
4. Serial output transitions from QNX shell to QVM console output
5. If `uboot_bootcmd` is set: interrupt U-Boot (send ESC/space), then send the bootcmd
6. Wait for QVM boot confirmation (configurable prompt/string?)

### Power off sequence (via qnx.serial)

1. Send `slay qvm` over `qnx.serial` (kills the QVM hypervisor process)
2. Serial transitions back to QNX shell prompt
3. Wait for QNX `final_prompt`

### Storage flash (fls with ssh:// target)

fls natively supports `ssh://` targets. The flasher runs fls on the **exporter
side**, writing through SSH into QNX. Connection details are derived from the
`qnx` driver's children:

```bash
# Host/port from qnx.tcp, user from qnx.ssh
fls from-url "oci://quay.io/org/image:latest" ssh://root@192.168.100.126/dev/emmc0
fls from-url "https://example.com/image.img.xz" ssh://root@192.168.100.126:2222/dev/emmc0
```

The QvmFlasher constructs the `ssh://` URL from the `qnx` driver's TCP host/port,
SSH username, and the `emmc_device` config. If `ssh_password` is set, it's passed
to fls via the appropriate mechanism (environment variable or CLI flag).

**Important**: QVM must be powered off before flashing (the block device
`/dev/emmc0` is in use while QVM is running). The flasher should enforce this.

### Console and serial port state management

QVM console is a `ref:` to the `qnx.serial` child — same physical UART. Since
the serial port is shared between boot orchestration and the user console,
access must be coordinated via a state machine:

```
BOARD_OFF → QNX_BOOTING → QNX_READY → QVM_STARTING → QVM_RUNNING → QVM_STOPPING → QNX_READY
    ↑                                                                                  │
    └──────────────────────── power off ───────────────────────────────────────────────┘
```

| State | Serial owned by | Description |
|-------|----------------|-------------|
| `BOARD_OFF` | nobody | `power_ctl` is off |
| `QNX_BOOTING` | `QnxPower.on()` | IPL interaction, init_script — serial is locked |
| `QNX_READY` | idle | QNX shell prompt, serial available |
| `QVM_STARTING` | `QvmPower.on()` | Launching QVM, U-Boot interaction — serial is locked |
| `QVM_RUNNING` | **user console** | QVM I/O on serial — `j qvm serial console` works here |
| `QVM_STOPPING` | `QvmPower.off()` | `slay qvm`, waiting for QNX prompt — serial is locked |

**Behavior of `j qvm serial console`:**

1. If state is `QVM_RUNNING` → connect immediately
2. If state is `QVM_STARTING` or `QNX_BOOTING` → **wait** until `QVM_RUNNING`, then connect
3. If state is `BOARD_OFF` or `QNX_READY` → error ("QVM is not running, use `j qvm power on` first")
4. If state transitions to `QVM_STOPPING` or `BOARD_OFF` while connected → **disconnect** gracefully

This supports the common pattern:
```bash
j qvm power on & j qvm serial console
# console waits for power on to finish, then attaches
```

**Implementation:** The Qvm driver holds an `asyncio.Event` (or similar) that
is set when `QVM_RUNNING` is reached and cleared on `QVM_STOPPING`/`BOARD_OFF`.
The console `connect` method `await`s this event before forwarding the serial
stream to the user.

---

## Example exporter YAML

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
endpoint: grpcs://jumpstarter.example.com:443
token: "..."
export:
  qnx:
    type: "jumpstarter_driver_qnx.driver.Qnx"
    config:
      boot_prompt: "Press 'M' for eSD download"
      boot_key: "M"
      final_prompt: "#"
      init_script: |
        mkdir -p /root/.ssh
        echo "ssh-ed25519 AAAA..." >> /root/.ssh/authorized_keys
    children:
      power_ctl:
        type: "jumpstarter_driver_dutlink.driver.Dutlink"
        config:
          serial: "XXXX"
      serial:
        type: "jumpstarter_driver_pyserial.driver.PySerial"
        config:
          url: "/dev/ttyUSB0"
          baudrate: 115200
      tcp:
        type: "jumpstarter_driver_network.driver.TcpNetwork"
        config:
          host: "192.168.100.126"
          port: 22
      ssh:
        type: "jumpstarter_driver_ssh.driver.SSHWrapper"
        config:
          default_username: "root"
        children:
          tcp:
            ref: "tcp"

  qvm:
    type: "jumpstarter_driver_qnx.driver.Qvm"
    config:
      emmc_device: "/dev/emmc0"
      ssh_password: "root"
      qvm_path: "/sbin/qvm"
      working_directory: "/tmp"
      virtio_net_mac: "52:54:00:ab:cd:ef"
      qvm_config_path: "/tmp/qvm-autosd10.conf"
      uboot_bootcmd: "bootefi bootmgr ${fdtcontroladdr}"
      qvm_default_config: |
        system uboot-guest
        cpu sched 80
        ram 0x40000000,2024m
        vdev pl011 loc 0x09000000 intr gic:33
        vdev virtio-blk loc 0x0a000000 intr gic:48 hostdev {emmc_device}
        vdev virtio-net loc 0x1c0c0000 intr gic:40 mac {virtio_net_mac} name p2p peer /dev/vdevpeers/vp0 peerfeats 0x0000
        guest load 0x40200000,u-boot-with-prior-stage-dt.bin
    children:
      qnx:
        ref: "qnx"
```

---

## CLI usage examples

```bash
# QNX board
j qnx power on              # hard power + IPL boot + init script
j qnx power off             # shutdown + hard power off
j qnx serial console        # main serial console
j qnx ssh -- uname -a       # SSH into QNX

# QVM (nested inside QNX)
j qvm storage flash ./rhivos.simg.xz
j qvm storage flash oci://quay.io/mangelajo/rhivos:latest
j qvm power on              # ensures QNX is booted, then launches QVM via serial
j qvm power off             # stops QVM via serial
j qvm serial console        # same UART — shows QVM output when QVM is running

# QVM config management
j qvm config show            # dump current .conf from the board
j qvm config set ./my.conf   # push a new .conf to the board (QVM must be off)
j qvm config reset           # restore default from exporter YAML
```

---

## Resolved decisions

- [x] QVM stop command: **`slay qvm`** over serial
- [x] Working directory: **configurable** via `working_directory` config field (default `/tmp`)
- [x] MAC address: **explicit config field** `virtio_net_mac` — no board-side derivation
- [x] Runtime templating: **yes** — `qvm_default_config` supports `{variable}` substitution via Python `.format()`, `set_qvm_config()` for arbitrary overrides
- [x] SSH connection details: **derived from qnx.tcp + qnx.ssh** — no duplication
- [x] Template syntax: **Python `.format()`** — variables are `{virtio_net_mac}` and `{emmc_device}`, substituted before writing to disk (no QVM `$env{}` syntax needed)

## Open questions / TODOs

- [ ] QVM boot confirmation: what output/prompt indicates QVM has started successfully?
- [ ] SSH key/password auth for fls ssh:// target — how does fls accept credentials? Check [jumpstarter-dev/fls](https://github.com/jumpstarter-dev/fls) source code
- [x] Timeout values: **configurable with sensible defaults** — added to driver configs
- [x] init_script error handling: **check overall exit code only** (not per-line)
- [x] U-Boot interrupt in QVM: **any key** stops autoboot (send space repeatedly until prompt appears)
