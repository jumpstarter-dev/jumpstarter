# Act 4 — The reveal + backwards compatibility

**Story:** Two reveals.

1. **It was Rust the whole time.** The controller and router you've been leasing through since
   Act 1 are the Rust rewrite:

   ```bash
   kubectl -n jumpstarter-lab get pods -o wide
   kubectl -n jumpstarter-lab get jumpstarter jumpstarter \
       -o jsonpath='{.spec.controller.image}{"\n"}'
   # -> quay.io/jumpstarter-dev/jumpstarter-controller-rust:latest
   ```

2. **Old clients still work.** An **unmodified 0.7.4 client from PyPI** (pre-rewrite Python
   packages) leases through the Rust controller and drives a **new Rust-core exporter**. The old
   client speaks the legacy `DriverCall(uuid, method, args)` protocol; the Rust exporter translates
   it into native per-interface dispatch via the legacy shim
   (`rust/jumpstarter-driver-core/src/legacy.rs`, wired into `jumpstarter-exporter/src/session.rs`).

## Prereqs

- `cluster/up.sh` has been run (`demo-compat` exporter + `demo-client` created).
- Install the old client:

  ```bash
  bash examples/rust-core-demo/act4-backcompat/install-old-client.sh
  # prints OLD_JMP=/tmp/jmp-old/.venv/bin/jmp
  ```

## Run

**Terminal A — host the new Rust-core exporter** (HEAD `jmp run`, Python MockPower driver):

```bash
JMP_DRIVERS_ALLOW=UNSAFE jmp run --exporter demo-compat
```

**Terminal B — drive it with the OLD client.** Put the old venv's `bin` first on PATH (so the
old `jmp` finds ITS OWN `j` and driver-client packages, exactly like a real pre-rewrite user with
their venv activated), and note the old `jmp` takes the command with no `--`:

```bash
export PATH="/tmp/jmp-old/.venv/bin:$PATH"
JUMPSTARTER_GRPC_INSECURE=1 JMP_DRIVERS_ALLOW=UNSAFE \
    jmp shell --client demo-client --selector example.com/dut=compat j power on
echo "exit=$?"   # 0 = the legacy DriverCall round-tripped through the Rust stack
```

The old client reads the very same `demo-client` config the new `jmp admin create client` wrote —
the config format is unchanged. It leases through the Rust controller, tunnels to the Rust
exporter, and its legacy `power.on()` DriverCall is served by the shim.

## What to say

- "Everything you've watched — Python, Kotlin, the polyglot exporter — has been going through a
  Rust controller and router this entire time. Here's the image."
- "And this is a client from before the rewrite, untouched, off PyPI. It leases through the Rust
  controller and drives a Rust-core exporter. The old wire protocol is translated to native
  dispatch on the fly. Nobody has to upgrade in lockstep."

## How this works (and one dependency to know about)

The old grpc-c-core client dials its lease socket as `unix:///path` and — per the gRPC naming
spec — sets the HTTP/2 `:authority` to the percent-encoded socket path. Stock hyper/h2 rejects
`%` in an authority and resets every RPC, so the Rust workspace carries a one-line vendored h2
patch (`rust/vendor/h2`, see its `README-JUMPSTARTER.md`) that treats an unparseable authority
as absent. With that in place this exact live chain (old client → Rust controller → router →
Rust exporter → legacy `DriverCall` shim → Python driver) is verified working — `j power on`
exits 0.

## Fallback (also proven)

If anything misbehaves live, fall back to the **committed compat suite**, which is green in CI
and also proves backwards-compat through the Rust controller (old client **and** old exporter →
new controller):

```bash
# point the compat deploy at the Rust controller image, then run the old-client scenario
export CONTROLLER_IMG=quay.io/jumpstarter-dev/jumpstarter-controller-rust:latest
export ROUTER_IMG=$CONTROLLER_IMG
COMPAT_SCENARIO=old-client bash e2e/compat/setup.sh
make e2e-compat-run COMPAT_TEST=old-client
```

(See `e2e/compat/setup.sh` / `e2e/test/compat_old_client_test.go`.)
