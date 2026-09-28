# Native Windows end-to-end tests

This compatibility runner uses native Windows Python, a Linux exporter built
from the working checkout, and Jumpstarter 0.9.0 in Kind. It exercises actual
gRPC, router, lease and subprocess paths. MockPower provides a deterministic
device; this is not a hardware test. The Linux fixture also exports a TCP echo
service and PySerial `loop://`; Windows clients without those drivers' Windows
support load them as stubs.

The managed Windows probes exercise the production Rust/PyO3 AF_UNIX listener.
Failures return a nonzero exit code and remain in the report. No test transport
is injected. A Linux control run helps distinguish platform and deployment failures.
The standalone runners below cover native Windows `jmp run`, lifecycle hooks
and interactive serial consoles without requiring a controller.

## Prerequisites

- Windows, PowerShell, Git and uv, with the selected Windows client packages
  installed in `python/.venv`. See [platform builds and tests](../../docs/source/contributing/platform-testing.md)
  for the development environment and [the native package](../../python/native/jumpstarter-core/README.md)
  for its build requirements. Source synchronization requires Rust and MSVC,
  or an already-installed native wheel followed by `--no-sync`.
- A running Linux Podman machine with its root connection available, Kind and
  kubectl. These commands use the Podman Desktop installation paths; put your
  equivalent executables on PATH if installed elsewhere.
- Free loopback ports 8082, 8083 and 19090. The managed exporter also uses port
  19091 inside the Podman machine.

Run commands from the repository root. Controller fixture configuration,
credentials, image staging and reports belong under the ignored
`.e2e/windows-client/` path. Standalone runners also store results under `.e2e/`.
The dedicated cluster name is `jumpstarter-windows-e2e`. Do not reuse these
commands against a shared cluster or real hardware.

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Podman;$env:USERPROFILE\.local\share\containers\podman-desktop\extensions-storage\podman-desktop.kind\bin;$env:USERPROFILE\.local\share\containers\podman-desktop\extensions-storage\podman-desktop.kubectl-cli\bin;$env:PATH"
$env:KIND_EXPERIMENTAL_PROVIDER = 'podman'
$env:CONTAINER_CONNECTION = 'podman-machine-default-root'
$kube = Join-Path $PWD '.e2e/windows-client/kubeconfig'
New-Item -ItemType Directory -Force .e2e/windows-client | Out-Null
kind get clusters
podman ps -a
```

These environment variables apply to the current terminal only. Inspect existing
resources before proceeding; the commands below create new, named test resources.

## Controller

```powershell
kind create cluster --name jumpstarter-windows-e2e --config e2e/windows/kind.yaml --kubeconfig $kube --wait 120s
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.19.2/cert-manager.yaml
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e apply -f https://github.com/jumpstarter-dev/jumpstarter/releases/download/v0.9.0/operator-installer.yaml
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e -n cert-manager wait --for=condition=available deployment/cert-manager-webhook --timeout=120s
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e -n jumpstarter-operator-system wait --for=condition=available deployment/jumpstarter-operator-controller-manager --timeout=120s
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e apply -f e2e/windows/jumpstarter.yaml
```

Wait for the operator to create `jumpstarter-controller` and
`jumpstarter-router-0` in namespace `jumpstarter-windows-e2e`, then:

```powershell
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e -n jumpstarter-windows-e2e wait --for=condition=available deployment/jumpstarter-controller deployment/jumpstarter-router-0 --timeout=180s
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e apply -f e2e/windows/identities.yaml
kubectl --kubeconfig $kube --context kind-jumpstarter-windows-e2e -n jumpstarter-windows-e2e wait --for=jsonpath='{.status.credential.name}' client/windows-e2e-client exporter/windows-e2e-linux --timeout=60s
./e2e/windows/export-configs.ps1 -Kubeconfig $kube -OutputDirectory .e2e/windows-client/credentials
```

The bootstrap script requires a fresh credential directory and restricts it to
the current Windows user and SYSTEM before saving the test configs. It reads
the cluster's CA and keeps TLS verification enabled without installing a system
trust root.

## Linux exporters

```powershell
./e2e/windows/build-exporter.ps1
podman run -d --name jumpstarter-windows-e2e-direct -p 127.0.0.1:19090:19090 localhost/jumpstarter-windows-e2e:latest
podman create --name jumpstarter-windows-e2e-managed --network host localhost/jumpstarter-windows-e2e:latest jmp run --exporter-config /fixture/exporter.json
podman cp .e2e/windows-client/credentials/exporter.json jumpstarter-windows-e2e-managed:/fixture/exporter.json
podman start jumpstarter-windows-e2e-managed
```

The direct listener deliberately uses plaintext gRPC on an explicitly published
loopback port. The managed exporter uses the Podman machine's host network to
reach the Kind ports at `localhost:8082` and `localhost:8083`, with TLS and its
own exporter identity. Its echo service listens only on `127.0.0.1:19091` inside
that Linux machine. Neither exporter needs physical-device privileges.

Wait until the native CLI shows the managed exporter online:

```powershell
uv run --project python --no-sync jmp get exporters --client-config .e2e/windows-client/credentials/client.json --with online,status
```

## Run and compare

```powershell
uv run --project python --no-sync python e2e/windows/windows_client_e2e.py --direct-endpoint 127.0.0.1:19090 --direct-insecure --controller-config .e2e/windows-client/credentials/client.json --report .e2e/windows-client/windows-results.json
```

Add `--powershell pwsh` to run two more session probes. These start `jmp shell`
without a custom command and feed input to its PowerShell 7 session. Each checks
the prompt and session variables, runs `j --help` and `j power read`, and verifies
the shell's exit code. The managed probe also verifies socket/directory cleanup
and lease release. Active-lease checks allow at most five seconds for the
controller's asynchronous release reconciliation; a lease still active at the
deadline fails the probe. This is redirected-input coverage, not a real console test;
Windows PowerShell 5 handles redirected interactive input differently and is
covered separately by bootstrap/explicit-command regression tests.

Each probe has a deadline and prints PASS, FAIL or SKIP. Supply both connection
arguments for the complete check; omitting one skips that group. The report
records platform, Python/package versions, error types and per-probe timing,
with credential values redacted. A failing managed Windows probe must not be
counted as a passing E2E run.

Use the dedicated internal-token test config generated above. The runner rejects
refresh-token configs to prevent the stock shell's OIDC refresh from rewriting a
supplied config, and gives each CLI probe a temporary user-config directory.

Run the same probes with a Linux interpreter in the managed exporter container:

```powershell
podman cp e2e/windows/windows_client_e2e.py jumpstarter-windows-e2e-managed:/fixture/windows_client_e2e.py
podman cp .e2e/windows-client/credentials/client.json jumpstarter-windows-e2e-managed:/fixture/client.json
podman exec jumpstarter-windows-e2e-managed python /fixture/windows_client_e2e.py --direct-endpoint 127.0.0.1:19090 --direct-insecure --controller-config /fixture/client.json --report /tmp/linux-results.json
podman cp jumpstarter-windows-e2e-managed:/tmp/linux-results.json .e2e/windows-client/linux-results.json
```

Check exporter/controller logs when a probe fails. No SSH server, ADB device or
physical COM device is exercised by this fixture.

## Interactive serial console

`e2e/windows/windows_serial_console_e2e.py` runs the stock `j serial console`
inside a real Windows ConPTY. It uses only the direct Linux exporter on
`127.0.0.1:19090`, with the `serial` PySerial `loop://` fixture. Kind, controller
credentials and leases are not needed for this runner. For console-only testing,
build the image and start only `jumpstarter-windows-e2e-direct` from the Linux
exporter commands above.

Install `pywinpty` and `pywin32` in the test environment. They create and inspect
the test console; they are not Jumpstarter runtime dependencies. Use the current
Windows runtime dependencies, including gRPC 1.84 or later.

```powershell
uv pip install --python python/.venv/Scripts/python.exe pywinpty==3.0.5 pywin32
uv run --project python --no-sync python e2e/windows/windows_serial_console_e2e.py --direct-endpoint 127.0.0.1:19090 --direct-insecure --report-dir .e2e/windows-client
```

The three probes verify:

- Exact remote bytes for Unicode (including supplementary characters), arrows,
  Enter and Ctrl+C; the first two Ctrl+B bytes are forwarded and the third exits.
- Observe mode displays a second client's output while sending no keyboard
  input. Reading the actual console screen verifies lines wider than its 160
  columns wrap intact and carriage returns overwrite the current line.
- Closing the disposable serial driver ends a console waiting for keyboard
  input. Every probe checks input/output console modes are restored, subsequent
  Unicode line input works and the exporter has no remaining attached clients.

The runner refuses to start on an already-used serial fixture. Its EOF probe
closes only that fixture's serial driver, so use the dedicated container rather
than a shared exporter. Each probe has a 60-second process deadline and writes
JSON, raw received bytes and a terminal transcript into a fresh report directory.
Failure returns nonzero. `--probe observe` selects one probe; `--output-mode 3`
also checks restoration when VT output was initially disabled. `--probe smoke`
checks the ConPTY harness without connecting to an exporter.

## Native Windows exporter

`e2e/windows/windows_exporter_e2e.py` starts the stock Windows `jmp run`
launcher against its own ephemeral loopback fixtures. It needs the installed
CLI, current `jumpstarter-core` wheel and power/network/pyserial driver packages, and uses the
default PowerShell hook executor. It needs no Podman, Kind, controller
credentials or physical devices.

```powershell
python/.venv/Scripts/python.exe e2e/windows/windows_exporter_e2e.py --report-dir .e2e/windows-exporter --timeout 30
```

The runner exports MockPower, a TCP echo network and PySerial `loop://` and
configures two lifecycle hooks. Its
PowerShell `beforeLease` hook runs `j power on` through the exporter's hook
socket, and its Python `afterLease` hook runs on graceful shutdown. It checks
both hooks, the direct SDK/CLI probes from `windows_client_e2e.py`, an automatic
worker restart, graceful Ctrl+Break shutdown (exit code 0), preserved JSON DEBUG
logs, reuse of the same listener, and process-tree cleanup after forcefully
terminating the supervisor.
A test-only driver spawns a disposable descendant for cleanup assertions;
never load this `ProcessFixture` in a real exporter. Each process operation has
a deadline. A fresh directory holds JSON reports and logs; failure returns
nonzero. The runner closes its processes and listeners afterward.

These checks cover the standalone exporter with a synthetic driver. Native
Windows controller registration and device backends require separate coverage.
See the [native package](../../python/native/jumpstarter-core/README.md) for the
process, console and local socket components used by the runtime.

## Native network protocols

`e2e/windows/windows_network_e2e.py` exercises network driver clients against a
stock native Windows `jmp run`, using `e2e/windows/windows_fixture.py` for
exporter startup and shutdown. Peers are owned loopback TCP, UDP and WebSocket
servers; no driver, transport or subprocess is monkeypatched. It needs no Kind
cluster, controller credentials or physical devices.

```powershell
python/.venv/Scripts/python.exe e2e/windows/windows_network_e2e.py --output .e2e/windows-network.json
```

`--case tcp`, `udp` or `websocket` selects one workflow (default deadline 60
seconds per case). It checks four concurrent TCP forwards, exact binary bytes,
datagram and message boundaries, reconnect and listener cleanup, and that the
exporter stops gracefully and removes its worker processes and listener.

## Cleanup

Keep the resources for subsequent runs, or remove these dedicated resources:

```powershell
podman rm -f jumpstarter-windows-e2e-direct jumpstarter-windows-e2e-managed
kind delete cluster --name jumpstarter-windows-e2e --kubeconfig $kube
```

The image, reports and private test configs remain local. After deleting the
cluster its credentials no longer grant access to a running controller. The
commands do not modify or delete the user's default Kubernetes configuration.
