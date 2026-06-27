# Jumpstarter driver interfaces

This package holds the language-agnostic `.proto` contracts for Jumpstarter
driver interfaces, under `proto/jumpstarter/interfaces/<name>/v1/<name>.proto`.

**These files are generated — do not edit them by hand.** They are derived from
the Python driver interface classes (e.g. `PowerInterface` in
`jumpstarter_driver_power.driver`) by introspecting their `@abstractmethod` /
`@export` methods and type annotations.

## Regenerating

```sh
make -C interfaces generate
```

This runs the generator against the installed driver packages:

```sh
cd python && uv run python -m jumpstarter.driver.proto_gen generate-all \
    --output-dir ../interfaces/proto \
    --import-package jumpstarter_driver_power.driver \
    ...
```

The set of driver modules scanned is configured via `IMPORT_PACKAGES` in the
`Makefile`. To export a new interface, add its `.driver` module there and
regenerate.

## Consuming these protos

- **JVM**: the Gradle build compiles these protos directly via the
  `com.google.protobuf` plugin, with `proto { srcDir(...) }` pointed at
  `interfaces/proto`. It produces Java under the bare `jumpstarter.interfaces.*`
  package and needs no buf/podman at build time.
- **Other languages**: `buf.gen.yaml` is a seed for future buf-native codegen
  targets (e.g. Go, TypeScript). It deliberately does **not** generate Java —
  buf's managed mode would emit a divergent `com.jumpstarter.interfaces.*`
  package that conflicts with the Gradle-generated one.

## Linting / building

`buf` runs in a container (matching `protocol/`), so a local container runtime
(podman/docker) is required:

```sh
make -C interfaces lint
make -C interfaces build
```
