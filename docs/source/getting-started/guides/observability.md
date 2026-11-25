# Observability

The Jumpstarter {term}`controller` and {term}`router` expose Prometheus metrics
and write structured logs. {term}`Exporter`s record metrics and structured logs as
well, but they often run outside the cluster, on lab hosts. The optional
telemetry service, `jumpstarter-telemetry`, collects metrics and logs from
exporters, and logs from `jmp shell`, so that neither needs Prometheus or Loki
credentials: Prometheus scrapes the telemetry service, and the telemetry
service pushes the logs to Loki.

This guide lists the signals, how to turn them on and how to consume them. The
Telemetry endpoints section of
[Production](../installation/service/production.md) explains how to expose the
telemetry service to exporters and {term}`client`s and how to provide its TLS
certificate. The design is described in
[JEP-0013](../../contributing/jeps/JEP-0013-observability-telemetry-logs.md);
this guide describes what is implemented.

## Signals

| Source | Metrics | Logs |
|--------|---------|------|
| Controller | `/metrics` on port 8080: {term}`lease` acquisitions | JSON lines |
| Router | `/metrics` on port 8080: runtime metrics only | JSON lines |
| Exporter | {term}`Driver` operations and stream traffic, collected by the telemetry service | Written by `jmp run`, streamed to clients during a lease, sent to the telemetry service |
| `jmp shell` | None | Sent to the telemetry service |
| Telemetry service | `/metrics` on port 8080: exporter metrics and its own counters | Forwards what it receives to Loki, or writes it to its own log |

```{mermaid}
flowchart LR
    subgraph "Kubernetes cluster"
        Controller["Controller"]
        Router["Router"]
        Telemetry["jumpstarter-telemetry"]
    end
    Exporter["Exporter"]
    Shell["jmp shell"]
    Prometheus["Prometheus"]
    Loki["Loki"]
    Exporter -- "GetServiceEndpoints" --> Controller
    Shell -- "GetServiceEndpoints" --> Controller
    Exporter -- "MetricsStream, PushLogs" --> Telemetry
    Shell -- "PushLogs" --> Telemetry
    Prometheus -- "GET /metrics" --> Controller
    Prometheus -- "GET /metrics" --> Router
    Prometheus -- "GET /metrics" --> Telemetry
    Telemetry -- "HTTP push" --> Loki
```

## How the telemetry service works

1. After registering with the controller, an exporter calls the controller's
   `GetServiceEndpoints` RPC; `jmp shell` calls it when it starts. When
   telemetry is enabled, the reply carries the telemetry address, a CA
   certificate to verify it, the minimum log severity to send and a token.
2. The exporter keeps a `MetricsStream` open to the telemetry service and sends
   its log records with `PushLogs`. `jmp shell` sends only its log records.
3. When Prometheus scrapes a telemetry pod, the pod asks every exporter
   connected to it for a snapshot of its metrics, in parallel, and answers with
   the merged result.
4. The telemetry service pushes the log entries it accepts to Loki. Without
   Loki, it writes them to its own log as JSON lines for a log shipper to
   collect.

Exporters and `jmp shell` have no telemetry settings of their own; they learn
everything from the controller. An exporter looks telemetry up once, when it
starts and registers, so restart exporters that were already running when you
enable telemetry.

## Enable the telemetry service

Enable telemetry in the `Jumpstarter` Custom Resource:

```yaml
spec:
  baseDomain: jumpstarter.example.com
  certManager:
    enabled: true
  telemetry:
    enabled: true
    image: quay.io/jumpstarter-dev/jumpstarter-controller:<version>
```

The examples on this page use the `Jumpstarter` resource from Production,
`jumpstarter` in the `jumpstarter-lab` namespace. The {term}`operator` names the
telemetry Deployment after it, `<name>-telemetry`.

With `baseDomain` set and an Ingress or Route API in the cluster, the
operator exposes telemetry at `telemetry.<baseDomain>`. To expose it
another way, set `spec.telemetry.grpc.endpoints` as described in
[Production](../installation/service/production.md). Exporters must be able to
verify the telemetry certificate; see [Security](#security).

The operator runs the `/telemetry` binary from `spec.telemetry.image`. Its
default, `quay.io/jumpstarter-dev/jumpstarter-telemetry:latest`, is not
published by the image build workflow, so set `spec.telemetry.image` to the
image and tag of `spec.controller.image`: the controller image contains the same
binary, and this keeps telemetry on the controller's release.

Check that the telemetry Deployment is ready and that the controller
advertises it:

```console
$ kubectl wait -n jumpstarter-lab --for=condition=TelemetryDeploymentReady \
    jumpstarter/jumpstarter --timeout=120s
$ kubectl get configmap jumpstarter-controller -n jumpstarter-lab \
    -o jsonpath='{.data.config}'
```

The `telemetry` block of that configuration holds the `endpoint`, `certificate`
and `logging` filter that the controller returns to callers. An exporter that
found the service logs:

```text
Connecting to telemetry service at telemetry.jumpstarter.example.com:443 (min_severity=info)
Telemetry log handler attached
```

`jmp shell` prints `Telemetry log handler attached (component=cli)`.

### Telemetry settings

These fields are under `spec.telemetry`; the
[CRD reference](../../reference/crds/index.md) covers the whole resource.

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Deploy the telemetry service and have the controller advertise it |
| `image` | `quay.io/jumpstarter-dev/jumpstarter-telemetry:latest` (not published; use the controller image) | Image that provides `/telemetry` |
| `imagePullPolicy` | `IfNotPresent` | Image pull policy |
| `replicas` | `1` | `0` suspends the Deployment, and the controller stops advertising telemetry |
| `resources` | Requests 50m CPU and 128Mi, limits 500m CPU and 256Mi | Used when no requests, limits or claims are set |
| `grpc.endpoints`, `grpc.tls.certSecret` | | Exposure and TLS, see [Production](../installation/service/production.md) |
| `logging.filter.minSeverity` | `info` | Lowest severity that exporters and `jmp shell` send: `debug`, `info`, `warning`, `error` or `critical`. Their `--log-level` still applies first |
| `metrics.scrapeTimeout` | `7s` | How long a scrape waits for exporters, at most `60s`. Keep it below the Prometheus `scrape_timeout` |
| `metrics.driverTypeEnum` | `power`, `storage`, `network`, `serial`, `console`, `video`, `composite` | Allowed `driver_type` label values, at most 16. Other values become `other` |
| `metrics.exemplarKeys` | `client`, `lease_id` | Exemplar labels to keep, at most 16 |
| `loki.url` | | Loki push URL. When empty, logs stay in the telemetry log |
| `loki.secretRef` | | Secret with Loki credentials |
| `loki.tls.caSecretRef` | | Secret whose `ca.crt` verifies the Loki certificate |
| `loki.tls.insecureSkipVerify` | `false` | Skip verifying the Loki certificate, for testing only |
| `backpressure.queueDepth` | `10000` | Log entries buffered for Loki |

An empty `driverTypeEnum` or `exemplarKeys` list falls back to the default.

## Metrics

### Controller

`jumpstarter_lease_acquisitions_total` is a counter with one label, `result`:

- `success`: the controller assigned an exporter to a lease.
- `failure`: the controller marked a lease unsatisfiable, for example because
  the requested exporter does not exist or no matching exporter is approved for
  the client.

Both series start at zero. The same endpoint serves the built-in metrics of
controller-runtime, the framework the controller is built on.

### Router

The router serves the default metrics of the Prometheus Go client library, such
as the Go runtime (`go_*`) and process (`process_*`) metrics. It records no
Jumpstarter metrics.

### Exporter

| Metric | Type | Labels |
|--------|------|--------|
| `jumpstarter_operations_total` | Counter | `exporter`, `operation`, `result`, `driver_type` |
| `jumpstarter_operation_duration_seconds` | Histogram | `exporter`, `operation`, `result`, `driver_type` |
| `jumpstarter_operation_errors_total` | Counter | `exporter`, `operation`, `driver_type`, `error_type` |
| `jumpstarter_stream_bytes_total` | Counter | `exporter`, `driver_type`, `direction` |
| `jumpstarter_active_sessions` | Gauge | `exporter` |

- `operation` is the driver method a client called, for example `on`
  or `flash`, and `result` is `success` or `failure`.
- `driver_type` is the category the driver class declares, such as `power`,
  `storage` or `network`. Drivers that declare none report `other`.
- `error_type` classifies a failure: `not_implemented`, `validation_error`,
  `timeout`, `connection_error`, `device_error` or `internal_error`.
- `direction` is `tx` for bytes the exporter sends to the client and `rx` for
  bytes it receives, on driver streams such as consoles and on file transfers.
- `jumpstarter_active_sessions` counts open lease {term}`session`s.
- Samples carry exemplars with `client` and `lease_id` when the exporter knows
  them.

The exporter does not accept scrapes from the network: the telemetry service
collects these metrics over `MetricsStream`. For debugging on the exporter
{term}`host`, `jmp run` also serves them on a random port on `127.0.0.1` and logs the
address at startup as `Serving metrics server at http://127.0.0.1:<port>/metrics`.

### Telemetry service

A telemetry pod's `/metrics` returns the exporter metrics above for every
exporter connected to it. It sets `exporter` to the name the exporter
authenticated as, replaces `driver_type` values that are not in
`metrics.driverTypeEnum` with `other`, and keeps only the exemplar labels in
`metrics.exemplarKeys`. It adds its own counters:

| Metric | Labels | Counts |
|--------|--------|--------|
| `jumpstarter_scrape_timeouts_total` | `exporter` | Exporters left out of a scrape because they did not answer within `metrics.scrapeTimeout` |
| `jumpstarter_metrics_parse_errors_total` | `exporter` | Exporter snapshots left out because they could not be parsed |
| `jumpstarter_telemetry_dropped_total` | `destination` (`loki`) | Log entries dropped because the Loki buffer was full |

Concurrent scrapes share one collection round. The response uses the
OpenMetrics text format, which carries the exemplars.

Each exporter streams to one telemetry pod, and a pod reports only the
exporters connected to it. With more than one replica, scrape every pod rather
than the `jumpstarter-telemetry` Service.

### Scraping

The operator runs the controller, every router and every telemetry pod with
plain HTTP `/metrics` on container port 8080, named `metrics`, without
authentication. The pods are labelled `component: controller`,
`component: router` and `component: telemetry`. Only telemetry has a Service
for this port, the internal `jumpstarter-telemetry` ClusterIP Service, and the
operator creates no ServiceMonitor. A Prometheus job that discovers the pods:

```yaml
scrape_configs:
  - job_name: jumpstarter
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names: [jumpstarter-lab]
    relabel_configs:
      - source_labels:
          - __meta_kubernetes_pod_label_component
          - __meta_kubernetes_pod_container_port_name
        regex: (controller|router|telemetry);metrics
        action: keep
      - source_labels: [__meta_kubernetes_pod_label_component]
        target_label: component
```

To look at the telemetry metrics by hand, forward the port:

```console
$ kubectl port-forward -n jumpstarter-lab deployment/jumpstarter-telemetry 8080:8080
```

and query it from another terminal:

```console
$ curl -s http://localhost:8080/metrics | grep '^jumpstarter_'
```

## Logs

### Exporter logs

`jmp run` writes its log to stderr. Choose the format with the `--log-format`
option of `jmp`, which goes before the `run` command:

```console
$ jmp --log-format json --log-level INFO run --exporter my-exporter
```

`auto`, the default, writes JSON when stderr is not a terminal and
human-readable text when it is; `json` and `text` force a format. The option
affects only `jmp run`. A JSON line looks like this:

```json
{"exporter": "my-exporter", "lease_id": "ci-1234", "build_id": "nightly-42", "level": "info", "logger": "driver.MockPower", "component": "exporter", "operation": "on", "driver_type": "power", "result": "success", "ts": "2026-11-24T15:50:18.645434Z", "msg": "Operation completed"}
```

- Each line has `ts`, `level`, `logger`, `msg` and `component` (`exporter`).
  Once the exporter serves, `exporter` holds its name.
- During a lease the exporter adds `lease_id`, the lease context entries (see
  [Lease context](#lease-context)) and, once known, `client`.
- In Kubernetes, `namespace` is added from the `NAMESPACE` or `POD_NAMESPACE`
  environment variable, or from the pod's service account.
- A driver call logs `Operation started`, then `Operation completed` or
  `Operation failed`, with `operation`, `driver_type`, `result` and, on
  failure, `error_type`.

To change how much a single driver logs, set `log_level` under the driver's
`config`; its children inherit it. See Common Configuration in
[Drivers](../../reference/package-apis/drivers/index.md).

### Lease context

Attach CI metadata to a lease to find every log line about it later:

```console
$ LEASE=$(jmp create lease -l board=rpi4 --duration 1h \
    --context build_id=nightly-42 --context pipeline=main --output name)
$ jmp shell --lease "$LEASE"
```

The entries are stored in the lease's `spec.context`: at most 8, with keys up
to 32 characters and values up to 64, immutable once created. The controller
adds them to its log lines about the lease. The exporter adds them to its log
lines and to the log entries it sends to the telemetry service.

### Controller and router logs

The controller and router log JSON lines at info level, with `component` set
to `controller` or `router`. While reconciling a lease, the controller adds
`lease_id`, `client`, `exporter` when the lease requests one by name, and the
lease context entries.

### Exporter logs in a session

`jmp shell` always prints the output of the exporter's {term}`hook` scripts;
see [Hooks](../../introduction/hooks.md). With `--exporter-logs` it also
prints the exporter's driver and system logs:

```console
$ jmp shell -l board=rpi4 --exporter-logs
```

Each block of lines starts with its source: `[exporter:beforeLease]`,
`[exporter:afterLease]`, `[exporter:driver]` or `[exporter:system]`.

In Python, `log_stream()` on the client streams the same records into Python
logging, under the same logger names:

```python
import logging

from jumpstarter.utils.env import env

logging.basicConfig(level=logging.INFO)

with env() as client:
    with client.log_stream():
        client.power.on()
```

Every client connected to the lease, including clients it is shared with,
receives every record. While no client is connected, the exporter keeps its
256 most recent records for the next one.

### Logs sent to the telemetry service

When telemetry is enabled, exporters send their log records with component
`exporter`, and `jmp shell` sends its own with component `cli`, from
`logging.filter.minSeverity` up. Each entry carries the message, severity and
timestamp and, when known, the lease, client, operation, result and driver
type. Other fields, such as the lease context, travel as up to 16 extra
fields, with keys up to 64 and values up to 256 characters. The `LogEntry`
message in the [telemetry protocol reference](../../reference/grpc/telemetry.md)
lists every field.

Sending is best effort. A batch that fails is dropped, and the exporter or
`jmp shell` prints `[telemetry] PushLogs failed, <n> entries dropped` to
stderr. At most 10,000 entries wait in memory.

Without Loki, the telemetry service writes each accepted entry to its own log
as a JSON line:

```console
$ kubectl logs -n jumpstarter-lab -l app=jumpstarter-telemetry
```

### Loki

Point the telemetry service at Loki in the `Jumpstarter` Custom Resource:

```yaml
spec:
  telemetry:
    enabled: true
    loki:
      url: https://loki.example.com
      secretRef: loki-credentials
      tls:
        caSecretRef: loki-ca
```

and create the Secrets it references:

```console
$ kubectl create secret generic loki-credentials -n jumpstarter-lab \
    --from-literal=username=jumpstarter --from-literal=password=<password>
$ kubectl create secret generic loki-ca -n jumpstarter-lab \
    --from-file=ca.crt=loki-ca.pem
```

- `url` uses `http://` or `https://`. Without a path, or with `/`, telemetry
  posts to `/loki/api/v1/push`; any other path is used as given.
- The credentials Secret holds `token`, sent as a bearer token, or `username`
  and `password` for basic authentication; `token` wins when both are present.
  Leave `secretRef` empty for a Loki that needs no credentials.
- Credentials are sent only over `https://`. With credentials and an `http://`
  URL, telemetry disables Loki push and logs `Loki push disabled`.
- If a referenced Secret is missing, or the credentials Secret has no `token`,
  `username` or `password`, the operator runs telemetry without Loki push and
  records a `LokiCredentialsMissing` warning event on the `Jumpstarter`
  resource. Changing a referenced Secret restarts the telemetry pods.
- Telemetry sends no tenant header such as `X-Scope-OrgID`, and does not
  follow redirects.
- With Loki configured, accepted entries go only to Loki. Failed pushes are
  retried. While `backpressure.queueDepth` entries are waiting, new entries
  are dropped, counted in `jumpstarter_telemetry_dropped_total` and reported
  by one `log entries dropped due to Loki backpressure` warning entry.

Telemetry groups the entries into streams labelled `component` (`exporter`,
`cli` or `telemetry`), `exporter` (for exporter entries) and `namespace`. Each
log line is a JSON object with `msg`, `severity`, `ts` and, when set, `lease`,
`client`, `operation`, `result`, `driver_type` and the extra fields. For
example, every exporter log line from one CI run:

```text
{namespace="jumpstarter-lab", component="exporter"} | json | build_id="nightly-42"
```

## Security

- **TLS.** The telemetry service accepts {term}`gRPC` only over TLS. Exporters and
  `jmp shell` verify it against the CA certificate that the controller returns
  with the endpoint, or against the system trust store when there is none. The
  controller returns the CA of the cert-manager issuer, or the `ca.crt` of
  `spec.telemetry.grpc.tls.certSecret`. With neither cert-manager nor
  `certSecret`, telemetry generates a self-signed certificate that exporters
  cannot verify. With `tls.insecure` in the configuration, or
  `JMP_GRPC_INSECURE=1`, and no CA returned by the controller, `jmp shell`
  accepts the telemetry certificate without verifying it, while an exporter
  falls back to plaintext gRPC, which the telemetry service rejects, so that
  exporter sends no telemetry.
- **Tokens.** Telemetry accepts only tokens that the controller issues from
  `GetServiceEndpoints` to an authenticated exporter or client. A token names
  that exporter or client, is valid only for telemetry calls, and expires
  after an hour at most, never later than the credential that requested it.
  Exporters and `jmp shell` renew it halfway to expiry. Exporter and client
  credentials themselves, including {term}`OIDC` tokens, are not accepted.
- **Identity.** Telemetry sets `namespace`, and `exporter` or `client`, on
  each log entry from the token, and drops entries that claim another
  identity. Only exporter tokens can open a `MetricsStream`, and only under
  the exporter name in the token.
- **No cluster access.** Telemetry verifies tokens with the controller's
  signing key, from the `jumpstarter-controller-secret` Secret, and runs under
  its own ServiceAccount, which the operator grants no permissions. Only the
  telemetry pod receives the Loki credentials.
- **Metrics endpoints.** The `/metrics` endpoints have no authentication, and
  the operator does not expose them outside the cluster. Telemetry's metrics
  carry client and lease names in exemplars; set `metrics.exemplarKeys` to
  `[lease_id]` to drop client names.

## Troubleshooting

| Symptom | What to check |
|---------|---------------|
| The exporter never logs `Connecting to telemetry service` | It started before telemetry was enabled, or the controller does not advertise telemetry. Restart it with `jmp --log-level DEBUG run` and look for `GetServiceEndpoints unavailable` or `No telemetry endpoint configured`. |
| `[telemetry] PushLogs failed` on stderr | The exporter or `jmp shell` cannot reach telemetry or verify its certificate. Check the advertised endpoint and the TLS setup. |
| The exporter logs `MetricsStream rejected (UNAUTHENTICATED)` | Telemetry refused its token, for example because it expired and could not be renewed. With `--log-level DEBUG` the exporter logs `Telemetry token renewal failed`. |
| An exporter is missing from `/metrics` | Each telemetry pod logs `MetricsStream registered` for the exporters connected to it, so check every pod. A rising `jumpstarter_scrape_timeouts_total` means the exporter answers too slowly for `metrics.scrapeTimeout`. |
| The telemetry log shows `Using self-signed TLS certificate` | No certificate is configured: enable cert-manager or set `grpc.tls.certSecret`. Copying the certificate into the controller ConfigMap, as the message suggests, does not last, because the operator rewrites that ConfigMap. |
| No logs reach Loki | Look for a `LokiCredentialsMissing` event with `kubectl describe jumpstarter -n jumpstarter-lab`, and for `Loki push disabled` in the telemetry log. |
