# Jumpstarter v0.9.0 — Release Notes

## What's New

* **Operator-only deployment**: Helm charts have been fully removed and operator-based installation is now the sole supported path for Jumpstarter services.
* **DUT network isolation driver**: The new `jumpstarter-driver-dut-network` package enables full network isolation of devices under test, including DHCP/DNS via dnsmasq, IP aliasing, NTP responder, egress/ingress traffic filtering, and tcpdump streaming.
* **Lease tags**: Leases now support user-defined metadata tags, making it easier to organize and filter leases in multi-team environments.
* **Lease expiration display**: The CLI now shows "expires at" and "remaining" columns in lease listings so you can see at a glance when leases will be released.
* **Token rotation**: Internal token rotation for long-running clients ensures sessions remain authenticated across token lifetimes without manual intervention.
* **gRPC health checking**: The controller now exposes the standard gRPC Health protocol, making it straightforward to configure readiness and liveness probes.
* **Exporter context env vars**: `jmp shell` now exposes `JMP_EXPORTER`, `JMP_LEASE`, and `JMP_EXPORTER_LABELS` environment variables for scripting convenience.
* **Secret/ConfigMap references for JWT CA certificates**: The operator can now reference Kubernetes Secrets and ConfigMaps for JWT CA certificates, removing the need to inline certificate data.
* **Exporter rapid failure detection**: Containerized exporters now detect crash loops and auto-exit, allowing the container runtime to handle restarts cleanly.
* **Access policy descriptions**: Access policy rules now support a `description` field for documentation purposes.
* **Default exporter config path**: Exporter configs now default to `~/.config/jumpstarter`, reducing boilerplate.
* **Configurable installer paths**: `INSTALL_DIR` and `VENV_DIR` can now be set via environment variables, giving more flexibility over where Jumpstarter is installed.
* **Consolidated `--insecure` flag**: Multiple TLS flags have been replaced with a single `--insecure` option for simpler configuration.
* **OCI credential model**: A new `OciCredentials` model and hardened credential resolution make flashing from private registries more reliable.
* **MicroShift bootc image**: A new container image for MicroShift-based deployments is now available.
* **JEP process**: Jumpstarter Enhancement Proposals (JEPs) have been formalized with docs, process, and initial proposals (JEP-0011, JEP-0013, JEP-0014).
* **Renovate migration**: Dependabot has been replaced with Renovate for better cross-ecosystem dependency grouping.
* **Testing and CI**: E2E tests have been converted from Bats to Go + Ginkgo with parallel builds, log collection on failure, and reusable composite actions. Quality gates for coverage and type checking, per-package test logs, path-based filtering, and merge-queue-only full matrix runs have also been added.

## Driver Updates

The table below summarizes driver-level changes in v0.9.0.
`Status = New driver` indicates the package was added during the 0.9 release cycle.

| Driver | Status | Notable updates |
| :--- | :--- | :--- |
| `android` | **New** | ADB and emulator power drivers |
| `dut-network` | **New** | Full DUT network isolation: DHCP, DNS, NTP, tcpdump, traffic filtering |
| `mitmproxy` | **New** | HTTP(S) interception and backend mocking driver |
| `noyito-relay` | **New** | NOYITO USB Relay power driver |
| `obd` | **New** | OBD-II vehicle diagnostics driver |
| `renode` | **New** | Renode embedded target emulator driver |
| `someip` | **New** | SOME/IP driver wrapping opensomeip Python binding with static remote endpoint support |
| `ssh-mount` | **New** | Remote filesystem mounting via SSH |
| `st-link` | **New** | ST-LINK mass storage flasher for STM32 boards |
| `ble` | Updated | Added tests |
| `dutlink` | Updated | Fixed swapped voltage/current in power readings |
| `esp32` | Updated | Release serial port before flash to prevent port-locked errors |
| `flashers` | Updated | Default CA injection into flash commands, improved RideSX error messages, updated fls to 0.3.0 |
| `gpio` | Updated | Added read method required by PowerInterface |
| `http-power` | Updated | Parse power measurements and add power read CLI command |
| `iscsi` | Updated | Block device allowlist confinement |
| `network` | Updated | HttpServer.close() properly releases port on cleanup |
| `opendal` | Updated | Removed opendal dependency from QEMU driver, fixed local path upload |
| `pyserial` | Updated | PTY drain improvements to prevent output loss on macOS |
| `qemu` | Updated | Added OCI flashing support, construct image URL after HTTP server starts |
| `sdwire` | Updated | Support unprogrammed FT200X EEPROM + macOS storage/mux fixes |
| `shell` | Updated | Keep shell usable after token refreshes, block dangerous environment variables |
| `someip` | Updated | Use opensomeip PyPI release, defer OsipClient creation, fail fast when unavailable |

## Operator / Controller Changes

* Restricted operator ClusterRole RBAC permissions on roles/rolebindings
* Restart controller pods when configmap changes
* Set default resource requests/limits for controller and router pods
* Add Secret/ConfigMap references for JWT CA certificates
* Fix Containerfile build with rootless Podman
* Prevent lease assignment to non-ready exporters
* Serialize Dial/Listen queue handoff to prevent router token loss
* Retry Dial and StatusMonitor poll on transient UNAVAILABLE
* Server-side Dial retry with exponential backoff for transient Available status
* gRPC keepalive configuration fields wired into server options
* Authorization always derives resource names from OIDC username
* Fix exporter state machine: stuck exporters on lease-end during hooks
* Guard beforeLease hook from setting LEASE_READY after lease expiry
* Release lease when beforeLease hook fails with onFailure=endLease
* Deduplicate NotIn values in ParseLabelSelector
* Fix flaky test for competing scheduled leases
* Add description field to access policy rules
* Update Go dependencies for CVE fixes

## Installation

**Install via the Jumpstarter Operator:**

```bash
kubectl apply -f https://github.com/jumpstarter-dev/jumpstarter/releases/download/v0.9.0/operator-installer.yaml
```

### Documentation

* [Install with Operator](https://jumpstarter.dev/main/getting-started/installation/service/production.html)
* [Service installation overview](https://jumpstarter.dev/main/getting-started/installation/service/)

## Contributors

We would like to thank all our contributors, with a special shout-out to new contributors in this release:

| Commits | Name | GitHub |
| :--- | :--- | :--- |
| 53 | Paul Wallrabe | [@raballew](https://github.com/raballew) |
| 7 | Vinicius Zein | [@vtz](https://github.com/vtz) |
| 4 | Marek Mahut | [@mmahut](https://github.com/mmahut) |
| 1 | Alexandre Bailon | [@anobli](https://github.com/anobli) |
| 1 | Pierre-Yves Chibon | [@pypingou](https://github.com/pypingou) |

## Full Changelog (v0.8.1..release-0.9)

- docs: align production service guide with operator TLS and gRPC behavior ([#388](https://github.com/jumpstarter-dev/jumpstarter/pull/388)) ([`b21e5912`](https://github.com/jumpstarter-dev/jumpstarter/commit/b21e5912))
- Microshift bootc image ([#314](https://github.com/jumpstarter-dev/jumpstarter/pull/314)) ([`505e3dd2`](https://github.com/jumpstarter-dev/jumpstarter/commit/505e3dd2))
- fix: reject deletion of already-released leases ([#401](https://github.com/jumpstarter-dev/jumpstarter/pull/401)) ([`0fe8ed70`](https://github.com/jumpstarter-dev/jumpstarter/commit/0fe8ed70))
- fix: validate name argument in admin create/delete commands ([#398](https://github.com/jumpstarter-dev/jumpstarter/pull/398)) ([`a08cb9fa`](https://github.com/jumpstarter-dev/jumpstarter/commit/a08cb9fa))
- fix: clean up listenQueues entry on listener disconnect ([#397](https://github.com/jumpstarter-dev/jumpstarter/pull/397)) ([`4a10a94b`](https://github.com/jumpstarter-dev/jumpstarter/commit/4a10a94b))
- fix: reset retry counter after receiving data in exporter reconnect ([#396](https://github.com/jumpstarter-dev/jumpstarter/pull/396)) ([`933f5337`](https://github.com/jumpstarter-dev/jumpstarter/commit/933f5337))
- Android ADB and Emulator Power Drivers ([#403](https://github.com/jumpstarter-dev/jumpstarter/pull/403)) ([`fba1b25d`](https://github.com/jumpstarter-dev/jumpstarter/commit/fba1b25d))
- fix: wire keepalive configuration fields into gRPC server options ([#399](https://github.com/jumpstarter-dev/jumpstarter/pull/399)) ([`647aff95`](https://github.com/jumpstarter-dev/jumpstarter/commit/647aff95))
- fix(authorization): always derive resource names from OIDC username ([#404](https://github.com/jumpstarter-dev/jumpstarter/pull/404)) ([`c043a331`](https://github.com/jumpstarter-dev/jumpstarter/commit/c043a331))
- Fix exporter state machine: stuck exporters on lease-end during hooks ([#349](https://github.com/jumpstarter-dev/jumpstarter/pull/349)) ([`d6cad897`](https://github.com/jumpstarter-dev/jumpstarter/commit/d6cad897))
- shell: keep shell usable after token refreshes ([`6d062ec7`](https://github.com/jumpstarter-dev/jumpstarter/commit/6d062ec7))
- NOYITO USB Relay Power Driver ([#268](https://github.com/jumpstarter-dev/jumpstarter/pull/268)) ([`b39a904b`](https://github.com/jumpstarter-dev/jumpstarter/commit/b39a904b))
- e2e: dump debug logs on test failure ([#420](https://github.com/jumpstarter-dev/jumpstarter/pull/420)) ([`e3fb3826`](https://github.com/jumpstarter-dev/jumpstarter/commit/e3fb3826))
- ci(e2e): run operator e2e on main push, drop helm matrix ([`0d055197`](https://github.com/jumpstarter-dev/jumpstarter/commit/0d055197))
- Add mitmproxy driver for HTTP(S) interception and backend mocking ([#254](https://github.com/jumpstarter-dev/jumpstarter/pull/254)) ([`f8c71162`](https://github.com/jumpstarter-dev/jumpstarter/commit/f8c71162))
- feat: show expires at and remaining columns in lease listing ([#32](https://github.com/jumpstarter-dev/jumpstarter/pull/32)) ([`c2bbf3f6`](https://github.com/jumpstarter-dev/jumpstarter/commit/c2bbf3f6))
- fix(e2e): set COLUMNS=200 to prevent Rich table header wrapping ([`6e75343d`](https://github.com/jumpstarter-dev/jumpstarter/commit/6e75343d))
- fix: address review feedback for lease expiration display ([`99dd5021`](https://github.com/jumpstarter-dev/jumpstarter/commit/99dd5021))
- Revert "fix: address review feedback for lease expiration display" ([`558584e1`](https://github.com/jumpstarter-dev/jumpstarter/commit/558584e1))
- fix: use timedelta .days and .seconds fields for remaining time formatting ([`402785b7`](https://github.com/jumpstarter-dev/jumpstarter/commit/402785b7))
- docs: add note about ingress-nginx SSL passthrough requirement ([`b9907977`](https://github.com/jumpstarter-dev/jumpstarter/commit/b9907977))
- fix: prevent lease assignment to non-ready exporters ([#426](https://github.com/jumpstarter-dev/jumpstarter/pull/426)) ([`c562cf2d`](https://github.com/jumpstarter-dev/jumpstarter/commit/c562cf2d))
- test(e2e): use -o name and wc -l for pagination count checks ([#419](https://github.com/jumpstarter-dev/jumpstarter/pull/419)) ([`9fdd1ad3`](https://github.com/jumpstarter-dev/jumpstarter/commit/9fdd1ad3))
- Restrict operator ClusterRole RBAC permissions on roles/rolebindings ([`042ef521`](https://github.com/jumpstarter-dev/jumpstarter/commit/042ef521))
- feat: consolidate TLS flags into single --insecure option ([#333](https://github.com/jumpstarter-dev/jumpstarter/pull/333)) ([`b37e463c`](https://github.com/jumpstarter-dev/jumpstarter/commit/b37e463c))
- fix(shell): block dangerous environment variables in shell driver ([`331f665e`](https://github.com/jumpstarter-dev/jumpstarter/commit/331f665e))
- fix(operator): restart controller pods when configmap changes ([`e1b64f9d`](https://github.com/jumpstarter-dev/jumpstarter/commit/e1b64f9d))
- fix(hooks): emit WARNING log inside context_log_source for client visibility ([`76755c62`](https://github.com/jumpstarter-dev/jumpstarter/commit/76755c62))
- docs: add kubeconfig mount to container run examples ([#437](https://github.com/jumpstarter-dev/jumpstarter/pull/437)) ([`75b75373`](https://github.com/jumpstarter-dev/jumpstarter/commit/75b75373))
- fix(deps): update Go dependencies to resolve known CVEs ([#447](https://github.com/jumpstarter-dev/jumpstarter/pull/447)) ([`3d5ffa7e`](https://github.com/jumpstarter-dev/jumpstarter/commit/3d5ffa7e))
- Convert bats E2E tests to Go + Ginkgo ([#439](https://github.com/jumpstarter-dev/jumpstarter/pull/439)) ([`e044198a`](https://github.com/jumpstarter-dev/jumpstarter/commit/e044198a))
- Fix swapped voltage/current in dutlink power readings ([`88966719`](https://github.com/jumpstarter-dev/jumpstarter/commit/88966719))
- Add server-side Dial retry for transient Available status ([`2264a0d2`](https://github.com/jumpstarter-dev/jumpstarter/commit/2264a0d2))
- Use exponential backoff for Dial retry instead of fixed interval ([`f400b218`](https://github.com/jumpstarter-dev/jumpstarter/commit/f400b218))
- Add tests for BLE driver ([#536](https://github.com/jumpstarter-dev/jumpstarter/pull/536)) ([`88b9c4fa`](https://github.com/jumpstarter-dev/jumpstarter/commit/88b9c4fa))
- Fix nginx ingress for e2e tests in kind clusters ([`b4bd9806`](https://github.com/jumpstarter-dev/jumpstarter/commit/b4bd9806))
- ci: parallelize e2e container image and wheel builds ([`d7a65e02`](https://github.com/jumpstarter-dev/jumpstarter/commit/d7a65e02))
- Increase Dial retry timeout from 10s to 30s to fix E2E flake ([`f473ede1`](https://github.com/jumpstarter-dev/jumpstarter/commit/f473ede1))
- improve error message in ridesx flashing ([#543](https://github.com/jumpstarter-dev/jumpstarter/pull/543)) ([`46de464c`](https://github.com/jumpstarter-dev/jumpstarter/commit/46de464c))
- feat: add SOME/IP driver wrapping opensomeip Python binding ([#391](https://github.com/jumpstarter-dev/jumpstarter/pull/391)) ([`31d9cf3a`](https://github.com/jumpstarter-dev/jumpstarter/commit/31d9cf3a))
- Add Renode emulator driver for embedded target simulation ([#533](https://github.com/jumpstarter-dev/jumpstarter/pull/533)) ([`e8e97319`](https://github.com/jumpstarter-dev/jumpstarter/commit/e8e97319))
- docs/ci: Renode driver listing and conditional macOS Renode install ([#557](https://github.com/jumpstarter-dev/jumpstarter/pull/557)) ([`7d06f2e7`](https://github.com/jumpstarter-dev/jumpstarter/commit/7d06f2e7))
- fix(renode): address review follow-ups from PR #533 ([#558](https://github.com/jumpstarter-dev/jumpstarter/pull/558)) ([`4ba92e88`](https://github.com/jumpstarter-dev/jumpstarter/commit/4ba92e88))
- ci: add quality gates for coverage and type checking ([#427](https://github.com/jumpstarter-dev/jumpstarter/pull/427)) ([`fcbbfbfa`](https://github.com/jumpstarter-dev/jumpstarter/commit/fcbbfbfa))
- fix: resolve ty type diagnostics and add CI quality gates ([#568](https://github.com/jumpstarter-dev/jumpstarter/pull/568)) ([`a64be234`](https://github.com/jumpstarter-dev/jumpstarter/commit/a64be234))
- Fix exporter deadlock when lease ends before before_lease_hook is set ([#569](https://github.com/jumpstarter-dev/jumpstarter/pull/569)) ([`aaf98be5`](https://github.com/jumpstarter-dev/jumpstarter/commit/aaf98be5))
- Update install.sh default source from release-0.7 to release-0.8 ([`1c0a3d92`](https://github.com/jumpstarter-dev/jumpstarter/commit/1c0a3d92))
- fix: drain remaining PTY data after reader stop on macOS ([#561](https://github.com/jumpstarter-dev/jumpstarter/pull/561)) ([`74794de3`](https://github.com/jumpstarter-dev/jumpstarter/commit/74794de3))
- fix(someip): defer OsipClient creation to first use ([#595](https://github.com/jumpstarter-dev/jumpstarter/pull/595)) ([`f92ac89f`](https://github.com/jumpstarter-dev/jumpstarter/commit/f92ac89f))
- fix: skip afterLease flow when lease has already expired ([#603](https://github.com/jumpstarter-dev/jumpstarter/pull/603)) ([`bdb69e9d`](https://github.com/jumpstarter-dev/jumpstarter/commit/bdb69e9d))
- Fix: log UNIMPLEMENTED gRPC errors from ReportStatus as warning ([#620](https://github.com/jumpstarter-dev/jumpstarter/pull/620)) ([`4a61fe5b`](https://github.com/jumpstarter-dev/jumpstarter/commit/4a61fe5b))
- feat(someip): support static remote endpoint (no Service Discovery) ([#621](https://github.com/jumpstarter-dev/jumpstarter/pull/621)) ([`d3a4a968`](https://github.com/jumpstarter-dev/jumpstarter/commit/d3a4a968))
- jumpstarter-driver-gpio: Add read method required by PowerInterface ([`f0ba0c1d`](https://github.com/jumpstarter-dev/jumpstarter/commit/f0ba0c1d))
- fix(someip): use opensomeip PyPI release instead of git URL ([`d6ffbf38`](https://github.com/jumpstarter-dev/jumpstarter/commit/d6ffbf38))
- fix: lease transfer error ([`82559668`](https://github.com/jumpstarter-dev/jumpstarter/commit/82559668))
- Add Jumpstarter Enhancement Proposal (JEP) process and docs ([#423](https://github.com/jumpstarter-dev/jumpstarter/pull/423)) ([`49576004`](https://github.com/jumpstarter-dev/jumpstarter/commit/49576004))
- fix: replace httpbin.org with local server in mitmproxy passthrough test ([`7a174e78`](https://github.com/jumpstarter-dev/jumpstarter/commit/7a174e78))
- feat: add ST-LINK mass storage flasher driver for STM32 boards ([`96aa730b`](https://github.com/jumpstarter-dev/jumpstarter/commit/96aa730b))
- fix: address PR review feedback ([`b19d6327`](https://github.com/jumpstarter-dev/jumpstarter/commit/b19d6327))
- qemu: add OCI flashing to qemu driver ([#555](https://github.com/jumpstarter-dev/jumpstarter/pull/555)) ([`9a812c15`](https://github.com/jumpstarter-dev/jumpstarter/commit/9a812c15))
- feat: add tags field to lease, to allow user set metadata ([#622](https://github.com/jumpstarter-dev/jumpstarter/pull/622)) ([`947d4b99`](https://github.com/jumpstarter-dev/jumpstarter/commit/947d4b99))
- feat: add DUT network isolation driver ([#642](https://github.com/jumpstarter-dev/jumpstarter/pull/642)) ([`82e91875`](https://github.com/jumpstarter-dev/jumpstarter/commit/82e91875))
- oci: support existing OCI credentials ([`abd5d919`](https://github.com/jumpstarter-dev/jumpstarter/commit/abd5d919))
- fix: address PR #642 review follow-ups for dut-network driver ([#653](https://github.com/jumpstarter-dev/jumpstarter/pull/653)) ([`e6a75404`](https://github.com/jumpstarter-dev/jumpstarter/commit/e6a75404))
- fix: guard beforeLease hook from setting LEASE_READY after lease expiry ([#655](https://github.com/jumpstarter-dev/jumpstarter/pull/655)) ([`7159a21a`](https://github.com/jumpstarter-dev/jumpstarter/commit/7159a21a))
- JEP-0013: Metrics, Tracing, and Log Observability ([`fab3f062`](https://github.com/jumpstarter-dev/jumpstarter/commit/fab3f062))
- JEP-0011 proposal ([`509db8d0`](https://github.com/jumpstarter-dev/jumpstarter/commit/509db8d0))
- fix(iscsi): block device allowlist confinement ([#432](https://github.com/jumpstarter-dev/jumpstarter/pull/432)) ([`6a510dab`](https://github.com/jumpstarter-dev/jumpstarter/commit/6a510dab))
- fix(container): add procps-ng for sysctl in dut-network driver ([`7864f393`](https://github.com/jumpstarter-dev/jumpstarter/commit/7864f393))
- fix(dut-network): make add_ip_alias idempotent to prevent loop/crash ([`75ef6843`](https://github.com/jumpstarter-dev/jumpstarter/commit/75ef6843))
- feat(dut-network): rename static_leases to addresses, allow MAC-less entries ([`38a6f72a`](https://github.com/jumpstarter-dev/jumpstarter/commit/38a6f72a))
- docs(dut-network): update README for addresses field and add-address/remove-address commands ([`e8362558`](https://github.com/jumpstarter-dev/jumpstarter/commit/e8362558))
- fix(e2e): update DUT network e2e tests for addresses/add-address rename ([`00bf16fc`](https://github.com/jumpstarter-dev/jumpstarter/commit/00bf16fc))
- feat(dut-network): add local NTP responder support ([#667](https://github.com/jumpstarter-dev/jumpstarter/pull/667)) ([`8e3ccb74`](https://github.com/jumpstarter-dev/jumpstarter/commit/8e3ccb74))
- fix: release serial port before ESP32 flash to prevent port-locked errors ([#659](https://github.com/jumpstarter-dev/jumpstarter/pull/659)) ([`a258f02b`](https://github.com/jumpstarter-dev/jumpstarter/commit/a258f02b))
- feat(dut-network): allow DNS hostnames in public_ip field ([#672](https://github.com/jumpstarter-dev/jumpstarter/pull/672)) ([`26094726`](https://github.com/jumpstarter-dev/jumpstarter/commit/26094726))
- dut-network: add tcpdump streaming support ([#674](https://github.com/jumpstarter-dev/jumpstarter/pull/674)) ([`abfaebab`](https://github.com/jumpstarter-dev/jumpstarter/commit/abfaebab))
- dut-network: enable dhcp-sequential-ip in dnsmasq by default ([`288da40c`](https://github.com/jumpstarter-dev/jumpstarter/commit/288da40c))
- fix: preserve percent-encoding in presigned URLs to prevent signature invalidation ([#662](https://github.com/jumpstarter-dev/jumpstarter/pull/662)) ([`72e0ed8a`](https://github.com/jumpstarter-dev/jumpstarter/commit/72e0ed8a))
- fix: remove Helm charts and standardize on operator-based deployment ([#448](https://github.com/jumpstarter-dev/jumpstarter/pull/448)) ([`1d056d3d`](https://github.com/jumpstarter-dev/jumpstarter/commit/1d056d3d))
- chore: remove obsolete migration script and leftover poetry.lock ([#683](https://github.com/jumpstarter-dev/jumpstarter/pull/683)) ([`ea9a9f49`](https://github.com/jumpstarter-dev/jumpstarter/commit/ea9a9f49))
- chore: enable ruff ERA rule to catch commented-out code ([#684](https://github.com/jumpstarter-dev/jumpstarter/pull/684)) ([`0c3d504d`](https://github.com/jumpstarter-dev/jumpstarter/commit/0c3d504d))
- fix: detect original_url before operator guard to fix encoding with explicit operators ([`a0dbedbc`](https://github.com/jumpstarter-dev/jumpstarter/commit/a0dbedbc))
- fix: clean up dut-network state directory on driver close/reset ([`954095f3`](https://github.com/jumpstarter-dev/jumpstarter/commit/954095f3))
- feat(dut-network): add egress and ingress traffic filtering ([#686](https://github.com/jumpstarter-dev/jumpstarter/pull/686)) ([`04891f62`](https://github.com/jumpstarter-dev/jumpstarter/commit/04891f62))
- fix: detect exporter rapid failure loop and exit for container restart ([`e7371292`](https://github.com/jumpstarter-dev/jumpstarter/commit/e7371292))
- fix: replace nonlocal with mutable container to satisfy ty type-checker ([`63425f95`](https://github.com/jumpstarter-dev/jumpstarter/commit/63425f95))
- refactor: move rapid failure config from env vars to exporter config ([`c22dfe8a`](https://github.com/jumpstarter-dev/jumpstarter/commit/c22dfe8a))
- feat: add internal token rotation for clients ([`eedff884`](https://github.com/jumpstarter-dev/jumpstarter/commit/eedff884))
- e2e: add token rotation tests ([`28e4a599`](https://github.com/jumpstarter-dev/jumpstarter/commit/28e4a599))
- docs: consolidate and improve documentation across the repository ([#693](https://github.com/jumpstarter-dev/jumpstarter/pull/693)) ([`7a765052`](https://github.com/jumpstarter-dev/jumpstarter/commit/7a765052))
- fix: fix ridesx local path upload ([`12a99ae7`](https://github.com/jumpstarter-dev/jumpstarter/commit/12a99ae7))
- fix: small fixes batch (#516, #525, #652, #517) ([#681](https://github.com/jumpstarter-dev/jumpstarter/pull/681)) ([`4e27c7ea`](https://github.com/jumpstarter-dev/jumpstarter/commit/4e27c7ea))
- feat(docs): self-host asciinema player to remove branding ([#705](https://github.com/jumpstarter-dev/jumpstarter/pull/705)) ([`ae354e5e`](https://github.com/jumpstarter-dev/jumpstarter/commit/ae354e5e))
- docs: add gRPC protocol reference and consistent field descriptions ([#703](https://github.com/jumpstarter-dev/jumpstarter/pull/703)) ([`6b284332`](https://github.com/jumpstarter-dev/jumpstarter/commit/6b284332))
- docs: align README and docs landing page with org profile ([#706](https://github.com/jumpstarter-dev/jumpstarter/pull/706)) ([`16527330`](https://github.com/jumpstarter-dev/jumpstarter/commit/16527330))
- docs: fix README link titles and CRDs ToC in multiversion build ([#702](https://github.com/jumpstarter-dev/jumpstarter/pull/702)) ([`a01296b8`](https://github.com/jumpstarter-dev/jumpstarter/commit/a01296b8))
- fix: preserve URL query parameters in storage flash for signed URLs ([#435](https://github.com/jumpstarter-dev/jumpstarter/pull/435)) ([`5c87e0c1`](https://github.com/jumpstarter-dev/jumpstarter/commit/5c87e0c1))
- fix: remove unreliable energenie.com link to prevent flaky CI ([#707](https://github.com/jumpstarter-dev/jumpstarter/pull/707)) ([`92e98f4f`](https://github.com/jumpstarter-dev/jumpstarter/commit/92e98f4f))
- ci: skip e2e and python tests for docs-only changes ([#708](https://github.com/jumpstarter-dev/jumpstarter/pull/708)) ([`7c67bd0c`](https://github.com/jumpstarter-dev/jumpstarter/commit/7c67bd0c))
- Add jumpstarter-driver-ssh-mount package for remote filesystem mounting ([#434](https://github.com/jumpstarter-dev/jumpstarter/pull/434)) ([`33ad352f`](https://github.com/jumpstarter-dev/jumpstarter/commit/33ad352f))
- oci: add OciCredentials model and harden credential resolution ([#709](https://github.com/jumpstarter-dev/jumpstarter/pull/709)) ([`fbf73a69`](https://github.com/jumpstarter-dev/jumpstarter/commit/fbf73a69))
- fix: serialize Dial/Listen queue handoff to prevent router token loss ([#573](https://github.com/jumpstarter-dev/jumpstarter/pull/573)) ([`e914a829`](https://github.com/jumpstarter-dev/jumpstarter/commit/e914a829))
- fix: set default resource requests/limits for controller and router pods ([#714](https://github.com/jumpstarter-dev/jumpstarter/pull/714)) ([`616a4014`](https://github.com/jumpstarter-dev/jumpstarter/commit/616a4014))
- fix(someip): fail fast when opensomeip native extension is unavailable ([#629](https://github.com/jumpstarter-dev/jumpstarter/pull/629)) ([`d706fa73`](https://github.com/jumpstarter-dev/jumpstarter/commit/d706fa73))
- Update opensomeip dependency to 0.1.5 ([#716](https://github.com/jumpstarter-dev/jumpstarter/pull/716)) ([`9c3e7e05`](https://github.com/jumpstarter-dev/jumpstarter/commit/9c3e7e05))
- fix: retry Dial and StatusMonitor poll on transient UNAVAILABLE ([#606](https://github.com/jumpstarter-dev/jumpstarter/pull/606)) ([`ad6f91b7`](https://github.com/jumpstarter-dev/jumpstarter/commit/ad6f91b7))
- feat: default exporter configs to ~/.config/jumpstarter ([#712](https://github.com/jumpstarter-dev/jumpstarter/pull/712)) ([`c07c4707`](https://github.com/jumpstarter-dev/jumpstarter/commit/c07c4707))
- ci: make linkcheck job non-blocking for PRs ([#729](https://github.com/jumpstarter-dev/jumpstarter/pull/729)) ([`54e3385c`](https://github.com/jumpstarter-dev/jumpstarter/commit/54e3385c))
- ci: extract e2e artifact loading into a reusable composite action ([`fa2f5763`](https://github.com/jumpstarter-dev/jumpstarter/commit/fa2f5763))
- Remove opendal dependency from QEMU driver ([#535](https://github.com/jumpstarter-dev/jumpstarter/pull/535)) ([`e654084d`](https://github.com/jumpstarter-dev/jumpstarter/commit/e654084d))
- fix: guard DurationParamType against OverflowError on large values ([#722](https://github.com/jumpstarter-dev/jumpstarter/pull/722)) ([`c1413322`](https://github.com/jumpstarter-dev/jumpstarter/commit/c1413322))
- fix: use select() in PTY drain loop to prevent output loss on macOS ([#733](https://github.com/jumpstarter-dev/jumpstarter/pull/733)) ([#734](https://github.com/jumpstarter-dev/jumpstarter/pull/734)) ([`99c3cfed`](https://github.com/jumpstarter-dev/jumpstarter/commit/99c3cfed))
- ci: collect controller and router logs from e2e runs ([#730](https://github.com/jumpstarter-dev/jumpstarter/pull/730)) ([`e798cd04`](https://github.com/jumpstarter-dev/jumpstarter/commit/e798cd04))
- fix: include exporter/client name and namespace in auth error messages ([#726](https://github.com/jumpstarter-dev/jumpstarter/pull/726)) ([`9b574664`](https://github.com/jumpstarter-dev/jumpstarter/commit/9b574664))
- fix: raise TypeError in V1Alpha1Lease.from_dict for non-dict spec ([#723](https://github.com/jumpstarter-dev/jumpstarter/pull/723)) ([`3ed3c3f0`](https://github.com/jumpstarter-dev/jumpstarter/commit/3ed3c3f0))
- fix: deduplicate NotIn values in ParseLabelSelector ([#741](https://github.com/jumpstarter-dev/jumpstarter/pull/741)) ([`00bf61a9`](https://github.com/jumpstarter-dev/jumpstarter/commit/00bf61a9))
- Replace Dependabot with Renovate for cross-ecosystem dependency grouping ([#745](https://github.com/jumpstarter-dev/jumpstarter/pull/745)) ([`04b5b7b4`](https://github.com/jumpstarter-dev/jumpstarter/commit/04b5b7b4))
- fix: pin uv container image and fix docs substitutions ([#749](https://github.com/jumpstarter-dev/jumpstarter/pull/749)) ([`536ef4ac`](https://github.com/jumpstarter-dev/jumpstarter/commit/536ef4ac))
- fix: HttpServer.close() properly releases port on cleanup ([#740](https://github.com/jumpstarter-dev/jumpstarter/pull/740)) ([`8f79b678`](https://github.com/jumpstarter-dev/jumpstarter/commit/8f79b678))
- feat: expose gRPC health checking protocol on the controller ([#747](https://github.com/jumpstarter-dev/jumpstarter/pull/747)) ([`4e22dfff`](https://github.com/jumpstarter-dev/jumpstarter/commit/4e22dfff))
- feat: make INSTALL_DIR and VENV_DIR configurable via environment variables ([#771](https://github.com/jumpstarter-dev/jumpstarter/pull/771)) ([`e60a3d7b`](https://github.com/jumpstarter-dev/jumpstarter/commit/e60a3d7b))
- fix: consolidate Renovate Go groups and restrict Fedora to stable ([#773](https://github.com/jumpstarter-dev/jumpstarter/pull/773)) ([`eec0278c`](https://github.com/jumpstarter-dev/jumpstarter/commit/eec0278c))
- fix: restrict Fedora to stable and create go-toolchain group ([#777](https://github.com/jumpstarter-dev/jumpstarter/pull/777)) ([`1cebbd0e`](https://github.com/jumpstarter-dev/jumpstarter/commit/1cebbd0e))
- fix: remove stale e2e section from README ([#752](https://github.com/jumpstarter-dev/jumpstarter/pull/752)) ([`c88b979f`](https://github.com/jumpstarter-dev/jumpstarter/commit/c88b979f))
- fix: use {doc} role for symlinked doc references for Sphinx 8.2 ([#778](https://github.com/jumpstarter-dev/jumpstarter/pull/778)) ([`a1477390`](https://github.com/jumpstarter-dev/jumpstarter/commit/a1477390))
- fix: skip uninstalled child drivers in composite CLI ([#785](https://github.com/jumpstarter-dev/jumpstarter/pull/785)) ([`24473f34`](https://github.com/jumpstarter-dev/jumpstarter/commit/24473f34))
- feat: add jumpstarter-driver-obd for OBD-II vehicle diagnostics ([#789](https://github.com/jumpstarter-dev/jumpstarter/pull/789)) ([`ffd9cc1d`](https://github.com/jumpstarter-dev/jumpstarter/commit/ffd9cc1d))
- fix: drop container image digest pinning, unify uv version, bump Fedora to 44 ([#781](https://github.com/jumpstarter-dev/jumpstarter/pull/781)) ([`590da7b4`](https://github.com/jumpstarter-dev/jumpstarter/commit/590da7b4))
- fix: construct image URL after HTTP server starts ([#797](https://github.com/jumpstarter-dev/jumpstarter/pull/797)) ([`8597d199`](https://github.com/jumpstarter-dev/jumpstarter/commit/8597d199))
- feat: add description field to access policy rules ([#803](https://github.com/jumpstarter-dev/jumpstarter/pull/803)) ([`2d748d07`](https://github.com/jumpstarter-dev/jumpstarter/commit/2d748d07))
- Add exporter context env vars to jmp shell and env_with_metadata() helper ([`d074b997`](https://github.com/jumpstarter-dev/jumpstarter/commit/d074b997))
- ci: move linkcheck to weekly scheduled workflow ([#813](https://github.com/jumpstarter-dev/jumpstarter/pull/813)) ([`e7bc41c9`](https://github.com/jumpstarter-dev/jumpstarter/commit/e7bc41c9))
- flashers: add default CA to be inject into flashing command ([#742](https://github.com/jumpstarter-dev/jumpstarter/pull/742)) ([`a235026f`](https://github.com/jumpstarter-dev/jumpstarter/commit/a235026f))
- feat: driver/sdwire: support unprogrammed FT200X EEPROM + macOS storage/mux fixes ([#748](https://github.com/jumpstarter-dev/jumpstarter/pull/748)) ([`c6b0748f`](https://github.com/jumpstarter-dev/jumpstarter/commit/c6b0748f))
- CI: per-package test logs, parallel runs, and uboot OOM fix ([#815](https://github.com/jumpstarter-dev/jumpstarter/pull/815)) ([`861e6819`](https://github.com/jumpstarter-dev/jumpstarter/commit/861e6819))
- feat: add Secret/ConfigMap references for JWT CA certificates ([#772](https://github.com/jumpstarter-dev/jumpstarter/pull/772)) ([`16511650`](https://github.com/jumpstarter-dev/jumpstarter/commit/16511650))
- fix(container): pin UV binary to build platform in cross-compilation stages ([#819](https://github.com/jumpstarter-dev/jumpstarter/pull/819)) ([`96a6c7b8`](https://github.com/jumpstarter-dev/jumpstarter/commit/96a6c7b8))
- docs(jep-0014): simplified virtual exporter design ([#744](https://github.com/jumpstarter-dev/jumpstarter/pull/744)) ([`a3bcb14c`](https://github.com/jumpstarter-dev/jumpstarter/commit/a3bcb14c))
- fix(ci): use merge commit ref in backport workflow for fork PRs ([#820](https://github.com/jumpstarter-dev/jumpstarter/pull/820)) ([`51971c26`](https://github.com/jumpstarter-dev/jumpstarter/commit/51971c26))
- fix(controller): fix Containerfile build with rootless Podman ([#822](https://github.com/jumpstarter-dev/jumpstarter/pull/822)) ([`c7c0ec96`](https://github.com/jumpstarter-dev/jumpstarter/commit/c7c0ec96))
- fix(ble): fix race condition in test_ble_driver_connect_stream ([#825](https://github.com/jumpstarter-dev/jumpstarter/pull/825)) ([`85542630`](https://github.com/jumpstarter-dev/jumpstarter/commit/85542630))
- fix(hooks): release lease when beforeLease hook fails with onFailure=endLease ([#823](https://github.com/jumpstarter-dev/jumpstarter/pull/823)) ([`4a5d98ea`](https://github.com/jumpstarter-dev/jumpstarter/commit/4a5d98ea))
- fix: retry PTY drain on empty select() to prevent data loss on macOS ([#826](https://github.com/jumpstarter-dev/jumpstarter/pull/826)) ([`86c308e0`](https://github.com/jumpstarter-dev/jumpstarter/commit/86c308e0))
- ci: reduce PR test matrix, run full matrix in merge queue ([#830](https://github.com/jumpstarter-dev/jumpstarter/pull/830)) ([`b38ff9ac`](https://github.com/jumpstarter-dev/jumpstarter/commit/b38ff9ac))
- fix: use shared uv cache for package tests ([#832](https://github.com/jumpstarter-dev/jumpstarter/pull/832)) ([`8cc61e1e`](https://github.com/jumpstarter-dev/jumpstarter/commit/8cc61e1e))
- ci: stop re-running validation workflows on push to main ([#834](https://github.com/jumpstarter-dev/jumpstarter/pull/834)) ([`f9d6f934`](https://github.com/jumpstarter-dev/jumpstarter/commit/f9d6f934))
- update fls to 0.3.0 ([#838](https://github.com/jumpstarter-dev/jumpstarter/pull/838)) ([`c29eece3`](https://github.com/jumpstarter-dev/jumpstarter/commit/c29eece3))
- use 0.3.0 default for CLI ([#839](https://github.com/jumpstarter-dev/jumpstarter/pull/839)) ([`a899a449`](https://github.com/jumpstarter-dev/jumpstarter/commit/a899a449))
- feat: parse power measurements in http-power and add a power read CLI command ([#790](https://github.com/jumpstarter-dev/jumpstarter/pull/790)) ([`96710823`](https://github.com/jumpstarter-dev/jumpstarter/commit/96710823))
- test: mark PTY-dependent hooks tests as xfail on macOS ([#821](https://github.com/jumpstarter-dev/jumpstarter/pull/821)) ([#836](https://github.com/jumpstarter-dev/jumpstarter/pull/836)) ([`70a5e3e1`](https://github.com/jumpstarter-dev/jumpstarter/commit/70a5e3e1))
- fix: retry connection when exporter is temporarily unavailable ([#829](https://github.com/jumpstarter-dev/jumpstarter/pull/829)) ([`26aee227`](https://github.com/jumpstarter-dev/jumpstarter/commit/26aee227))
- chore: bump operator version to 0.9.0-rc.1 ([`78777b5a`](https://github.com/jumpstarter-dev/jumpstarter/commit/78777b5a))
- fix: allow skipping interactive confirmation in contribute script ([`28bc684b`](https://github.com/jumpstarter-dev/jumpstarter/commit/28bc684b))

