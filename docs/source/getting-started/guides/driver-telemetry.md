# Driver telemetry

Drivers can publish Prometheus metrics and structured operational events without
starting a server or knowing how the exporter collects telemetry. This page is
the driver-author contract for JEP-0013 Phase 4.

The exporter owns one process-wide registry; drivers declare instruments on it
through `self.metrics` and record events through `self.emit_event()`. Nothing in
a driver talks to Loki, Prometheus, or the Telemetry service directly.

```{note}
Do not start an HTTP server in a driver. The exporter already exposes the
registry (locally and through `MetricsStream` reverse-scrape); a per-driver
endpoint would duplicate it and is explicitly out of scope.
```

## Declare a metric

```python
from jumpstarter.driver import Driver, export

class Power(Driver):
    driver_type = "power"

    @export
    def on(self):
        started = time.perf_counter()
        self._do_on()
        self.metrics.counter(
            "transitions",
            "Power transitions performed by the driver",
            labels=("state", "result"),
        ).labels(state="on", result="success").inc()
        self.metrics.histogram(
            "transition_seconds",
            "Time spent in a power transition",
            labels=("state",),
        ).labels(state="on").observe(time.perf_counter() - started)
        self.emit_event("power on", operation="on", result="success")
```

`self.metrics` offers three constructors:

| Method | Resulting series | Notes |
| --- | --- | --- |
| `self.metrics.counter(name, help, *, labels=())` | `…_total` | Counters get `_total` automatically; do not include it |
| `self.metrics.histogram(name, help, *, labels=(), buckets=None)` | `…_bucket/_sum/_count` | Default buckets unless `buckets=` is given |
| `self.metrics.gauge(name, help, *, labels=())` | bare name | Use `set()`/`inc()` |

Instrument names are prefixed `jumpstarter_driver_<namespace>_`. The namespace
is the driver's package slug (`jumpstarter_driver_power` → `power`), or the
class attribute `metrics_namespace` when set. Names and label names are
validated when declared: an invalid or reserved name raises immediately.

Declaration is idempotent: declaring the same family twice with the same
definition returns the same instrument. Declaring it with a different type,
help, labels, or buckets raises `MetricConflictError` naming the first differing
field, for example:

```text
jumpstarter_driver_power_transitions_total redefined: help was 'Power transitions', now 'Transitions'
```

## Labels

Every driver series carries two labels the driver does not manage:

| Label | Value | Overridable |
| --- | --- | --- |
| `exporter` | exporter name from the log context, resolved when the sample is recorded | no |
| `driver_type` | the driver's category (see below) | yes, per call or context |

Driver-declared labels are added after those two. Values are bounded to
`^[A-Za-z0-9_.:-]{1,64}$`; anything else (paths, URLs, IP addresses, serials,
free text) is recorded as `other` with a single warning per metric and label.
Keep distinct values out of labels: put them in an exemplar or an event field.
Series cardinality is the driver author's responsibility.

## Categories and composite drivers

Each driver declares one category from the documented set:

| Category | Typical drivers |
| --- | --- |
| `power` | `power`, `snmp`, `yepkit` |
| `storage` | `flashers`, `tftp`, `iscsi`, `opendal`, `sdwire` |
| `network` | `network`, `ssh`, `http`, `adb`, `mitmproxy` |
| `serial` | `pyserial` |
| `console` | `uboot` |
| `video` | `video`, `ustreamer` |
| `composite` | `qemu`, `renode`, `corellium`, `cuttlefish` |
| `automotive` | `can`, `doip`, `someip`, `xcp`, `obd` |
| `gpio` | `gpiod`, `copilot` |
| `bluetooth` | `ble` |
| `appium` | `appium` |
| `debug` | `probe-rs` |
| `iosdevice` | `iosdevice` |
| `shell` | `shell` |
| `testing` | `tmt` |

Values outside the operator's `driverTypeEnum` allowlist are remapped to
`other` by the Telemetry service. The default allowlist covers every category
above; administrators can extend it for site-specific drivers.

Composite drivers attribute delegated work with a context manager instead of a
second class attribute:

```python
with self.metrics.driver_type("storage"):
    self.metrics.counter("reads", "Delegated reads").inc()
```

Inside the block, driver metrics, emitted events, and the generic `DriverCall`
operation metrics all report `storage`; outside, they report the class value.
The context is task-local, so concurrent RPCs cannot bleed categories.

## Exemplars

Counter and histogram observations attach the same default exemplars as the
core exporter metrics (`client`, `lease_id`) from the request context. Pass
`exemplar=` to override:

```python
self.metrics.counter("reads", "Reads").inc(exemplar={"lease_id": "abc123"})
```

Exemplar keys, ordering, and the 128-character budget are managed by the
Telemetry service and the operator's `exemplarKeys`, not by the driver.

## Events

`self.emit_event(message, *, operation=None, result=None, fields=None,
severity="info")` records one structured operational event. The record carries
`driver_type`, `operation`, `result`, the current `lease_id` and `client`, and
the bounded `fields` map:

```python
self.emit_event(
    "flash failed",
    operation="flash",
    result="failure",
    severity="warning",
    fields={"image": "quay.io/example/image:1"},
)
```

Limits mirror the Telemetry service: at most 16 fields, keys up to 64
characters, values up to 256. Reserved log keys (`message`, `levelname`,
`time`, …) are dropped rather than raising. Emit events for significant
transitions, not per byte; the exporter's log fan-out handles backpressure.

Telemetry never changes an operation's outcome: a metrics or logging failure is
reported once and the operation continues (or re-raises its own error).

## The catalog and the workspace check

Every family the exporter publishes — the five core families and every driver
family declared in-repo — is listed in
[`driver-telemetry-catalog.json`](../../_static/driver-telemetry-catalog.json).

The checks run from `python/`:

```sh
make driver-telemetry-check          # guard + catalog + docs + Go allowlists
UPDATE_CATALOG=1 make driver-telemetry-check   # after adding or renaming a metric
```

The check scans `python/packages/jumpstarter-driver-*` on disk (no imports), so
it sees uninstalled drivers too. It fails when:

- a package declares no `driver_type` and is not listed in
  `python/packages/jumpstarter/jumpstarter/driver/_driver_type_baseline.txt`;
- a baseline entry now declares a category (remove the line in that change);
- a declared value is outside the documented set;
- the catalog differs from the source, naming added, removed, or changed rows;
- the docs page and the three Go allowlist authorities disagree.

The baseline may only shrink. Assigning a category to a grandfathered driver is
part of that driver's change.

## Testing a driver

Unit-test with the real registry, no exporter or cluster:

```python
from jumpstarter.metrics import get_registry
from jumpstarter.metrics._testing import reset_registry_for_tests

def test_power_counts_success():
    reset_registry_for_tests()
    power = MockPower(labels={"jumpstarter.dev/name": "lab-01"}, children={})
    power.on()
    body = get_registry().generate_latest().decode()
    assert 'jumpstarter_driver_power_transitions_total{driver_type="power",exporter="lab-01",result="success",state="on"} 1.0' in body
```

Call `reset_registry_for_tests()` so each test starts from the five core
families; handles held by an older driver instance re-register into the new
registry automatically.

### Hardware-in-the-loop checklist

Fake-backed tests cover the code paths in CI. Before claiming a driver is
instrumented on real hardware, verify in a lab:

- [ ] The power path records one `transitions` sample and one event for both a
      successful and a failed transition.
- [ ] The flash path records its counter by `result` and an event carrying the
      image reference.
- [ ] Serial/stream traffic appears as `jumpstarter_stream_bytes_total{tx|rx}`.
- [ ] `driver_type` is the documented category, and a composite reports its
      sub-driver categories for delegated work.
