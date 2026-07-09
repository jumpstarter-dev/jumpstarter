# Act 2 — "Same test, now Kotlin": JUnit + generated Kotlin client, Python driver

**Story:** The same on/off/read, but the test is written in **Kotlin with JUnit**, using a
**generated Kotlin client** — against the *same* kind of Python driver. Multi-language, from one
set of Protobuf interfaces, over the same Rust transport.

**The test is right here: [`src/PowerNativeIT.kt`](src/PowerNativeIT.kt)** — it drives the
Python `MockPower` through the generated `PowerClient`. The gradle module
`:jumpstarter-driver-power-example` compiles this directory as an external test srcDir, so the
file you're reading in the demo folder is the exact code that runs (no copy to drift).

- Generated client (regenerated each build, never committed):
  `java/jumpstarter-driver-power-example/build/generated/jumpstarter/clients/PowerClient.kt`

The key point for the audience: **the JVM opens no gRPC socket.** Stock grpc-java stubs run over a
channel that marshals bytes across UniFFI into the Rust core — so grpc-java's usual macOS
Unix-domain-socket pain simply doesn't exist here. The socket is Rust's.

## Prereqs

- `cluster/up.sh` has been run (`demo-mock` exporter created).
- JDK 21 (`java -version`), and the Rust workspace pre-built (the gradle build shells out to
  `cargo` to build the `jumpstarter_core` cdylib, the Kotlin UniFFI bindings, and device codegen).
  **Pre-warm during rehearsal** — see the top-level README's checklist.

## Run

Two terminals:

```bash
./serve.sh   # terminal A: host the Python power driver
./run.sh     # terminal B: lease it + run src/PowerNativeIT.kt via gradle
```

Equivalent by hand (venv active):

```bash
JMP_DRIVERS_ALLOW=UNSAFE jmp run --exporter demo-mock                       # terminal A
cd java && jmp shell --client demo-client --selector example.com/dut=mock -- \
    ./gradlew :jumpstarter-driver-power-example:integrationTest \
    --tests "*PowerNativeIT"                                                # terminal B
```

> Scope to `*PowerNativeIT` — the module also carries `PowerLeaseExtensionTest`, whose assertions
> assume a power driver whose read-back voltage tracks on/off state (real hardware), which the
> mock `MockPower` doesn't model. `PowerNativeIT` is the generated-client-over-UniFFI test for this act.

`jmp shell` sets `JUMPSTARTER_HOST` to the lease tunnel; the `integrationTest` gradle task forwards
it to the test JVM, where `ExporterSession.fromEnv()` connects the Rust `ClientSession` and the
generated `PowerClient` drives `on()`/`off()`/`read()` over the UniFFI channel.

## What to say

- "Same interface, same Python driver — but the test is Kotlin/JUnit and the client is generated
  from the Protobuf interface. One interface definition, many languages."
- "Look closely: there is no JVM gRPC socket. The channel rides the Rust core. All the grpc-java
  transport headaches on macOS just... aren't here."

## Notes

- `integrationTest` runs the `@Tag("integration")` tests (`PowerNativeIT`); the plain `test` task
  excludes them.
- Want a bespoke assertion (e.g. voltages drop to 0 after `off()`)? Edit `PowerNativeIT.kt` — the
  generated `PowerClient` exposes `on()`, `off()`, and `read(): List<PowerReading>`.
- The generated `PowerClient` only appears under `build/` after `compileKotlin` runs; a fresh
  checkout shows nothing until the first build.
