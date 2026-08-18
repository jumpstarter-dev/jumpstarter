# E2E Test Suite Summary

This document summarizes the Go/Ginkgo e2e test suite in `e2e/test/`, organized by
lane (Ginkgo `Label`, which is how CI and the Makefile select subsets via
`--label-filter` / `GINKGO_LABEL_FILTER`).

## Global prerequisites (`make e2e-run`, non-compat suite)

Built by `make e2e-setup` (`e2e/setup-e2e.sh`): kind cluster, dex OIDC provider +
TLS CA, controller/operator deployed via `make -C controller deploy`, service
accounts `test-client-sa`/`test-exporter-sa`, `/etc/jumpstarter/exporters` dir,
endpoint/login-endpoint env written to `.e2e-setup-complete`, QEMU guest image
prefetched. Tests run via `make e2e-run` → `go run ginkgo` with
`JUMPSTARTER_GRPC_INSECURE=1`.

---

## Lane: `core` (`e2e_test.go`)

Prereq (all): dex + controller/operator running, namespace created.

| Test Name | Prerequisite | Steps | Pass Check |
|---|---|---|---|
| serves landing page | login endpoint up | `curl` login endpoint | body contains "Jumpstarter" and "jmp login" |
| can create clients with admin cli | — | `jmp admin create client` for oidc/sa/legacy variants | no error; `client list` shows legacy client |
| can create exporters with admin cli | — | `jmp admin create exporter` for oidc/sa/legacy, merge driver overlay | no error; `exporter list` shows legacy exporter |
| can login with oidc test-client-oidc | client created | `jmp login` via dex username/password | no error; client listed |
| can login with oidc test-client-oidc-provisioning | dex issuer up | `jmp login` with empty `--name` (auto-provision) | client `test-client-oidc-provisioning-example-com` listed |
| can login with oidc test-client-sa | SA `test-client-sa` exists | create k8s token, `jmp login --connector-id kubernetes` | client `test-client-sa` listed |
| can login with oidc test-exporter-oidc | exporter created | `jmp login --exporter-config`, merge driver overlay | exporter `test-exporter-oidc` listed |
| can login with oidc test-exporter-sa | SA `test-exporter-sa` exists | k8s token login to per-user config path, merge overlays | user exporter config file exists; exporter listed |
| can login with simplified login (`operator-only`) | operator mode | delete old client cfg, `jmp login user@http://endpoint` | CA populated in client yaml; client marked default (`*`) |
| admin can rotate a client token | legacy client exists | `jmp admin rotate client` | output contains "Token rotated" |
| admin rotate with --save updates local config | rotate above | rotate `--save`, compare token before/after | new token differs from old |
| client can self-rotate token via auth rotate | legacy client config | `jmp auth rotate --client` | output contains "rotated"; token changed |
| auth status shows valid token after rotation | rotated token | `jmp auth status --client` | output contains "Valid" |
| double rotation produces distinct tokens | legacy client | rotate twice with `--save` | two tokens differ |
| admin rotate fails for non-existent client | — | rotate unknown client name | command errors |
| rotated token works for listing exporters | rotated token | `jmp get exporters --client` | no error |
| can run exporters | clients+exporters logged in | start oidc/sa/legacy exporter processes | `WaitForExporters` reaches Available |
| can specify client config only using environment variables | exporters running | fetch endpoint/token via kubectl, run `jmp shell` with `JMP_*` env vars only | no error |
| legacy client config contains CA cert and works with secure TLS (`operator-only`) | exporters running | inspect legacy client yaml `tls.ca`; run `jmp get exporters` without `JUMPSTARTER_GRPC_INSECURE` | CA present; command succeeds over real TLS |
| can operate on leases | exporters running | create lease, list leases/exporters, test label-selector filters (`=`, `!`, multi-expr), delete all | selector filtering returns correct/empty results |
| can create a lease with context metadata | exporters running | `create lease --context k=v` x2, fetch yaml | CRD-persisted context keys/values present in output |
| paginated lease listing returns all leases | exporters running | create 10 leases, `get leases --page-size 5 -o name` | exactly 10 lines returned across pages |
| paginated exporter listing returns all exporters | — | create 10 exporters w/ shared label, list with `--page-size 5` | exactly 10 names returned |
| lease listing shows expires at and remaining columns | exporters running | create lease, `get leases` with wide COLUMNS | output has "EXPIRES AT" and "REMAINING" |
| can transfer lease to another client | exporters running | create lease, wait Ready, `update lease --to-client` | updated lease yaml shows new client |
| can lease and connect to exporters | exporters running | `jmp shell --selector ... j power on` for oidc/sa/legacy/provisioning clients | all shells succeed |
| can lease and connect to exporters by name | exporters running | `jmp shell --name <exporter>` (+ combined `--name`+`--selector`) | all succeed |
| fails fast when requesting non-existent exporter by name | exporters running | `timeout 20s jmp shell --name doesnotexist` | errors (not a 124 timeout); message contains "cannot be satisfied" |
| can get CRDs with admin cli | — | `jmp admin get client/exporter/lease` | no error |
| can delete clients with admin cli | clients exist | delete oidc/sa/legacy clients | secrets/CRDs no longer found via kubectl |
| can delete exporters with admin cli | exporters exist | delete oidc/sa/legacy exporters | secrets/CRDs no longer found via kubectl |

---

## Lane: `hooks` (`hooks_test.go`)

Prereq: OIDC client/exporter `test-client-hooks`/`test-exporter-hooks` created+logged
in; each test overlays a different hook config YAML from `e2e/exporters/` before
restarting the exporter.

| Group / Test | Prerequisite | Steps | Pass Check |
|---|---|---|---|
| A1 beforeLease hook executes | `exporter-hooks-before-only.yaml` | `jmp shell ... j power on` | output has "BEFORE_HOOK_MARKER: executed" |
| A2 afterLease hook executes | `exporter-hooks-after-only.yaml` | same | output has "AFTER_HOOK_MARKER: executed" |
| A3 both hooks execute in order | `exporter-hooks-both.yaml` | same | BEFORE marker index < AFTER marker index |
| B1 beforeLease onFailure=warn allows shell | `-before-fail-warn.yaml` | shell command | succeeds; contains "HOOK_FAIL_WARN"; exporter returns Available |
| B2 beforeLease onFailure=endLease fails shell | `-before-fail-endLease.yaml` | shell w/ `--retry-timeout 0` | errors; message matches hook-fail/shutdown/connection-lost regex |
| B3 endLease releases lease, accepts new one | same config | run shell twice | both fail same way; exporter returns Available both times |
| B4 fail+endLease skips afterLease | `-before-fail-endLease-with-after.yaml` | shell fails | output lacks "AFTER_SHOULD_NOT_RUN" |
| B5 onFailure=exit shuts down exporter | `-before-fail-exit.yaml` (single-run mode) | shell fails | exporter process exits within 30s; goes Offline |
| C1 afterLease onFailure=warn keeps exporter available | `-after-fail-warn.yaml` | shell succeeds | contains "HOOK_FAIL_WARN"; exporter stays Available |
| C2 afterLease onFailure=exit shuts down exporter | `-after-fail-exit.yaml` (single-run) | shell (result ignored) | exporter process exits; goes Offline |
| D1 beforeLease timeout treated as failure | `-timeout.yaml` (hook sleeps past its 5s `timeout`, `onFailure: warn`) | shell succeeds | contains "HOOK_TIMEOUT: starting" |
| E1 beforeLease can use j power on | `-both.yaml` | shell | contains "BEFORE_HOOK: complete" |
| E2 afterLease can use j power off | `-both.yaml` | shell | contains "AFTER_HOOK: complete" |
| E3 env vars available in hooks | `-both.yaml` | shell | output matches `lease=[hex-uuid]` and `client=` |
| F1 exec /bin/bash runs bash syntax | `-exec-bash.yaml` | shell | contains "BASH_HOOK: complete" |
| F2 .py file auto-detects Python + driver API | `-exec-python.yaml` + write `/tmp/...hook-python.py` | shell | contains "PYTHON_HOOK: driver API works" and "complete" |
| F3 script as file path executes | `-exec-script-file.yaml` + write `/tmp/...hook-script.sh` | shell | contains "SCRIPTFILE_HOOK: complete" |
| G1 no hooks + lease timeout exits cleanly | `-none.yaml` | `jmp shell --duration 10s -- sleep 30` under `timeout 60` | output has no "Error:" |
| G2 lease timeout during slow beforeLease exits cleanly | `-slow-before.yaml` | `--duration 5s -- sleep 30` | no "Error:" |
| G3 lease timeout shortly after beforeLease exits cleanly | `-slow-before.yaml` | `--duration 12s -- sleep 30` | no "Error:" |
| H1 infra messages hidden from client output | `-before-only.yaml` | shell | has marker; lacks "Starting hook subprocess"/"Creating PTY"/"Hook executed successfully" |
| H2 fail+exit does NOT run afterLease | `-before-fail-exit-with-after.yaml` (single-run) | shell fails | output lacks "AFTER_SHOULD_NOT_RUN"; exporter goes Offline |
| H3 warning shown when beforeLease fails w/ warn | `-before-fail-warn.yaml` | shell succeeds | contains "Warning:" |
| H4 warning shown when afterLease fails w/ warn | `-after-fail-warn.yaml` | shell succeeds | contains "Warning:" |

---

## Lane: `direct-listener` (`direct_listener_test.go`)

Prereq: no controller/lease needed — exporter runs standalone with `jmp run --listen`
on port 19090; each test starts its own process against a config in `e2e/exporters/`.

| Test Name | Prerequisite | Steps | Pass Check |
|---|---|---|---|
| exporter starts and client can connect | `exporter-direct-listener.yaml` | `jmp shell --tls-grpc host:port --tls-grpc-insecure -- j power on` | no error |
| client can call multiple driver methods | same | power on then power off | both succeed |
| client without --tls-grpc-insecure fails against insecure server | same | shell without insecure flag | errors |
| beforeLease hook executes and j commands work | `exporter-direct-hooks-before.yaml` | shell with `--exporter-logs` | contains "BEFORE_HOOK_DIRECT: executed/complete" |
| afterLease hook runs on exporter shutdown | `exporter-direct-hooks-both.yaml` | shell, then SIGTERM the exporter process | before marker seen; stderr eventually shows "AFTER_HOOK_DIRECT: executed" |
| correct passphrase connects | listener started with passphrase `my-secret` | shell with `--passphrase my-secret` | no error |
| wrong passphrase is rejected | same | shell with wrong passphrase | errors; stderr shows auth-failed message; wrong passphrase value never logged |
| missing passphrase is rejected | same | shell without passphrase flag | errors; stderr shows auth-failed message |

(AfterEach verifies port 19090 fully releases between tests.)

---

## Lane: `dut-network` (`dut_network_test.go`)

Prereq: **Linux + root/passwordless-sudo** (skipped otherwise); sets up two network
namespaces (`jmp-e2e-dut`, `jmp-e2e-ext`) + veth pairs simulating a DUT and external
network, then starts a direct exporter (`exporter-dut-network.yaml`) on port 19091
offering the `dut-network` driver (nftables NAT/masquerade + DHCP/DNS).

| Test Name | Steps | Pass Check |
|---|---|---|
| should report network status via CLI | `j dut-network status` | output has veth name, "masquerade"; JSON has `interface_status` |
| should show leases via CLI | `j dut-network leases` | non-empty output |
| should show active NAT rules | `j dut-network nat-rules` | contains "masquerade" and nft table name |
| should allow DUT to reach external via NAT | ping from DUT netns to ext IP | ping succeeds (`Eventually`) |
| should return error for unknown MAC | `j dut-network get-ip ff:ff:ff:ff:ff:ff` | errors; "No lease found" |
| should add and remove an address entry via CLI | `add-address` then `remove-address` | "Added" then "Removed" |
| should add, list, and remove DNS entries via CLI | `add-dns`, `dns-entries`, `remove-dns`, `dns-entries` | entry appears then disappears |
| should allow TCP connections from DUT to external via NAT | start Python TCP server in ext ns, connect from DUT ns via NAT | client receives "E2E_OK" |

---

## Lane: `exit-on-lease-end` (`exit_on_lease_end_test.go`)

Prereq: legacy client/exporter `test-client-exit-on-lease-end` /
`test-exporter-exit-on-lease-end`, overlay `exporter-exit-on-lease-end.yaml`
(sets `exitOnLeaseEnd`).

| Test Name | Steps | Pass Check |
|---|---|---|
| exporter exits after serving one lease | start exporter single-run, run one `jmp shell ... j power on` | exporter process exits within 60s; goes Offline in controller |
| exporter does not exit before any lease is served | start exporter single-run, idle | process stays alive for 10s (`Consistently`) |

(AfterEach also verifies tracked PIDs actually terminate after `StopAll`.)

---

## Lane: `exporterset-qemu` (`exporterset_qemu_test.go`)

Prereq: exporterset-controller Deployment running; QEMU-runtime image; native guest
arch detected via `qemu-guest-arch.sh`; Alpine guest image ensured via
`ensure-qemu-guest-image.sh`; OIDC e2e client; applies
`manifests/exporterset-qemu-kind-{arch}.yaml` (creates an `ExporterSet`).

| Test Name | Steps | Pass Check |
|---|---|---|
| brings an Exporter Online with a Ready Pod | wait for ExporterSet-created Exporter, wait Online/Registered/Available, wait Pod Running+Ready | Pod ready; `target-runtime` container has the expected `qemu-system-*` binary |
| leases, flashes Alpine, and boots to a console login marker | (skipped if shared emptyDir `sizeLimit` is empty or `100Mi`, pending #924) run `qemu_flash_boot.py` under `jmp shell --duration 1h` | script output contains "OK: matched marker" |
| power cycles QEMU then rotates the Pod/Exporter, stays responsive | record old Pod name/UID; `j qemu power on/off` inside shell, verify qemu binary is the running process; wait old Pod+Exporter deleted, one new Pod/Exporter running w/ new UID; re-run `j qemu power on/off` | ExitAndReplace produced exactly one new, ready, differently-UID'd Pod/Exporter that still answers power commands |

---

## Lane: `auth-logging` (`auth_logging_test.go`)

Prereq: self-contained — creates its own legacy client/exporter
(`test-client-authlog` / `test-exporter-authlog`) with driver overlay. **Not run
in compat suites** (old controllers lack this logging).

| Test Name | Steps | Pass Check |
|---|---|---|
| controller logs client authentication failures with the peer address | corrupt token in client's local yaml config, `jmp get exporters --client` | command errors; controller logs (since test start) eventually contain "client authentication failed" with a `"peer"` field; corrupted token value never appears in logs |
| controller logs exporter authentication failures with the peer address | corrupt token in exporter's yaml config, start exporter once | controller logs eventually contain "exporter authentication failed" with `"peer"`; token value never appears in logs |

---

## Lane: `compat` + `old-controller` (`compat_old_controller_test.go`)

Prereq: separate setup path — `make e2e-compat-setup COMPAT_SCENARIO=old-controller
COMPAT_CONTROLLER_TAG=v0.8.1` deploys an **old (v0.8.1) controller** against current
client/exporter code. Skipped on PRs (merge-queue/`workflow_dispatch` only).

| Test Name | Steps | Pass Check |
|---|---|---|
| can create client with admin cli | `jmp admin create client --save` | no error |
| can create exporter with admin cli | `jmp admin create exporter`, merge driver overlay | no error |
| new exporter registers with old controller | start exporter loop | `WaitForExporter` reaches Available |
| exporter shows as Online and Registered | — | kubectl conditions Online/Registered both `True` |
| new client can lease and connect through old controller | — | `jmp shell ... j power on` succeeds |
| can operate on leases through old controller | — | create/list/filter/delete leases (including no-match selector) |
| exporter stays Online after lease cycle | — | conditions still `True` |
| client started before exporter connects | start `jmp shell` client first (background), wait 5s confirm still running, then start exporter | client shell completes successfully within 120s once exporter starts |
| cleans up resources | `jmp admin delete` client/exporter x2 | (cleanup, no strict assertions) |

---

## Lane: `compat` + `old-client` (`compat_old_client_test.go`)

Prereq: `make e2e-compat-setup COMPAT_SCENARIO=old-client
COMPAT_CLIENT_VERSION=0.7.4` — current controller, but client/exporter binaries from
an **old (v0.7.4) Python venv** (`PYTHON_OLD_VENV`); whole suite `Skip`s if that
venv/binary isn't found.

| Test Name | Steps | Pass Check |
|---|---|---|
| creates resources | `jmp admin create client/exporter`, merge overlay | no error |
| old exporter registers with new controller | start exporter loop using **old** `jmp` binary | `WaitForExporter` reaches Available |
| old exporter shows as Online (not incorrectly offline) | — | Online/Registered conditions `True` |
| old client can connect through new controller | **old** `OldJmp shell ... j power on` | no error |
| old exporter stays Online after lease completes | — | Online condition `True` |
| new client can connect to old exporter | current `jmp shell` | no error |
| old exporter still Online after multiple lease cycles | — | Online + Registered `True` |
| stop exporter and wait for offline | kill exporter process | kubectl wait `condition=Online=False` succeeds within 5m |
| old exporter recovers Online after reconnect | restart old exporter loop | reaches Available again |
| lease works after reconnect | old client shell | no error |
| client started before exporter connects | old-binary client shell started first (bg), then old exporter | client shell completes within 120s |
| cleans up resources | delete client/exporter x2 | cleanup only |

---

## Notes on structure

- **Lanes = Ginkgo `Label(...)`** on each top-level `Describe`; selected via
  `GINKGO_LABEL_FILTER` / `--label-filter` (see `e2e/run-e2e.sh` header comment).
  CI's `e2e-tests` job effectively runs the full suite (including
  `exporterset-qemu`); `e2e-compat-old-controller`/`e2e-compat-old-client` jobs run
  only on merge-queue/dispatch via `compat/run.sh`.
- **`operator-only`** is a secondary label on 2 individual `It`s inside the `core`
  lane (real-TLS/CA checks) — excluded when running against a plain controller
  (non-operator) deployment via `--label-filter "!operator-only"`.
- Failure log dumping is lane-specific infrastructure (for example: core and
  exit-on-lease-end dump exporter + controller logs; exporterset-qemu dumps
  exporterset/QEMU pod logs).
