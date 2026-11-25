# jumpstarter-driver-zephyr

Flash Zephyr firmware to a board attached to a Jumpstarter **exporter**, while
the test runner (twister) stays on the **client / CI** machine.

This is the flash half of the "server-side twister" workflow: twister runs
locally, builds the firmware, and for each test ships only the built image(s) to
the exporter. The exporter owns *how* to flash - the flash command and the
board's probe/target configuration live in this driver's config, so the client
is board-agnostic.

## How it fits together

```
jmp shell --selector <board>
+-- j zephyr twister <twister args>
    +-- west twister --device-testing \
            --device-serial-pty j,zephyr,console,pipe,-i,--retry-count,... \
            --flash-command jmp_flash_wrapper.sh,--driver,zephyr <twister args>
    +-- per testcase:
        +-- j zephyr console pipe -i --retry-count 10 ...
        |     (twister owns the PTY; pipe bridges it to the console)
        +-- jmp_flash_wrapper.sh  -> j zephyr flash --build-dir <dir>
              +-- (client side) tar zephyr.hex/.bin/.elf -> exporter
              +-- (exporter side) run flash command
                    (closing the console around it if release_consoles is set)
```

Everything runs inside one `jmp shell` lease; both commands attach to that
lease via `JUMPSTARTER_HOST`. `j zephyr twister` (below) adds the three
jumpstarter options and passes everything else to twister unchanged. `zephyr`
here is the driver's name in the exporter config: whatever it is, `j <name>
twister` passes it to both wrappers.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-zephyr
```

This installs the Zephyr driver and its `j zephyr` client commands (`flash` and
`twister`).

## Exporter config

The flash command runs with its working directory set to the directory holding
the uploaded firmware, so it can reference files by bare name (`zephyr.hex`) or
via the `{hex}` / `{bin}` / `{elf}` / `{dir}` tokens (absolute paths).

```yaml
export:
  zephyr:                       # any name; the commands are then `j <name> ...`
    type: jumpstarter_driver_zephyr.driver.Zephyr
    config:
      flash_command: >-
        openocd -f interface/cmsis-dap.cfg -f target/cc13x2_cc26x2.cfg
        -c "program {hex} verify reset exit"
      flash_timeout: 300        # seconds; 0 disables the timeout
    children:
      console:                  # serial used by twister (any name, see below)
        type: jumpstarter_driver_pyserial.driver.PySerial
        config:
          url: /dev/ttyACM0
          baudrate: 115200
```

If the flash tool opens the same tty as the console (e.g. esptool), set
`release_consoles: true`. While flashing, the driver then closes the tty of
every PySerial child, whatever its name, and reopens it afterwards; twister
stays attached and sees a `[reconnected]` line in the output. Leave it off
otherwise (e.g. openocd over SWD, as above): whatever the board prints while
the tty is closed is lost, including its first output after the reset that
ends the flash.

The board must be left **reset and running** after flashing so twister captures
the boot output over serial (e.g. openocd's `reset` above).

## Configuration reference

| Field              | Required | Default | Description                                                                                                    |
| ------------------ | -------- | ------- | -------------------------------------------------------------------------------------------------------------- |
| `flash_command`    | yes      | -       | Shell command to flash the board. Supports `{hex}/{bin}/{elf}/{dir}` tokens.                                   |
| `flash_timeout`    | no       | `300`   | Seconds before the flash command, and everything it started, is stopped. `0` disables.                         |
| `release_consoles` | no       | `false` | Close the PySerial children's ttys while flashing, for flash tools that open the console's tty (e.g. esptool). |

## Usage with twister

`j zephyr twister` runs `west twister` in the current directory, passing its
arguments through unchanged. Jumpstarter only adds:

- `--device-testing`;
- `--device-serial-pty`, running `j <driver> <console> pipe` for the driver's
  PySerial child;
- `--flash-command`, pointing at the bundled flash wrapper.

```bash
jmp shell --selector <board>
# then, with any twister arguments:
j zephyr twister -p <platform> -T <tests>
```

Twister builds the tests locally and only the images are sent to the exporter.
In CI, the build is often done in an earlier step with `--build-only`. The test
step then reuses the same arguments, from the same directory, with
`--test-only`, so twister finds the builds where it left them:

```bash
west twister --build-only -p <platform> -T <tests> -O <outdir>
# ... later, in a jmp shell:
j zephyr twister --test-only -p <platform> -T <tests> -O <outdir>
```

Options that would flash or reach the board some other way are rejected:
`--flash-command`, `--device-serial-pty`, `--device-serial`, `--hardware-map`,
`--generate-hardware-map`, and `--west-flash` / `--west-runner` /
`--west-flash-cmd` (twister would ignore them, since `--flash-command` takes
precedence).

Jumpstarter's own option is `--console <child>`. It selects the PySerial child
twister uses as the board's serial port and is only needed when the driver has
more than one. Jumpstarter's options must come first: everything from the first
other argument on is passed to twister unchanged, including a `--` and the
arguments after it, which twister hands to the test binary. For example,
`j zephyr twister --help` shows jumpstarter's help, and
`j zephyr twister -p <platform> --help` shows twister's.

`j zephyr twister` exits with twister's exit code. Twister returns non-zero when
any test fails, not only when twister itself fails, so a CI job that collects
the reports afterwards should not stop on it (e.g. `j zephyr twister ... || rc=$?`).

Twister splits `--device-serial-pty` on spaces and commas without a shell, so
the driver and console names must not contain either. Twister also connects
that command's stderr to the PTY, so `pipe`'s status line, and its error if it
cannot attach to the console, show up at the start of each test's serial
output. It retries for a few seconds while the previous test's connection still
holds the console.

## Manual use / debugging

Inside a `jmp shell` you can flash a build directory directly:

```bash
jmp shell --selector <board>
# then:
j zephyr flash --build-dir twister-out/<platform>/<test>
```

It prints one plain line per step (archive received, command being run), then
the flash command's own output as it runs. A failed flash exits non-zero with
the reason.

From Python, `ZephyrClient` is a
`jumpstarter.client.flasher.StreamingFlasherClient`: `flash_stream(path)` sends a
tar of the artifacts (a local path, or an HTTP(S) URL the exporter downloads
itself) and yields `FlashStatus` updates; each output line of the flash command
is in the `stdout` field of a `step` update. `flash(path)` returns the final
update.

## Image contract

The client uploads the fixed-name artifacts found under `<build_dir>/zephyr/`:
`zephyr.hex` and, when present, `zephyr.bin` / `zephyr.elf`. Single-image boards
are covered. Multi-image / MCUboot / partitioned flows are a known limitation of
the fixed-name convention.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_zephyr.client.ZephyrClient()
    :members: flash, flash_stream, flash_build_dir, twister
```
