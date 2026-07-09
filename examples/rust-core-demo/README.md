# Jumpstarter Rust-core demo

A four-act community demo. One message: **the entire Jumpstarter core is now Rust — but nothing
your users rely on changed, and it unlocks true multi-language.** Everything runs on a Mac
laptop: a local kind cluster (podman/docker) with the **Rust controller + router**, exporters as
local `jmp run` processes, and a real (small) QEMU VM as the DUT.

| Act | Shows | Client / test | Driver(s) | DUT |
|-----|-------|---------------|-----------|-----|
| 1 | "Nothing changed" | Python `pytest` | Python (QEMU) | real QEMU VM |
| 2 | Multi-language clients | Kotlin / JUnit + **generated** client | Python `MockPower` | mock |
| 3 | Polyglot exporter | native `j` CLI + a **Rust `cargo test`** | **Python + Rust + Java** at once | mock |
| 4 | Reveal + backwards-compat | **old 0.7.4** PyPI client | Python on a **Rust-core** exporter | mock |

Tests exist in all three languages, each visible in its act's directory: `test_qemu_boot.py`
(Act 1), `src/PowerNativeIT.kt` (Act 2), `power_test.rs` (Act 3).

The controller/router are Rust from Act 1 — **don't reveal that until Act 4.**

Each act has its own `README.md` with the exact two-terminal run steps. This top-level file is the
bring-up + rehearsal + narration guide.

---

## Prerequisites (all present on the demo Mac)

podman (or docker) with a running machine, `kind`, `kubectl`, `qemu-system-aarch64` + `qemu-img`,
`uv`, a Rust toolchain (`cargo`), and JDK 21. No `gradle`/`yq` needed (we use `./gradlew` and
PyYAML). `sudo` is used once to create `/etc/jumpstarter/exporters`.

> The demo uses **internal/unsafe auth (no dex)** and the operator's **self-signed TLS** with
> `-k` (insecure) client/exporter configs — so bring-up is a single script, and the old 0.7.4
> client in Act 4 works too.

---

## One-time bring-up

```bash
cd <repo-root>
bash examples/rust-core-demo/cluster/up.sh
```

What it does (see `cluster/up.sh`):

1. `make rust-controller-image` — builds the Rust controller/router image (podman). **Cold Rust
   compile; pre-warm during rehearsal** (`SKIP_IMAGE=1` reuses a cached image).
2. `make -C controller deploy` with `CONTROLLER_IMG`/`ROUTER_IMG` = the Rust image (the
   `deploy_vars` seam) — creates/uses the kind cluster and deploys the operator + Jumpstarter CR
   pointing controller/router at the **Rust** image. `IP=127.0.0.1` pins the nip.io baseDomain to
   loopback so kind's published nodeports resolve on macOS.
3. `make -C python sync` — builds the native `jmp`/`j` CLIs + the `jumpstarter_core` extension into
   `python/.venv` and installs all `jumpstarter-*` packages.
4. Creates the `demo-client` identity and the four exporter configs (`demo-qemu`, `demo-mock`,
   `demo-polyglot`, `demo-compat`) in `/etc/jumpstarter/exporters/`.

Then activate the venv for everything else:

```bash
source python/.venv/bin/activate     # gives you jmp, j, pytest, gradlew helpers
kubectl -n jumpstarter-lab get pods  # controller + router Running
```

There is already a kind cluster named `jumpstarter` on this machine; `up.sh` reuses it and just
redeploys the controller as Rust. To start clean: `bash cluster/down.sh --cluster` first.

---

## Run order (the show)

Every act is two terminals and two scripts — `serve.sh` (host the exporter) ‖ `run.sh` (lease it
and run the test). The scripts and the test sources live **in each act's directory**, so the
audience can read exactly what's being run. One-time preps are noted; details in each act README.

- **Act 1** — `act1-python-qemu/`: the test is `test_qemu_boot.py`.
  Prep once: `bash act1-python-qemu/fetch-image.sh`. Then `./serve.sh` ‖ `DEBUG_CONSOLE=1 ./run.sh`
- **Act 2** — `act2-kotlin-python/`: the test is `src/PowerNativeIT.kt` (compiled by the gradle
  module via an external srcDir — this file IS what runs). `./serve.sh` ‖ `./run.sh`
- **Act 3** — `act3-polyglot/`: the drivers are declared in `exporter.yaml`; `run.sh` first tours
  them with `j`, then runs the native Rust test `power_test.rs` (compiled by
  `jumpstarter-driver-power-pure-client` via a `#[path]` shim — this file IS what runs).
  Prep once: `bash act3-polyglot/build-hosts.sh`. Then `./serve.sh` ‖ `./run.sh`
- **Act 4** — `act4-backcompat/`: reveal the Rust image (see README), then
  prep once: `bash act4-backcompat/install-old-client.sh`. Then `./serve.sh` ‖ `./run.sh`

---

## Rehearsal checklist (do the night before)

The demo touches a lot of first-time compilation. Warm every cache so nothing builds live:

```bash
# 1) controller image + cluster + venv (the whole bring-up)
bash examples/rust-core-demo/cluster/up.sh

# 2) Act 1 assets + a standalone QEMU boot smoke-test (watch for a login: prompt)
bash examples/rust-core-demo/act1-python-qemu/fetch-image.sh

# 3) Act 2/3 JVM + Rust hosts (gradle shells out to cargo; first build is slow)
( cd java && ./gradlew :jumpstarter-driver-power-example:installDist \
    :jumpstarter-driver-power-example:integrationTest -x test )   # build only
( cd rust && cargo build -p jumpstarter-driver-example --bin jmp-rust-host )  # workspace is under rust/

# 4) Act 4 old client
bash examples/rust-core-demo/act4-backcompat/install-old-client.sh

# 5) dry-run all four acts end-to-end, then re-run to confirm repeatability
```

---

## Risks & fallbacks

- **QEMU is TCG (software) on macOS** — the driver adds no hvf/kvm accel. Keep the image small;
  first boot is a minute or two. Act 1's test timeout is generous (300s). If `login:` never
  appears, the image may lack a serial getty — swap images via `DEMO_IMAGE_URL=...`.
- **Act 3's three-language exporter is verified working live** (`j pypower/rustpower/jvmpower on`
  all OK through one lease). It depends on two in-tree fixes: per-driver descriptor pools in
  `jumpstarter-codec/native_table.rs` and the `@JumpstarterDriver` client-label fix in the JVM
  `ConfigDrivenHostFactory` — make sure you run binaries built from this branch. Fallback if
  anything regresses: drop `jvmpower` and demo the Python+Rust pair (also covered by
  `rust/jumpstarter-exporter/tests/polyglot_mixed.rs`).
- **Act 4's old-client → Rust-exporter is verified working live** (0.7.4 `j power on` exits 0).
  It requires the **vendored h2 patch** (`rust/vendor/h2`, one-line tolerance for the old
  client's percent-encoded `:authority`) baked into the exporter binary — anything built from
  this branch has it. Run the old jmp with its venv bin first on PATH. Fallback: the committed
  `e2e/compat` old-client suite — see `act4-backcompat/README.md`.
- **Rust/gradle compile times** everywhere — pre-warm (checklist above).
- **nip.io / ports** — `up.sh` pins `IP=127.0.0.1` so kind's published nodeports resolve on macOS.
  If the controller endpoint is unreachable, confirm the podman/docker machine publishes 8082/8083.
- **Container tool** — `up.sh` defaults to `CONTAINER_TOOL=podman` (+ `KIND_EXPERIMENTAL_PROVIDER=podman`).
  Override with `CONTAINER_TOOL=docker` if kind here uses docker.

---

## Teardown

```bash
bash examples/rust-core-demo/cluster/down.sh            # remove demo client/exporters, keep cluster
bash examples/rust-core-demo/cluster/down.sh --cluster  # also delete the kind cluster
```
