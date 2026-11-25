# Robot Framework

[Robot Framework](https://robotframework.org/) is a test framework that uses
keywords. The `jumpstarter-robot` package makes each Jumpstarter driver
available as Robot Framework keywords. It also gets the {term}`lease` for a
test run.

For all arguments and keywords, refer to the
{doc}`Robot Framework reference <../../reference/package-apis/robot/reference>`.

## What you get

- **Keywords for each driver.** The package makes the keywords from the Python
  client of each driver. You do not change the drivers.
- **Completion in your editor.** A test file imports one keyword library for
  each driver. [RobotCode](https://robotcode.io/) reads these imports and
  shows each keyword. A lease is not necessary.
- **Lease control.** A test run can use a local exporter config, a
  `jmp shell`, or a lease from the {term}`controller`.
- **Standard libraries.** A port forward connects a local port to a driver.
  Then the
  [Telnet](https://robotframework.org/robotframework/latest/libraries/Telnet.html)
  library, [SSHLibrary](https://github.com/MarketSquare/SSHLibrary) and remote
  libraries can connect to the device.

## Installation

Install `jumpstarter-robot` and the driver packages of your exporter:

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-robot
```

## Quick start

The example in `python/examples/robot` uses mock drivers. The test run starts
these drivers in its own process ({term}`local mode`). Hardware and a
controller are not necessary.

1. Go to the example directory:

   ```console
   $ cd python/examples/robot
   ```

2. Run the tests:

   ```console
   $ robot -d results tests/
   ```

The file `exporter.yaml` gives the drivers of the mock board:

```yaml
export:
  power:
    type: jumpstarter_driver_power.driver.MockPower
  storage:
    type: jumpstarter_driver_opendal.driver.MockStorageMuxFlasher
  console:
    type: jumpstarter_driver_network.driver.EchoNetwork
  shell:
    type: jumpstarter_driver_shell.driver.Shell
    config:
      methods:
        hello: "echo \"$GREETING $@\""
```

The file `client.resource` declares the session and imports a keyword library
for each driver:

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/exporter.yaml    auto_import=False
Library    jumpstarter_robot.Driver    power    client=jumpstarter_driver_power.client.PowerClient    AS    power
Library    jumpstarter_robot.Driver    storage    client=jumpstarter_driver_opendal.client.StorageMuxFlasherClient    AS    storage
```

Each test file imports `client.resource`:

```robotframework
*** Settings ***
Resource            ../client.resource

*** Test Cases ***
Flash Image And Boot
    Power Off
    Storage Host
    Storage Flash    ${CURDIR}/../data/image.bin
    Storage Dut
    Power On
```

Each keyword calls one method of a driver client. `Power On` calls
`client.power.on()`. `Storage Flash` calls `client.storage.flash(...)`.

The first keyword that uses the exporter starts it. The test run stops the
exporter at the end. Thus a suite setup is not necessary.

You can run the same tests on a remote exporter that has the same drivers.
Use `jmp shell`:

```console
$ jmp shell --selector board=mock -- robot -d results tests/
```

## Driver keywords

The `jumpstarter_robot.Driver` library gives the keywords of one driver.
Import it one time for each driver:

```robotframework
Library    jumpstarter_robot.Driver    dut.power    client=jumpstarter_driver_power.client.PowerClient    AS    dut.power
```

- The first argument is the path of the driver in the exporter. `dut.power` is
  `client.dut.power` in Python.
- `client` is the client class of the driver. The library reads the keywords,
  their arguments and their documentation from this class. Thus your editor
  can show them.

Put these imports in a
[resource file](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#resource-files).
Then each test file imports that one file.

If you do not import a `Driver` library, the `Jumpstarter` library imports one
for each driver when the session connects. The keywords then exist only during
the test run, and your editor cannot show them.

### Keyword names

A keyword name is the path of the driver and then the name of the method.

| Driver path | Method | Keyword |
| --- | --- | --- |
| `dut.power` | `cycle` | `Dut Power Cycle` |
| `storage` | `flash` | `Storage Flash` |

To use a different prefix, give the `prefix` argument of the `Driver` library.

`Call Driver` calls a client method by its driver path. `List Drivers` and
`List Driver Keywords` show the drivers and keywords of an exporter:

```robotframework
Call Driver    dut.power    cycle    wait=5
${keywords}=    List Driver Keywords    dut.power
```

## Leases

A test run gets its lease in one of three ways.

### Use a lease that you made

Make the lease before the test run. Then give the name of the lease, or give
the same selector:

```console
$ jmp create lease -l board=rpi4 --duration 4h
$ JMP_LEASE=<name> robot -d results tests/
```

```robotframework
Library    jumpstarter_robot.Jumpstarter    selector=board=rpi4
```

A session with a selector uses an active lease of your client that has the
same selector. The test run does not release this lease.

### Use `jmp shell`

Each session uses the lease of the shell:

```console
$ jmp shell --selector board=rpi4 -- robot -d results tests/
```

### Get a lease in the tests

Use the `Acquire Lease` keyword. A good location is the suite setup in the
`__init__.robot` file of the test directory:

```robotframework
*** Settings ***
Resource       ../client.resource
Suite Setup    Acquire Lease    selector=board=rpi4    duration=1 hour
```

`Acquire Lease` first looks for an active lease with the same selector. If it
finds one, it uses that lease. If it does not find one, it gets a new lease.
The test run releases a new lease at the end.

Only `Acquire Lease` gets a new lease. If a session opens without a lease, the
keyword fails. The error message gives these three ways.

## Use standard libraries on a device

Special Jumpstarter keywords for device protocols are not necessary.
`Open Port Forward` connects a local port to the byte stream of a driver. It
returns the address as `host:port`. Then a standard library connects to that
address:

```robotframework
*** Settings ***
Library     String
Library     Telnet    timeout=30s    newline=CR    prompt=root@dut:~#
Resource    client.resource

*** Test Cases ***
Board Boots To Login Prompt
    ${address}=    Open Port Forward    dut.console
    ${host}    ${port}=    Split String    ${address}    :
    Telnet.Open Connection    ${host}    port=${port}
    Dut Power On
    Telnet.Login    root    ${PASSWORD}
    ${kernel}=    Telnet.Execute Command    uname -r
```

You can use the same procedure with these libraries:

- [SSHLibrary](https://github.com/MarketSquare/SSHLibrary), with a
  `TcpNetwork` driver that connects to the SSH port of the device.
- A
  [remote library](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#remote-library-interface)
  server on the device.

Some client methods are context managers. Use `Open Driver Context` to start
one. To read a stream of values, use `Collect Driver Stream`.

## Change the keywords of a driver

Each public method of a `DriverClient` class becomes a keyword. The type
hints give the argument types. The docstring gives the documentation.

To rename or hide a keyword, use the
[`@keyword` and `@not_keyword`](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#keyword-decorator)
decorators of Robot Framework on the client class.

## Next steps

- {doc}`Reference <../../reference/package-apis/robot/reference>`: all library
  arguments and keywords.
