# Polyglot Codegen Example

This example demonstrates JEP-0004's code generation pipeline, producing typed device wrappers from a single ExporterClass definition in four languages: Python, Java, TypeScript, and Rust.

## ExporterClass: `example-board`

```
example-board
  |-- power     (required)  PowerClient         — on/off, streaming reads
  |-- storage   (required)  StorageMuxClient    — host/dut switching, read/write
  `-- network   (optional)  NetworkClient       — bidi byte stream
```

- **power** exercises unary RPCs and server-streaming (PowerReading with voltage/current)
- **storage** exercises typed request messages (Read{dst}, Write{src})
- **network** exercises bidi streaming (@exportstream) and optional interface handling

## Regenerating

```bash
make generate-all
```

This runs `jmp codegen` for each language, reading the ExporterClass from
`example-board-exporterclass.yaml` and resolving DriverInterfaces from the
operator deployment manifests at `controller/deploy/operator/config/driverinterfaces/`.

## Running Tests

All tests require `jmp shell` to provide a connected exporter session:

```bash
# Python
jmp shell --exporter-config <config> -- pytest examples/polyglot/python/

# Java
jmp shell --exporter-config <config> -- cd examples/polyglot/java && ./gradlew test

# TypeScript
jmp shell --exporter-config <config> -- cd examples/polyglot/typescript && npx vitest run

# Rust
jmp shell --exporter-config <config> -- cd examples/polyglot/rust && cargo test
```

## Directory Structure

```
examples/polyglot/
  example-board-exporterclass.yaml   # ExporterClass definition
  Makefile                           # Codegen targets
  python/
    gen/                             # Generated: DeviceWrapper + pytest fixture
    test_example_board.py            # Hand-written example tests
  java/
    gen/                             # Generated: per-interface clients + wrapper
    src/test/.../ExampleBoardTest.java
  typescript/
    gen/                             # Generated: clients + wrapper + Jest helper
    example-board.test.ts
  rust/
    gen/                             # Generated: clients + wrapper + test example
    tests/example_board_test.rs
```
