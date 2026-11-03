---
orphan: true
---

# JEP-0017: Flight Control Host Management for jumpstarter-lab-config

| Field             | Value                                                                                         |
| ----------------- | --------------------------------------------------------------------------------------------- |
| **JEP**           | 0017                                                                                          |
| **Title**         | Flight Control Host Management for jumpstarter-lab-config                                     |
| **Author(s)**     | @raballew (Paul Wallrabe \<code@raballew.de\>)                                                |
| **Status**        | Draft                                                                                         |
| **Type**          | Standards Track                                                                               |
| **Created**       | 2026-10-27                                                                                    |
| **Updated**       | 2026-10-27                                                                                    |
| **Discussion**    | [Issue #1242](https://github.com/jumpstarter-dev/jumpstarter/issues/1242)                     |
| **Requires**      |                                                                                               |
| **Supersedes**    |                                                                                               |
| **Superseded-By** |                                                                                               |

---

## Abstract

[jumpstarter-lab-config](https://github.com/jumpstarter-dev/jumpstarter-lab-config)
configures exporter hosts - the machines that run Jumpstarter exporters next to
the hardware - by opening an SSH session to each host and running a sequence of
file writes and shell commands. This JEP adds a second, declarative management
backend built on the [Flight Control](https://github.com/flightctl/flightctl)
Device API: for each exporter host, lab-config renders the bootc OS image, the
exporter unit and configuration files, an activation hook and systemd
monitoring into the part of the host's Flight Control `Device` spec that
lab-config owns, and the Flight Control agent on the host converges to it
transactionally, rolls back on failure and reports status back. SSH stays
supported and stays the default; the backend is chosen per `ExporterHost`. The
JEP records an analysis of the Flight Control API's suitability, explains why
the Device API rather than the Fleet API is the integration point, and
specifies mitigations for the gaps the analysis found: secret handling,
lease-aware OS updates and floating container image tags.

## Motivation

### How exporter hosts are managed today

lab-config reads a GitOps repository of `ExporterHost`, `ExporterInstance`,
`ExporterConfigTemplate` and `JumpstarterInstance` resources (API group
`meta.jumpstarter.dev/v1alpha1`). `jumpstarter-lab-config apply` first syncs
`Exporter` and `Client` objects into every Jumpstarter controller and collects
each exporter's token, then walks the exporter hosts. For every host it
(`internal/exporter/host/host.go`, `internal/exporter/ssh/ssh.go`):

1. Opens an SSH and SFTP session using `spec.management.ssh`.
2. For each exporter on the host, renders its `ExporterConfigTemplate` and
   reconciles up to three files: `/etc/containers/systemd/<name>.container`
   or `/etc/systemd/system/<name>.service`, and
   `/etc/jumpstarter/exporters/<name>.yaml`, which contains the exporter's
   token.
3. Runs `systemctl daemon-reload`, `systemctl enable` and `systemctl start` as
   needed and, when something changed, sends `SIGHUP` to the exporter
   (`podman kill -s SIGHUP`, falling back to `systemctl kill -s SIGHUP`). An
   exporter that receives `SIGHUP` exits once its current lease ends, and
   systemd restarts it with the new configuration.
4. Compares the registry labels of the exporter container image
   (`skopeo inspect`) with the labels of the running container
   (`podman inspect`) and triggers the same graceful restart when they differ,
   so that a moved tag such as `release-0.6` is picked up.
5. Runs `bootc upgrade --check` and, if an update is available, restarts
   `bootc-fetch-apply-updates.timer`.
6. Retries failed hosts with exponential backoff, nine attempts over about ten
   minutes.

The lab-config README already plans "a module to interface with
flightctl/redhat edge management", and the doc comment on
`ExporterHost.spec.management` lists "flightctl device ids" as a future
option. Issue [#1242](https://github.com/jumpstarter-dev/jumpstarter/issues/1242)
tracks this proposal.

### Problems with SSH push management

1. **Reachability.** The machine running `apply`, usually a CI runner, needs a
   direct SSH connection to every exporter host. Hosts behind NAT, on site
   networks or on desks outside the lab network are hard or impossible to
   reach without VPNs or jump hosts.
2. **Credentials and host verification.** CI holds SSH credentials for a
   root-capable account on every host. Host keys are not verified
   (`ssh.InsecureIgnoreHostKey()`), so a spoofed host would be sent exporter
   tokens.
3. **Imperative, order-dependent reconciliation.** Desired state is
   re-derived from shell output on every run, which produces ordering bugs:
   lab-config [#34](https://github.com/jumpstarter-dev/jumpstarter-lab-config/issues/34)
   and [PR #39](https://github.com/jumpstarter-dev/jumpstarter-lab-config/pull/39)
   found that a `daemon-reload` skipped during a bootc upgrade is never
   retried, because on the next run the files already match. A failure part
   way through leaves a host half updated, with no rollback.
4. **Convergence only while `apply` runs.** A host that is offline or
   rebooting during `apply` is retried for about ten minutes and then stays
   stale until someone runs `apply` again. Nothing corrects drift in between.
5. **The OS image is not actually declared.** `ExporterHost.spec.containerImage`
   holds the host's bootc image in practice (the example repository sets it to
   a `jumpstarter-exporter-bootc` image), but the SSH backend never reads it.
   It only upgrades whatever image the host already tracks, so moving hosts to
   a new image stream takes a manual `bootc switch` on every host.
6. **Disruption.** An OS upgrade reboots the host as soon as `apply` triggers
   it, whatever leases are active. lab-config
   [#22](https://github.com/jumpstarter-dev/jumpstarter-lab-config/issues/22)
   asks for a non-disruptive mode.
7. **No inventory or status.** lab-config cannot say what a host runs without
   connecting to it, and organizations that standardized on Flight Control
   (productized as Red Hat Edge Manager) for edge devices have to manage
   exporter hosts outside of it.

### User Stories

- **As a** lab administrator with exporter hosts in three sites behind NAT,
  **I want** lab-config to configure them without inbound SSH, **so that** CI
  needs no VPN or jump host to reach them.
- **As a** security engineer, **I want** CI to stop holding root SSH keys for
  lab machines, **so that** a leaked CI secret does not grant a shell on every
  exporter host.
- **As a** platform engineer, **I want** to roll a new bootc image out to
  exporter hosts declaratively, inside a maintenance window and with automatic
  rollback, **so that** a bad image cannot take a lab offline.
- **As a** lab administrator migrating gradually, **I want** to move hosts from
  SSH to Flight Control one at a time, **so that** hosts that cannot run the
  Flight Control agent keep working.

### Goals

- A `flightctl` management backend in lab-config with parity for exporter
  deployment, graceful exporter restarts and OS image management.
- Backend selection per `ExporterHost`, with the SSH backend unchanged.
- Exporter tokens never stored in clear text in Flight Control resources by
  default.
- No changes to the Jumpstarter controller, exporter, CRDs or gRPC protocol.

### Non-Goals

- Enrolling devices into Flight Control or approving enrollment requests.
- Deploying or configuring Flight Control itself (service, authentication
  providers, organizations).
- Running exporters as Flight Control applications (see DD-2).
- The productization half of #1242: packaging lab-config as a supported
  component, moving it into the monorepo, or turning it into an operator.
  These deserve their own proposal.

## Flight Control API Suitability Analysis

This section analyzes the Flight Control API as the integration surface for
lab-config. It is based on Flight Control `main` at
[`3c8fd02`](https://github.com/flightctl/flightctl/tree/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f)
(2026-10-26, `v1.4.0-rc1` plus 35 commits) and its core API
`flightctl.io/v1beta1`. Claims cite the OpenAPI document
(`api/core/v1beta1/openapi.yaml`), the user documentation (`docs/user/`) or
the source code; paths are relative to that commit.

### Programming model

- Kubernetes-style resources with `metadata`, `spec` and `status`, served
  under `/api/v1`. The API version is negotiated per resource with the
  `Flightctl-API-Version` request header; `v1beta1` is "supported throughout
  the 1.x.x major version" (`docs/developer/architecture/api-versioning.md`).
- Bearer JWT authentication. Supported identity sources are OIDC, OAuth2,
  Kubernetes service account tokens (validated through TokenReview),
  OpenShift OAuth and AAP. Multi-tenancy uses an `org_id` query parameter.
  Built-in roles such as `flightctl-operator` grant create, update and delete
  on devices and fleets (`docs/user/installing/configuring-auth/`).
- The agent on each device generates its own key pair, enrolls through an
  `EnrollmentRequest` that an authorized user approves, and from then on
  connects outbound only, over mTLS with a device-specific certificate. It
  polls for its rendered spec and reports status, both every 60 seconds by
  default (`internal/agent/config/config.go`).

### Capability map

| lab-config need | SSH backend today | Flight Control capability | Fit |
| --- | --- | --- | --- |
| Address a host | `management.ssh.host` | `Device`, named by the agent's key fingerprint; selectable by labels, by the automatic `alias` label (the hostname) and by field selectors | Good, needs a mapping (DD-4) |
| Write exporter files | SFTP | `spec.config[]` providers (inline, Kubernetes Secret, Git, HTTP), applied transactionally to any absolute path except `/var/lib/flightctl`, `/usr/lib/flightctl`, `/etc/flightctl/certs` and the agent configuration | Good |
| Deliver the exporter token | Inside the file written over SFTP | The device spec is not encrypted at rest and is readable by anyone allowed to get devices; `secretRef` content is resolved at render time into the encrypted rendered configuration | Partial (DD-5) |
| Activate changes | `systemctl`, `podman kill -s SIGHUP` | `afterUpdating` lifecycle hook rules with path conditions and changed-file variables; the built-in rule reloads systemd only for `/etc/systemd/system/` | Good with a hook (DD-6) |
| Run exporters as managed workloads | Quadlet files | `spec.applications[]` of type `quadlet`, `container` or `compose`: native status, but an update stops the application's units before starting them again, unit names are namespaced, and `container` applications cannot express `--privileged` or host device mounts | Poor (DD-2) |
| OS image | `bootc upgrade` of the tracked image | `spec.os.image`: staged download, bootc switch, reboot, greenboot-based rollback | Excellent (DD-7) |
| Avoid disrupting leases | None | `spec.updatePolicy` cron windows for download and update; hooks can abort an update, but an abort permanently fails that rendered version | Partial (DD-8) |
| Exporter health | `systemctl is-active` | `spec.systemd.matchPatterns` reported as `status.systemd[]` with load, active, sub and enable state | Good |
| Host state | None | `status.summary`, `status.updated` with the `Updating` condition reason, `status.os`, `status.config.renderedVersion`, `/devices/{name}/lastseen`, events | Good |
| Pick up a moved container tag | Registry labels compared with running labels | Floating tags are deliberately not reconciled by service or agent | Gap (DD-9) |
| Run arbitrary commands | SSH | WebSocket console `/ws/v1/devices/{name}/console`, authorized separately (`devices/console`) | Not used for reconciliation |
| Concurrent writers | Not applicable | JSON Patch (`add`, `replace`, `remove`, `test`) re-applied in a server-side compare-and-swap loop; agent status heartbeats bump `resourceVersion` | Good with `test` guards |
| Roll out to many hosts | Parallel SSH sessions | Fleets: label selector, one device template, rollout policy with batches, success thresholds and disruption budgets | Poor fit (see below) |
| Go client | `golang.org/x/crypto/ssh` | Versioned OpenAPI documents; a public `client` package that returns types from `internal/api/client`; `api/core/v1beta1` imports flightctl internal packages | Generate a client (DD-12) |

### Device API

The `Device` resource covers everything lab-config does over SSH except
running arbitrary commands, and adds a declared OS image, rollback and status.
The following properties shape the design:

- **Names are not chosen by users.** "The unique device name is generated by
  the agent and cannot be changed" (`docs/user/using/managing-devices.md`).
  lab-config therefore binds an `ExporterHost` to a device through labels or
  an explicit device name.
- **Fleet ownership locks the spec.** A device selected by a fleet carries
  `metadata.owner: Fleet/<name>`, and writes that change its spec fail with
  `updating the resource is not allowed because it has an owner`
  (`internal/service/device/handler.go`). Labels can still be changed.
- **The spec is shared.** Other Flight Control users may add configuration or
  applications to the same device. lab-config must not overwrite entries it
  does not own.
- **Heartbeats bump `resourceVersion`.** The agent's status update writes the
  status and increments `resource_version` (`internal/store/device/store.go`,
  `UpdateStatus`). A PUT guarded by `resourceVersion` would therefore conflict
  about once a minute per device. PATCH re-applies the JSON Patch to the
  current object on every compare-and-swap attempt
  (`internal/service/device/handler.go`, `PatchDevice`), so `test` operations
  on the fields lab-config owns give optimistic locking that heartbeats do not
  disturb.
- **Configuration is transactional, activation is not.** "The Flight Control
  Agent applies updates to this layer transactionally, ensuring that either
  all files have been successfully updated in the file system or have been
  returned to their pre-update state." Reloading services is left to
  lifecycle hooks, which are read from disk when they run
  (`internal/agent/device/hook/manager.go`), so hook files delivered in an
  update apply to that same update.
- **Hook failures are terminal.** A failing `beforeUpdating` action aborts the
  update and a failing `afterUpdating` action rolls it back. Both surface as
  `ErrExitCode`, which `IsRetryable` does not treat as retryable
  (`internal/agent/device/errors/errors.go`), so the agent marks the rendered
  version as failed and does not try it again until a new version is
  rendered. A timed-out action is killed and reported the same way. Hooks
  therefore cannot be used to wait for a lease to end.
- **Application updates restart workloads.** For quadlet applications,
  `Execute` stops the target and services of every removed or updated
  application, runs `daemon-reload` and then starts every added or updated
  application (`internal/agent/device/applications/lifecycle/quadlet.go`).
  Applied to an exporter, that ends any active lease.
- **Secrets need Kubernetes.** Encryption at rest covers `renderedConfig` and
  `renderedApplications` but not the device spec, and the documentation warns
  not to store tokens in unprotected fields
  (`docs/user/installing/configuring-encryption.md`). The Kubernetes Secret
  provider works only when Flight Control runs in a cluster and its worker
  service account may read the Secret, either through a Role in the Secret's
  namespace or cluster-wide through the Helm value
  `worker.clusterLevelSecretAccess`. Standalone devices fetch the secret on
  every render
  (`internal/tasks/device_render.go`, `renderK8sConfig`); files derived from a
  secret are written with mode `0644`, and `secretRef.name` must be a DNS-1123
  label of at most 63 characters.

### Fleet API

A `Fleet` selects devices by label and stamps one device template onto all of
them, rolling changes out according to its rollout policy. It is the obvious
tool for many identical devices, and batches, success thresholds and
disruption budgets grouped by label would be attractive for OS updates. It
does not fit lab-config's model:

- **One template per device.** A host runs a varying number of exporters,
  often from different `ExporterConfigTemplate`s. Device templates support
  conditionals but no loops (`range` is not supported), so a variable list of
  exporters cannot be expressed.
- **Per-device data only through name and labels.** Template placeholders can
  reference only the device name and labels, and label values are limited to
  63 characters of `[A-Za-z0-9._-]`. Exporter parameters such as
  `/dev/ttyUSB0` or `socket://localhost:9997` cannot be carried.
- **Exclusive ownership.** A fleet-owned device cannot be edited directly, so
  fleet-wide settings and per-host exporters cannot be combined on the same
  device.
- **ResourceSync does not help.** Flight Control can pull resource manifests
  from Git, but only `Fleet` and catalog resources
  (`internal/tasks/resourcesync.go`), and rendering tokens into Git is not
  acceptable.

The only workable fleet design would pre-render each host's entire exporter
set into per-device Kubernetes Secrets referenced through a templated
`secretRef` name. That turns non-secret configuration into secrets, ties the
design to in-cluster Flight Control and hides configuration changes from
review. Fleets are therefore not used by this proposal; a "fleet mode" for
homogeneous pools is listed under Future Possibilities.

### Verdict

The Device API is suitable as the integration point, given three mitigations
that this JEP specifies: tokens are delivered through Kubernetes Secret
references (DD-5), OS updates are coordinated with leases by lab-config rather
than by the device (DD-8), and container images are pinned by digest (DD-9).
Two upstream Flight Control improvements would simplify the design and are
listed as Unresolved Questions: a file mode for secret-derived files, and a
retryable "not now" outcome for lifecycle hooks.

## Proposal

### Overview

lab-config gets a second implementation of host management. Which one runs is
decided per host by `ExporterHost.spec.management`:

- `ssh`: the existing behavior, unchanged.
- `flightctl`: lab-config never connects to the host. It edits the host's
  `Device` in Flight Control, and the Flight Control agent on the host applies
  the change.

This introduces five concepts:

- **FlightctlInstance**: a new resource kind that describes how to reach a
  Flight Control service: endpoint, organization, credentials, TLS, and where
  to stage secrets.
- **Device binding**: how an `ExporterHost` finds its `Device`, by label
  selector or by device name.
- **Owned fragment**: the parts of a Device spec that lab-config manages:
  `spec.os` when the host declares `containerImage`, every config provider
  whose name starts with `jumpstarter.`, the exporter units in
  `spec.systemd.matchPatterns`, and `spec.updatePolicy` when the host
  declares one. Everything else in the spec belongs to someone else and is
  left alone.
- **Activation hook**: an `afterUpdating` rule file plus a helper script that
  lab-config delivers to every host. After each update they reload systemd,
  start new exporters, gracefully restart changed ones and stop removed ones.
- **Exporter credential secret**: a Kubernetes Secret per exporter that holds
  the exporter's rendered configuration file, including its token. The Device
  spec references the Secret instead of carrying the token.

### Configuring a lab

`jumpstarter-lab.yaml` gets one more source:

```yaml
sources:
  locations:
    - locations/*.yaml
  exporter_hosts:
    - devices/*/*/*-sidekick.yaml
  exporters:
    - devices/*/*/*-dut.yaml
  exporter_templates:
    - exporter-templates/*/*.yaml
  jumpstarter_instances:
    - jumpstarter-instances/*.yaml
  flightctl_instances:
    - flightctl-instances/*.yaml
variables:
  - vars.yaml
```

A `FlightctlInstance` describes one Flight Control service and organization:

```yaml
apiVersion: meta.jumpstarter.dev/v1alpha1
kind: FlightctlInstance
metadata:
  name: edge-bos
spec:
  endpoint: https://api.flightctl.bos.example.com
  organization: 7b4d1c6e-2f0a-4f39-9d2e-5f8f0c3b1a20
  token: "$( vars.flightctl_bos_token )"
  tls:
    caData: "$( vars.flightctl_bos_ca )"
  secrets:
    kube-context: edge-bos
    namespace: jumpstarter-exporter-configs
  notes: |
    Flight Control instance that manages the Boston lab exporter hosts.
```

| Field | Meaning |
| --- | --- |
| `endpoint` | Base URL of the Flight Control API. lab-config calls `<endpoint>/api/v1/...` and sends `Flightctl-API-Version: v1beta1`. Required. |
| `organization` | Organization ID, sent as `org_id`. Optional for single-organization deployments. |
| `token` | Bearer token, usually a vault-encrypted variable. When empty, lab-config reads `FLIGHTCTL_TOKEN` from the environment, the variable the `flightctl` CLI uses for non-interactive login. |
| `tls.caData`, `tls.insecureSkipVerify` | Trust settings for the API endpoint. |
| `secrets.kube-context`, `secrets.kubeconfig`, `secrets.namespace` | Where lab-config writes exporter credential secrets. Flight Control must run in that cluster and be allowed to read that namespace. |
| `allowInlineSecrets` | Default `false`. When `true` and `secrets` is not set, tokens are written inline into the Device spec (DD-5). |
| `notes` | Free text, as on `JumpstarterInstance`. |

An `ExporterHost` opts in through `spec.management.flightctl`:

```yaml
apiVersion: meta.jumpstarter.dev/v1alpha1
kind: ExporterHost
metadata:
  name: ti-jacinto-j784s4xevm-01-sidekick-01
spec:
  locationRef:
    name: bos2-lab1
    rack: "311"
    tray: U2
  containerImage: "$( vars.exporter-host-image )"
  management:
    flightctl:
      instanceRef:
        name: edge-bos
      osUpdateStrategy: DrainExporters
      updatePolicy:
        updateSchedule:
          at: "0 3 * * *"
          timeZone: America/New_York
          startGraceDuration: 2h
```

| Field | Meaning |
| --- | --- |
| `instanceRef.name` | The `FlightctlInstance` that manages this host. Required. |
| `selector` | Label selector that must match exactly one device. Defaults to `jumpstarter.dev/exporter-host=<metadata.name>`. |
| `deviceName` | Explicit device name. Mutually exclusive with `selector`. |
| `osUpdateStrategy` | `Immediate` (default) or `DrainExporters` (DD-8). |
| `updatePolicy` | Passed through to the Device's `spec.updatePolicy`: cron windows for downloading and for applying updates. |

`ExporterInstance` and `ExporterConfigTemplate` do not change. The same
templates render for SSH-managed and Flight Control-managed hosts, and
`spec.addresses` is no longer required for hosts managed by Flight Control.

### Preparing a host

1. Build the exporter host bootc image with the Flight Control agent and an
   enrollment configuration (`config.yaml`, obtained with `flightctl
   certificate request --signer=enrollment`), following Flight Control's
   "Building OS Images" guide. For a CentOS Stream 9 based image:

   ```dockerfile
   FROM quay.io/centos-bootc/centos-bootc:stream9
   RUN dnf -y config-manager --add-repo https://rpm.flightctl.io/flightctl-epel.repo && \
       dnf -y install flightctl-agent && \
       dnf -y clean all && \
       systemctl enable flightctl-agent.service
   ADD config.yaml /etc/flightctl/
   ```

2. Boot the host. The agent submits an `EnrollmentRequest`.
3. Approve the request and bind it to the `ExporterHost` with the binding
   label:

   ```console
   $ flightctl approve -l jumpstarter.dev/exporter-host=ti-jacinto-j784s4xevm-01-sidekick-01 \
       enrollmentrequest/54shovu028bvj6stkovjcvovjgo0r48618khdd5huhdjfn6raskg
   ```

4. Set `spec.management.flightctl` on the `ExporterHost` and run
   `jumpstarter-lab-config apply`.

### What `apply` does for a Flight Control host

Client and exporter sync against the Jumpstarter controllers is unchanged and
still produces each exporter's token. Then, for each host managed by Flight
Control, lab-config:

1. Resolves the device. Zero matches means the host is not enrolled or not
   approved yet: it is reported as pending and skipped. More than one match is
   an error for that host.
2. Refuses to write if the device is owned by a fleet or is being
   decommissioned.
3. Renders every active exporter on the host exactly as for SSH, and pins
   each exporter container image to a digest (DD-9).
4. Creates or updates one credential Secret per exporter in
   `secrets.namespace`.
5. Builds the owned fragment, merges it into the device's current spec and
   prints the difference, with secret content masked.
6. Applies the host's `osUpdateStrategy` (DD-8), which may hold back an OS
   image change for this run.
7. Writes the merged spec with a JSON Patch guarded by `test` operations.
8. Reports the device's status. With `--wait`, it keeps polling until the
   host has converged, failed or timed out.

```{mermaid}
sequenceDiagram
    participant LC as lab-config
    participant JC as Jumpstarter controller
    participant NS as Secret namespace
    participant FC as Flight Control API
    participant AG as Agent on exporter host
    LC->>JC: sync Exporter objects, read tokens
    LC->>FC: GET /api/v1/devices?labelSelector=...
    FC-->>LC: Device spec, status, owner
    LC->>NS: create or update exporter credential Secrets
    LC->>FC: PATCH /api/v1/devices/{name} (test, then replace owned fields)
    FC-->>LC: updated Device
    AG->>FC: fetch rendered spec
    FC->>NS: read Secrets while rendering
    FC-->>AG: rendered spec (files, OS image)
    AG->>AG: write files, run afterUpdating hook
    AG->>FC: report status (updated, systemd units, OS image)
    LC->>FC: with --wait, poll Device status
```

### Example: the owned fragment

For the host above, with one container exporter, lab-config produces this
fragment and merges it into whatever else the device's spec contains:

```yaml
apiVersion: flightctl.io/v1beta1
kind: Device
metadata:
  name: 54shovu028bvj6stkovjcvovjgo0r48618khdd5huhdjfn6raskg
  labels:
    jumpstarter.dev/exporter-host: ti-jacinto-j784s4xevm-01-sidekick-01
spec:
  os:
    image: quay.io/auto-lab/jumpstarter-exporter-bootc:0.7.1
  updatePolicy:
    updateSchedule:
      at: "0 3 * * *"
      timeZone: America/New_York
      startGraceDuration: 2h
  config:
    - name: jumpstarter.activation
      inline:
        - path: /etc/flightctl/hooks.d/afterupdating/50-jumpstarter-exporters.yaml
          content: |
            ...
        - path: /etc/jumpstarter/lab-config/activate-exporters
          mode: 0755
          content: |
            ...
    - name: jumpstarter.exporter.ti-jacinto-j784s4xevm-01
      inline:
        - path: /etc/containers/systemd/ti-jacinto-j784s4xevm-01.container
          content: |
            [Unit]
            Description=ti-jacinto-j784s4xevm-01 jumpstarter exporter
            [Container]
            ContainerName=ti-jacinto-j784s4xevm-01
            Exec=/jumpstarter/bin/jmp run --exporter ti-jacinto-j784s4xevm-01
            Image=quay.io/jumpstarter/jumpstarter:release-0.6@sha256:4f2c...
            Network=host
            PodmanArgs=--privileged
            Volume=/run/udev:/run/udev
            Volume=/dev:/dev
            Volume=/etc/jumpstarter:/etc/jumpstarter
            [Service]
            Restart=always
            StartLimitBurst=0
            [Install]
            WantedBy=multi-user.target default.target
    - name: jumpstarter.exporter.ti-jacinto-j784s4xevm-01.credentials-9c1e04ab
      secretRef:
        name: jumpstarter-ti-jacinto-j784s4xevm-01
        namespace: jumpstarter-exporter-configs
        mountPath: /etc/jumpstarter/exporters
  systemd:
    matchPatterns:
      - ti-jacinto-j784s4xevm-01.service
```

The files land exactly where the SSH backend puts them, so the same exporter
images, templates and documentation apply to both backends.

### Example output

```text
Syncing exporter hosts via Flight Control (edge-bos) ==========

Exporter host: ti-jacinto-j784s4xevm-01-sidekick-01
    device: 54shovu028bvj6stkovjcvovjgo0r48618khdd5huhdjfn6raskg (Online, UpToDate)
    Exporter instance: ti-jacinto-j784s4xevm-01
        image pinned: quay.io/jumpstarter/jumpstarter:release-0.6@sha256:4f2c...
        credentials secret: unchanged
        Changes for file: /etc/containers/systemd/ti-jacinto-j784s4xevm-01.container
        Diff (-existing +new):
        - Image=quay.io/jumpstarter/jumpstarter:release-0.6@sha256:77ab...
        + Image=quay.io/jumpstarter/jumpstarter:release-0.6@sha256:4f2c...
    os image: quay.io/auto-lab/jumpstarter-exporter-bootc:0.7.0 -> 0.7.1
        deferred (DrainExporters): 1 active lease on ti-jacinto-j784s4xevm-01
    device spec updated, rendered version 41 -> 42

Exporter host: ti-am69-02-sidekick-01
    device: not found for selector jumpstarter.dev/exporter-host=ti-am69-02-sidekick-01 (pending enrollment)

Summary
  Hosts:      2 processed (0 ok, 1 changed, 0 failed, 1 pending)
```

### Moving a host from SSH to Flight Control

Because the file layout is identical, migration is in place:

1. Enroll and bind the host as described above, while it stays managed by SSH.
   Make `containerImage` match the image the host runs (`bootc status`);
   otherwise the first apply also switches the OS image and reboots the host.
2. Replace `management.ssh` with `management.flightctl` and run `apply`. The
   agent's first update writes files with the content they already have; the
   activation hook sees them as created and runs `systemctl start`, which is a
   no-op for running exporters.
3. Remove the SSH credentials for the host from the lab-config variables.

Moving back to SSH requires removing the `jumpstarter.` config providers from
the Device first (with `flightctl edit device/<name>`, or by decommissioning
the device); otherwise the agent keeps enforcing them.

### API / Protocol Changes

All changes are in lab-config's own configuration schema
(`meta.jumpstarter.dev/v1alpha1`) and are additive. The Jumpstarter
controller CRDs, the gRPC protocol and the exporter are unchanged.

`ExporterHost.spec.management` before:

```go
type Management struct {
	SSH SSHCredentials `json:"ssh,omitempty"`
}
```

After:

```go
type Management struct {
	SSH       SSHCredentials       `json:"ssh,omitempty"`
	Flightctl *FlightctlManagement `json:"flightctl,omitempty"`
}

type FlightctlManagement struct {
	InstanceRef      FlightctlInstanceRef   `json:"instanceRef"`
	Selector         string                 `json:"selector,omitempty"`
	DeviceName       string                 `json:"deviceName,omitempty"`
	OSUpdateStrategy OSUpdateStrategy       `json:"osUpdateStrategy,omitempty"`
	UpdatePolicy     *FlightctlUpdatePolicy `json:"updatePolicy,omitempty"`
}

type FlightctlInstanceRef struct {
	Name string `json:"name"`
}

type OSUpdateStrategy string

const (
	OSUpdateImmediate      OSUpdateStrategy = "Immediate"
	OSUpdateDrainExporters OSUpdateStrategy = "DrainExporters"
)

type FlightctlUpdatePolicy struct {
	DownloadSchedule *FlightctlUpdateSchedule `json:"downloadSchedule,omitempty"`
	UpdateSchedule   *FlightctlUpdateSchedule `json:"updateSchedule,omitempty"`
}

type FlightctlUpdateSchedule struct {
	At                 string `json:"at"`
	TimeZone           string `json:"timeZone,omitempty"`
	StartGraceDuration string `json:"startGraceDuration"`
}
```

New kind `FlightctlInstance`:

```go
type FlightctlInstanceSpec struct {
	Endpoint           string                  `json:"endpoint"`
	Organization       string                  `json:"organization,omitempty"`
	Token              string                  `json:"token,omitempty"`
	TLS                FlightctlTLS            `json:"tls,omitempty"`
	Secrets            *FlightctlSecretStaging `json:"secrets,omitempty"`
	AllowInlineSecrets bool                    `json:"allowInlineSecrets,omitempty"`
	Notes              string                  `json:"notes,omitempty"`
}

type FlightctlTLS struct {
	CAData             string `json:"caData,omitempty"`
	InsecureSkipVerify bool   `json:"insecureSkipVerify,omitempty"`
}

type FlightctlSecretStaging struct {
	KubeContext string `json:"kube-context,omitempty"`
	Kubeconfig  string `json:"kubeconfig,omitempty"`
	Namespace   string `json:"namespace"`
}
```

The doc comment of `ExporterHost.spec.containerImage` changes from "the
container image to be used for the exporter" to "the bootc image the exporter
host runs", which is how it is used in practice.

New `apply` flags:

| Flag | Meaning |
| --- | --- |
| `--wait` | After writing, poll each Flight Control host until it has applied the new spec, the update failed, or the timeout expired. Exit non-zero if any host failed or timed out. |
| `--wait-timeout` | Default `30m`. |

`--parallel`, `--dry-run`, `--prune`, `--filter-exporters` and
`--debug-configs` apply to Flight Control hosts with the meanings they have
for SSH hosts.

New lint rules:

- `management.ssh` and `management.flightctl` are mutually exclusive.
- `management.flightctl.instanceRef.name` references an existing
  `FlightctlInstance`.
- `selector` and `deviceName` are mutually exclusive, and `selector` parses as
  a label selector.
- A `FlightctlInstance` without `secrets` must set `allowInlineSecrets: true`,
  and every apply then warns that tokens are stored unencrypted.
- `updatePolicy` schedules parse as cron expressions with valid IANA time
  zones and Go durations.
- Warning: a Flight Control host whose `containerImage` uses the `latest` tag
  or no tag.
- Warning: an exporter template on a Flight Control host that hard-codes its
  container image instead of using `$( params.container_image )`, because
  such images are not pinned (DD-9).

### Hardware Considerations

- **Network.** The agent needs outbound TLS connections to the Flight Control
  agent endpoint and to the container registries. No inbound port is needed.
- **OS mode.** OS image management requires image mode (bootc). Package-mode
  devices reject `spec.os.image`; for them lab-config does not own `spec.os`
  and warns if the host declares `containerImage`. Exporter files, hooks and
  monitoring work in both modes.
- **Reboots.** An OS update reboots the host and ends every exporter session
  on it. The device under test is not power cycled, but an interrupted flash
  or boot can leave it in an unknown state; DD-8 describes how disruption is
  controlled.
- **Offline hosts.** The desired spec is stored by Flight Control and applied
  when the host reconnects. `status.summary.status` reports `Unknown` or
  `AwaitingReconnect` meanwhile.
- **Privileges.** Exporters keep running as root Podman containers or root
  systemd services with access to `/dev` and `/run/udev`, exactly as with SSH.
  The agent runs as root and executes the activation hook as root.

## Design Decisions

### DD-1: Integrate through the Device API

**Alternatives considered:**

1. **Device API** - One `Device` per exporter host; lab-config edits the part
   of the device's spec it owns.
2. **Fleet API** - One `Fleet` per group of hosts, with per-host content
   carried by labels or by per-device Kubernetes Secrets.
3. **ResourceSync** - lab-config renders manifests into a Git repository and
   Flight Control syncs them.

**Decision:** Device API.

**Rationale:** lab-config's unit of configuration is a host with a varying set
of exporters, each from its own template with its own parameters. Fleet
templates cannot loop, can carry per-device data only in label values of at
most 63 characters from a restricted alphabet, and lock the specs of the
devices they own, so fleets would force all per-host content into secrets
and hide it from review (see the suitability analysis). ResourceSync accepts
only `Fleet` and catalog resources and would put tokens into Git. The Device
API matches lab-config's per-host model one to one and leaves fleets
available for a later, opt-in mode for homogeneous pools.

### DD-2: Deliver exporters as configuration files, not as Flight Control applications

**Alternatives considered:**

1. **Configuration files and a hook** - Config providers write the same files
   as the SSH backend; an `afterUpdating` hook activates them.
2. **Quadlet applications** - Each exporter becomes an `appType: quadlet`
   application with inline unit content.
3. **Container applications** - Each exporter becomes an `appType: container`
   application.

**Decision:** Configuration files and a hook.

**Rationale:** The decisive requirement is the graceful restart: a changed
exporter must keep serving its current lease and restart only when the lease
ends, which the exporter implements on `SIGHUP`. Flight Control updates a
quadlet application by stopping its units, reloading systemd and starting
them again, which terminates the lease. Container applications cannot
express `--privileged` or host device mounts, which most exporters need.
Applications also namespace their unit names, which would change the service
names that operators, the SSH backend and the documentation rely on, and
they cannot carry `SystemdServiceTemplate`-based exporters at all. With files,
existing templates render unchanged and hosts can switch backends in place.
The cost is that lab-config maintains the activation logic and that exporter
health comes from `status.systemd` rather than `status.applications`.

### DD-3: Own a well-defined part of the Device spec

**Alternatives considered:**

1. **Own the whole spec** - Replace `spec` on every apply.
2. **Own a fragment identified by convention** - `spec.os` when the host
   declares `containerImage`, config providers named `jumpstarter.*`, the
   exporter units in `spec.systemd.matchPatterns`, and `spec.updatePolicy`
   when the host declares one.
3. **Server-side field managers** - Let the server track which client owns
   which field, as Kubernetes server-side apply does.

**Decision:** Own a fragment identified by convention.

**Rationale:** Exporter hosts are often also subject to organization-wide
configuration delivered through Flight Control, such as registry
credentials, monitoring agents or security baselines; replacing the whole
spec would delete it on every run. Flight Control has no field managers.
The naming convention is the ownership record: the config providers present
on a device tell lab-config which exporters it deployed earlier, which is
what pruning and the `matchPatterns` bookkeeping need, and nothing else has to
be stored. The `jumpstarter.` provider prefix is reserved for lab-config.

### DD-4: Bind hosts to devices with a label selector

**Alternatives considered:**

1. **Device name only** - Store the device fingerprint in the `ExporterHost`.
2. **Label selector with an exactly-one rule** - Default selector
   `jumpstarter.dev/exporter-host=<ExporterHost name>`, with `deviceName` as
   an override.
3. **Hostname** - Match the `alias` label the agent derives from the
   hostname, or `status.systemInfo.hostname`.

**Decision:** Label selector with an exactly-one rule, `deviceName` as an
override.

**Rationale:** Fingerprints are opaque and only exist after enrollment, which
makes them poor GitOps content, although an explicit name stays available. A
label set when the enrollment request is approved binds the host to an
identity that an authorized person accepted, and requires no change to the
lab repository when a host is re-provisioned. The hostname is reported by the
device itself and can be duplicated or spoofed before approval; it can still
be chosen explicitly with `selector: alias=<hostname>`. Requiring exactly one
match makes a misconfigured selector fail loudly instead of sending tokens to
the wrong machine.

### DD-5: Deliver exporter tokens through Kubernetes Secret references

**Alternatives considered:**

1. **Inline** - Put the rendered exporter configuration, token included, into
   an inline config provider.
2. **Kubernetes Secret reference** - lab-config writes one Secret per exporter
   holding its rendered configuration file, and the Device spec references it
   with `secretRef`.
3. **HTTP config provider** - Serve rendered configurations from a new
   endpoint that Flight Control fetches with credentials it stores encrypted.
4. **Token file** - Teach the exporter to read its token from a separate file
   and deliver only that file as a secret.
5. **No static token** - Let the exporter authenticate with an identity
   derived from the device's Flight Control certificate or an OIDC workload
   identity.

**Decision:** Kubernetes Secret reference by default. Inline only when the
`FlightctlInstance` sets `allowInlineSecrets: true`.

**Rationale:** Flight Control encrypts the rendered configuration but not the
device spec, explicitly warns against storing tokens in unprotected fields,
and shows the spec to anyone allowed to read devices. With `secretRef` the
token never appears in the spec: Flight Control reads the Secret while
rendering, stores the result encrypted and reports the Secret's
`resourceVersion` in `status.dependencySync`, so lab-config can confirm the
device consumed the current version. The cost is a dependency on Flight
Control running in Kubernetes with read access to the Secret namespace, and
lab-config needing credentials to write Secrets there, the same way it already
holds kube contexts for Jumpstarter controllers.
Standalone Flight Control deployments, which have no Kubernetes API, can opt
into inline delivery knowingly. The HTTP provider needs a new service. A
token file and device-derived identities are better long-term answers but
change the Jumpstarter exporter and controller, so they are left to Future
Possibilities.

### DD-6: Activate changes with a hook delivered by lab-config

**Alternatives considered:**

1. **Hook delivered by lab-config** - A rule file under
   `/etc/flightctl/hooks.d/afterupdating/` and a helper script, both in the
   `jumpstarter.activation` config provider.
2. **Hook baked into the image** - The same files under
   `/usr/lib/flightctl/hooks.d/` in the exporter host bootc image.
3. **systemd path units** - Units on the host that watch the exporter
   directories and react to changes, independent of Flight Control.

**Decision:** Hook delivered by lab-config.

**Rationale:** The activation logic must match the file layout that
lab-config writes, so it should be versioned and shipped together with
lab-config rather than with every exporter host image. Hook rule files are
read when the hook runs, so a new version takes effect in the same update
that delivers it. Path conditions hand the hook the exact lists of created,
updated and removed files, which path units cannot provide, and the hook
runs inside the agent's update transaction, after the files are written. The
helper always exits successfully: a failing `afterUpdating` action rolls back
the whole update and fails that rendered version permanently, so a single
broken exporter unit would otherwise block configuration changes for every
exporter on the host. Unit failures are reported through `status.systemd`
instead. Images may additionally ship their own hooks; lab-config only owns
the file names it writes.

### DD-7: Declare the OS image with `spec.os.image`

**Alternatives considered:**

1. **Map `ExporterHost.spec.containerImage` to `spec.os.image`** - Flight
   Control downloads, switches and rolls back the bootc image.
2. **Leave the OS alone** - Keep the SSH semantics and rely on the host's
   bootc update timer.
3. **A new field** - Add `spec.osImage` and deprecate `containerImage`.

**Decision:** Map `containerImage` to `spec.os.image`. When the field is
empty, lab-config does not own `spec.os`.

**Rationale:** Declaring the image is one of the main gains of the new
backend: Flight Control stages the download, switches images rather than only
upgrading the tracked one, and rolls back automatically when greenboot health
checks fail after the reboot. The field already holds the bootc image in
practice and the README already describes "exporter hosts will be updated
with the new bootc image", so a new field would only add a migration. Its
misleading doc comment is corrected. The SSH backend keeps ignoring the
field; making it enforce the image is a separate change.

### DD-8: Coordinate OS updates with leases in lab-config

**Alternatives considered:**

1. **Immediate** - Write the new image on the next apply; Flight Control
   applies it when the agent next syncs, subject to `updatePolicy`.
2. **Maintenance windows only** - Pass `updatePolicy` through and rely on
   time-based windows.
3. **Check leases at apply time** - Write the new image only if no exporter on
   the host holds a lease at that moment.
4. **Drain exporters** - Disable the host's exporters for new leases, wait
   until no lease is active, then write the new image, and re-enable the
   exporters once the host reports the new image.
5. **Device-side gate** - A `beforeUpdating` or `beforeRebooting` hook that
   fails or blocks while an exporter is leased.

**Decision:** Offer `Immediate` (the default, which behaves like SSH today)
and `DrainExporters`, both combinable with `updatePolicy` windows.

**Rationale:** The device-side gate does not work: a failing or timed-out hook
fails that rendered version permanently and rolls it back, and the agent
does not retry it until a new version is rendered. Checking leases only at
apply time leaves a window as long as the image download, often minutes,
during which a new lease can start and then be cut by the reboot. Draining
closes that window with an existing Jumpstarter mechanism, the exporter's
`spec.enabled` field (JEP-0014, Phase 1), which stops new lease assignments
while the exporter stays online. The drain state is derived on every run from
the declared image, the device's target image and its running image, so
lab-config stores nothing extra. Draining keeps exporters out of service
until a later run sees the update completed, so it needs lab-config to run
on a schedule or with `--wait`; that is why it is opt-in rather than the
default. Maintenance windows remain useful on their own and in combination.

### DD-9: Pin exporter container images by digest

**Alternatives considered:**

1. **Digest pinning** - Resolve the template's `containerImage` to a digest
   at render time and render `repository:tag@sha256:<digest>` as
   `$( params.container_image )`.
2. **Podman auto-update** - Use `AutoUpdate=registry` in the units and let
   Podman restart containers when the tag moves.
3. **Immutable tags only** - Require templates to use immutable tags.

**Decision:** Digest pinning, for hosts managed by Flight Control.

**Rationale:** Flight Control does not reconcile floating tags, so without
pinning a moved tag never reaches a running exporter. A pinned reference
turns a moved tag into a file change, which the activation hook handles with
the usual graceful restart, so updates stay lease-aware and visible in
`apply --dry-run`. Podman auto-update restarts units with `systemctl
restart`, which ends active leases. Requiring immutable tags breaks the
existing practice of tracking release branches such as `release-0.6`. The
digest of the multi-architecture index is used, so hosts of any
architecture resolve the same reference. If a digest cannot be resolved,
lab-config keeps the reference currently deployed for that exporter and
warns; an exporter that has never been deployed is not deployed until its
image resolves.

### DD-10: A host-level backend interface

**Alternatives considered:**

1. **Implement the existing `HostManager` interface** - Keep per-exporter
   `Apply`, `RunHostCommand`, `GetBootcStatus` and `HandleBootcUpgrade`, and
   implement them for Flight Control.
2. **A host-level interface** - Pass a backend the complete desired state of a
   host, the OS image and all exporters, and let it plan and apply the change
   as a whole.

**Decision:** A host-level interface; the SSH implementation adapts to it.

**Rationale:** The Flight Control backend must write a host's exporters in a
single patch: one patch per exporter would produce one rendered version and
one agent update cycle per exporter, and a patch built from a single
exporter could not tell an absent exporter from a removed one.
`RunHostCommand` and the bootc methods have no declarative equivalent, and a
meaningful `--dry-run` diff must be computed over the whole spec. The SSH
backend can loop over the exporters internally, so its behavior does not
change.

### DD-11: Asynchronous apply with optional waiting

**Alternatives considered:**

1. **Fire and forget** - Return as soon as Flight Control accepts the spec.
2. **Always wait** - Block until every host has converged.
3. **Asynchronous by default, `--wait` on request.**

**Decision:** Asynchronous by default, `--wait` on request.

**Rationale:** Flight Control's value is that it converges hosts that are
offline, rebooting or waiting for a maintenance window, which can take hours;
blocking CI on that by default would turn every offline host into a failed
pipeline. Release pipelines that must gate on convergence, and the
`DrainExporters` strategy when lab-config does not run on a schedule, use
`--wait`. Without `--wait`, success means every spec was accepted; the last
known device status is always reported.

### DD-12: Generate a minimal Flight Control client from the OpenAPI document

**Alternatives considered:**

1. **Import the flightctl Go module** - Use `api/core/v1beta1` types and the
   public `client` package.
2. **Generate a client** - Run `oapi-codegen` on
   `api/core/v1beta1/openapi.yaml`, pinned to a Flight Control release and
   restricted to the device operations lab-config uses.
3. **Shell out to the `flightctl` CLI.**

**Decision:** Generate a client.

**Rationale:** The public `client` package returns
`*apiclient.ClientWithResponses` from `internal/api/client`, a type lab-config
cannot name, and `api/core/v1beta1` imports flightctl internal packages, so
importing it pulls Flight Control's entire dependency graph (about 490
`go.mod` lines) into lab-config and forces toolchain upgrades in lockstep.
The CLI adds a runtime dependency, brittle output parsing and login state
kept outside lab-config. A generated client keeps the contract explicit and small,
pairs naturally with the `Flightctl-API-Version: v1beta1` header, and is easy
to test against an `httptest` server.

## Design Details

### Architecture

```{mermaid}
flowchart LR
    repo[GitOps repository] --> lc[jumpstarter-lab-config apply]
    lc -->|Exporter and Client objects| jc[Jumpstarter controller]
    lc -->|SSH and SFTP| sshhost[SSH-managed exporter host]
    lc -->|exporter credential Secrets| ns[Secret namespace]
    lc -->|Device PATCH| fc[Flight Control API]
    ns -.->|read while rendering| fc
    agent[Agent on exporter host] -->|mTLS, outbound only| fc
    agent --> files[unit files and exporter configs]
    files --> exp[Jumpstarter exporters]
    exp -->|gRPC| jc
```

The host loop in `internal/exporter/host` keeps filtering instances and
rendering templates, then hands each host's desired state to the backend its
`spec.management` selects. The SSH backend wraps today's `SSHHostManager`;
the Flight Control backend lives in a new `internal/exporter/flightctl`
package with the generated client under
`internal/exporter/flightctl/client`.

```go
type HostBackend interface {
	Plan(ctx context.Context, desired HostDesiredState) (HostPlan, error)
	Apply(ctx context.Context, plan HostPlan) (HostResult, error)
	Close() error
}

type HostDesiredState struct {
	Host      *api.ExporterHost
	OSImage   string
	Exporters []RenderedExporter
	Retained  []string
	Prune     bool
}

type RenderedExporter struct {
	Name          string
	ContainerUnit string
	ServiceUnit   string
	Config        string
}
```

`Retained` lists exporters on the host that must stay exactly as deployed:
dead or unmanaged instances, and instances excluded by `--filter-exporters`.
`--dry-run` calls `Plan` only.

### Rendering the owned fragment

For each host lab-config builds:

| Device spec field | Content |
| --- | --- |
| `spec.os.image` | `ExporterHost.spec.containerImage`, if set, subject to the OS update strategy. |
| `spec.updatePolicy` | `management.flightctl.updatePolicy`, if set. |
| `spec.config[jumpstarter.activation]` | The hook rule file and the helper script. |
| `spec.config[jumpstarter.exporter.<name>]` | The rendered `SystemdContainerTemplate` at `/etc/containers/systemd/<name>.container` or `SystemdServiceTemplate` at `/etc/systemd/system/<name>.service`. |
| `spec.config[jumpstarter.exporter.<name>.credentials-<hash>]` | `secretRef` to the exporter's credential Secret, mounted at `/etc/jumpstarter/exporters`. With `allowInlineSecrets`, an inline file at `/etc/jumpstarter/exporters/<name>.yaml` instead. |
| `spec.systemd.matchPatterns` | `<name>.service` for every exporter. |

Names follow the constraints Flight Control validates: config provider names
are dot-separated DNS labels, so exporter names, which are Kubernetes object
names, fit as one label; the Secret name must be a single DNS-1123 label of at
most 63 characters, so it is `jumpstarter-<name>`, or a truncated name
followed by an 8 character hash when that does not fit. `<hash>` in the
credentials provider name is the first 8 hex digits of the SHA-256 of the
rendered configuration. Every content change of a Secret therefore also
changes the Device spec, which re-renders the device without relying on
Flight Control's optional secret change detection, and shows up in
`--dry-run`.

### Merging and writing

lab-config reads the device and merges the fragment into its spec:

1. Config providers whose names do not start with `jumpstarter.` are kept in
   place.
2. `jumpstarter.exporter.<name>` providers for desired exporters are replaced.
3. Providers of `Retained` exporters are copied unchanged.
4. Providers of exporters that are neither desired nor retained are removed
   only with `--prune`; otherwise they are reported as pending removal, in
   line with how lab-config treats `Exporter` objects. Removal stops the
   exporter immediately, so an exporter that still holds a lease is kept and
   reported until a later run finds it idle.
5. `matchPatterns` entries for exporters lab-config deployed before, as
   derived from the provider names, are replaced by the desired set; other
   entries are kept.
6. `spec.os` and `spec.updatePolicy` are set or left alone as described above.

It then sends one JSON Patch per host. Every owned path that is changed is
first asserted with a `test` operation against the value just read, then
written with `add`, `replace` or `remove`:

```json
[
  {"op": "test", "path": "/spec/config", "value": ["...as read..."]},
  {"op": "replace", "path": "/spec/config", "value": ["...merged..."]},
  {"op": "test", "path": "/spec/os", "value": {"image": "quay.io/auto-lab/jumpstarter-exporter-bootc:0.7.0"}},
  {"op": "replace", "path": "/spec/os", "value": {"image": "quay.io/auto-lab/jumpstarter-exporter-bootc:0.7.1"}}
]
```

If a `test` fails because another client changed the device in the
meantime, Flight Control rejects the whole patch; lab-config re-reads,
re-merges and retries up to three times before reporting the host as
failed. Status heartbeats do not cause retries, because they do not touch
`spec`. Credential Secrets are written before the patch, so a spec never
references a Secret that does not exist yet; Secrets of exporters removed with
`--prune` are deleted after the patch succeeded.

### Activation hook

Rule file at
`/etc/flightctl/hooks.d/afterupdating/50-jumpstarter-exporters.yaml`:

```yaml
- if:
    - path: /etc/jumpstarter/exporters/
      op: [removed]
  run: /etc/jumpstarter/lab-config/activate-exporters stop ${ RemovedFiles }
  timeout: 1m
- if:
    - path: /etc/containers/systemd/
      op: [created, updated, removed]
  run: /usr/bin/systemctl daemon-reload
  timeout: 2m
- if:
    - path: /etc/jumpstarter/exporters/
      op: [created, updated]
  run: /etc/jumpstarter/lab-config/activate-exporters protect ${ CreatedFiles } ${ UpdatedFiles }
  timeout: 30s
- if:
    - path: /etc/jumpstarter/exporters/
      op: [created]
  run: /etc/jumpstarter/lab-config/activate-exporters start ${ CreatedFiles }
  timeout: 1m
- if:
    - path: /etc/jumpstarter/exporters/
      op: [updated]
  run: /etc/jumpstarter/lab-config/activate-exporters reload ${ UpdatedFiles }
  timeout: 1m
- if:
    - path: /etc/containers/systemd/
      op: [updated]
  run: /etc/jumpstarter/lab-config/activate-exporters reload ${ UpdatedFiles }
  timeout: 1m
- if:
    - path: /etc/systemd/system/
      op: [updated]
  run: /etc/jumpstarter/lab-config/activate-exporters reload ${ UpdatedFiles }
  timeout: 1m
```

Flight Control's built-in rule already runs `systemctl daemon-reload` for
changes below `/etc/systemd/system/` and runs first, because
`00-default.yaml` sorts before `50-jumpstarter-exporters.yaml`. Quadlet units
need the explicit reload above, which is exactly the step lab-config #34 found
missing.

The helper at `/etc/jumpstarter/lab-config/activate-exporters`:

```bash
#!/usr/bin/bash
set -u

is_exporter() { [ -f "/etc/jumpstarter/exporters/$1.yaml" ]; }

start_exporter() {
  if [ -f "/etc/systemd/system/$1.service" ]; then
    systemctl enable --now --no-block "$1.service"
  else
    systemctl start --no-block "$1.service"
  fi
}

reload_exporter() {
  if systemctl is-active --quiet "$1.service"; then
    podman kill --signal SIGHUP "$1" 2>/dev/null || systemctl kill --signal SIGHUP "$1.service"
  else
    systemctl restart --no-block "$1.service"
  fi
}

action="$1"
shift
for path in "$@"; do
  name="$(basename "${path%.*}")"
  case "$action" in
    stop) systemctl stop --no-block "$name.service" ;;
    protect) chmod 0600 "$path" ;;
    start) is_exporter "$name" && start_exporter "$name" ;;
    reload) is_exporter "$name" && reload_exporter "$name" ;;
  esac
done
exit 0
```

The exporter configuration file is the membership marker: a unit is treated
as an exporter only if `/etc/jumpstarter/exporters/<name>.yaml` exists, so
units written by other configuration on the same host are left to their own
owners. Removal is keyed on the configuration file because lab-config always
removes it together with the unit. Starts and restarts use `--no-block`
because pulling a large exporter image can outlast any reasonable hook
timeout, and a timed-out action would roll back the update. `protect` narrows
the exporter configuration files to `0600`, since Flight Control writes
secret-derived files with `0644` and offers no way to change that.

After a reboot into a new OS image the hook runs as well, and running
exporters receive a `SIGHUP`; they have no lease at that point and restart
immediately, which is harmless.

### Exporter credential Secrets

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: jumpstarter-ti-jacinto-j784s4xevm-01
  namespace: jumpstarter-exporter-configs
  labels:
    app.kubernetes.io/managed-by: jumpstarter-lab-config
    jumpstarter.dev/exporter: ti-jacinto-j784s4xevm-01
    jumpstarter.dev/exporter-host: ti-jacinto-j784s4xevm-01-sidekick-01
type: Opaque
stringData:
  ti-jacinto-j784s4xevm-01.yaml: |
    apiVersion: jumpstarter.dev/v1alpha1
    kind: ExporterConfig
    ...
    token: "..."
```

The key becomes the file name below `mountPath`. Flight Control's worker
service account needs `get` on Secrets in this namespace. A `Role` and
`RoleBinding` in the namespace, as described in Flight Control's fleet
documentation, is preferred over the Helm value
`worker.clusterLevelSecretAccess`, which lets the worker read Secrets in every
namespace. lab-config's credentials for the namespace need `get`, `create`,
`update` and `delete` on Secrets and nothing else.

### OS update strategies

`Immediate` writes the declared image on the next apply. Before writing,
lab-config looks up the host's exporters in their Jumpstarter controllers and
warns when any of them holds a lease (`Exporter.status.leaseRef`), since that
lease will be interrupted when the agent reboots the host.

`DrainExporters` derives the host's state on every run from three values: the
declared image (`ExporterHost.spec.containerImage`), the device's target
image (`spec.os.image`) and its running image (`status.os.image`).

```{mermaid}
stateDiagram-v2
    [*] --> Steady
    Steady --> Draining: declared image differs from target image
    Draining --> Draining: an exporter on the host holds a lease
    Draining --> Updating: no lease held, target image written
    Updating --> Steady: running image equals target and status UpToDate
    Updating --> Failed: Updating condition reports Error
    Failed --> Draining: declared image changed again
```

| State | Condition | Effect on the host's exporters |
| --- | --- | --- |
| Steady | declared = target = running | `spec.enabled` as declared |
| Draining | declared differs from target | `spec.enabled: false`; target unchanged while any exporter holds a lease |
| Updating | target differs from running, update in progress | `spec.enabled: false` |
| Failed | target differs from running, `Updating` reason `Error` | `spec.enabled` as declared again, since the host rolled back to its old image and is usable; the failure is reported on every run |

lab-config computes each exporter's effective `spec.enabled` as the declared
value combined with the host's state, so the drain is visible in the normal
`Exporter` diff and is undone by the same mechanism. With `--wait`, a single
run walks through all states; without it, each scheduled run advances the
host as far as it can.

Disabled exporters are filtered out of every selector-based lease. A lease
that names an exporter explicitly and sets `allowDisabled` can still acquire
a drained exporter; that field exists for investigating broken exporters and
is treated as a deliberate override, so lab-config reports such a lease like
any other active lease and keeps waiting.

### Status and convergence

| Device field | Reported as |
| --- | --- |
| `status.summary.status` | Host health: `Online`, `Degraded`, `Error`, `Rebooting`, `PoweredOff`, `Unknown`, `AwaitingReconnect` or `ConflictPaused`. |
| `status.updated.status` and the `Updating` condition reason | Update progress: `Preparing`, `ReadyToUpdate`, `ApplyingUpdate`, `Rebooting`, `RollingBack`, `Updated` or `Error`. |
| `status.config.renderedVersion` and the `device-controller/renderedVersion` annotation | Whether the agent runs the latest rendered spec. |
| `status.os.image` | The running OS image. |
| `status.systemd[]` | Per exporter unit: load, active and sub state. |
| `status.dependencySync.configRefs` | The Secret `resourceVersion` the device consumed. |
| `/api/v1/devices/{name}/lastseen` | Last contact. |

A host has converged when its rendered version equals the annotation,
`status.updated.status` is `UpToDate`, and, if lab-config owns `spec.os`,
`status.os.image` equals the target. `--wait` polls at the agent's status
interval until every host has converged, reports `Error`, or the timeout
expires.

### Failure modes

| Failure | Behavior |
| --- | --- |
| API unreachable, 5xx or 429 | Retried through the existing retry queue with backoff; reported as failed after the last attempt. |
| 401 or 403 | Reported immediately for every host of that `FlightctlInstance`; not retried. |
| No device matches | Host reported as pending enrollment and skipped; the run does not fail. |
| Several devices match | Host fails; nothing is written. |
| Device owned by a fleet | Host fails with the owning fleet's name; nothing is written. |
| Device decommissioning or decommissioned | Host fails; nothing is written. |
| `test` operation fails | Re-read, re-merge, retry up to three times, then fail. |
| Secret write fails | Host fails before its spec is patched. |
| Spec rejected by validation (400) | Host fails with Flight Control's message. |
| Agent offline | Spec accepted; host reported as pending with its last-seen time; `--wait` times out. |
| Update fails on the device | Reported with the `Updating` condition message; the agent has already rolled back and does not retry that version. |
| Exporter unit fails to start | Reported from `status.systemd`; the update itself succeeds. |
| Device in `ConflictPaused` | Reported; lab-config never resumes devices on its own. |

### Security considerations

- **Least privilege.** The Flight Control token needs to list, get and patch
  devices in one organization; a dedicated service account bound to
  `flightctl-operator` or a narrower custom role is recommended over personal
  tokens. The Kubernetes credentials for secret staging are limited to one
  namespace.
- **Tokens.** Tokens reach the host through the encrypted rendered
  configuration, never through the device spec, unless inline delivery is
  explicitly allowed, in which case every apply warns. `--dry-run` and
  `--debug-configs` mask token values with the existing `sanitizeDiff` rules.
- **Host authenticity.** Devices authenticate with certificates issued at
  enrollment, and binding requires an approval-time label matching exactly
  one device, which replaces today's unverified SSH host keys.
- **Code execution.** Anyone who can patch a device's spec can run commands
  as root on it through hooks. That is inherent to Flight Control and equally
  true of configuration files; the patch permission must be treated as root
  access to the host.
- **Audit.** Flight Control records every spec change as a `ResourceUpdated`
  event, which gives a better audit trail than SSH sessions.

## Test Plan

### Unit Tests

All in lab-config, written test first, with an `httptest` server that serves
recorded `v1beta1` responses:

- Rendering the owned fragment from an `ExporterHost` and its exporters,
  compared against golden files, for container and service exporters, secret
  and inline delivery, with and without `containerImage` and `updatePolicy`.
- Merging: foreign config providers and `matchPatterns` entries survive;
  retained exporters are copied unchanged; pruning happens only with
  `--prune`.
- JSON Patch generation: `test` before every change, `add` when a path is
  absent, retry after a failed `test`.
- Device resolution: no match, one match, several matches, fleet-owned,
  decommissioning.
- Naming: Secret names at the 63 character boundary, hash suffixes, provider
  names.
- The `DrainExporters` state derivation, as a table of declared, target and
  running images, lease presence and update conditions.
- Digest resolution against a fake registry, including multi-architecture
  indexes and resolution failures.
- The new lint rules.
- The activation helper, run against a temporary root with stubbed
  `systemctl` and `podman`.

### Integration Tests

- A Flight Control service deployed with its Helm chart in kind, using
  Kubernetes authentication, with Flight Control's device simulator, which
  runs real agent processes and enrolls them automatically, providing a few
  devices. The tests run `apply` and assert the resulting Device specs, the
  rendered specs, the Secret references and status reporting. OS images are
  left out of these specs, as the simulator documentation recommends; OS
  updates are covered by the hardware tests.
- A real agent in a systemd-enabled container in package mode, which
  exercises config providers and hooks without bootc: create, change and
  remove exporters, and check that the units are started, signalled and
  stopped.

### Hardware-in-the-Loop Tests

On one exporter host in the project lab, attached to a board already used by
the HiL suite, enrolled in a test Flight Control instance:

- Deploy an exporter, lease the board, change the exporter configuration
  during the lease, and verify that the lease survives and the exporter
  restarts after the lease ends.
- Roll out a new bootc image with `DrainExporters` while a lease is active,
  and verify that no new lease is granted, that the update starts only after
  the lease is released, and that the exporter is enabled again afterwards.
- Roll out a deliberately broken image and verify the greenboot rollback and
  the failure report.

### Manual Verification

Reviewers can follow the "Preparing a host" steps against a Flight Control
instance with a bootc virtual machine, run `apply --dry-run` and `apply
--wait`, and inspect the device with `flightctl get device/<name> -o yaml`.

## Acceptance Criteria

- [ ] An `ExporterHost` with `management.flightctl` is deployed end to end:
      exporter units and configurations written, exporters running and
      registered with the controller.
- [ ] SSH-managed hosts behave exactly as before; the existing test suite
      passes unchanged.
- [ ] With secret staging configured, no exporter token appears in the
      Device spec returned by the Flight Control API.
- [ ] Changing an exporter configuration during an active lease does not end
      the lease; the exporter restarts after the lease ends.
- [ ] Changing `containerImage` rolls the host to the new bootc image; with
      `DrainExporters`, no lease is interrupted.
- [ ] Config providers, applications and `matchPatterns` entries not created
      by lab-config survive any number of applies.
- [ ] `apply --dry-run` makes no changes to Flight Control or Kubernetes and
      prints a diff with masked secrets.
- [ ] `apply --wait` exits non-zero when a host's update fails or times out.
- [ ] The lab-config README documents the backend, the host image
      requirements, the RBAC needed for secret staging and the migration from
      SSH.
- [ ] Unit test coverage of the new packages is at least 80 percent.

## Graduation Criteria

### Experimental

- The backend ships in a lab-config release, documented as experimental, and
  `apply` prints a notice for each Flight Control host.
- Feedback wanted: binding ergonomics, secret staging setup, drain behavior
  in real labs, and status output.

### Stable

- At least one production lab has managed ten or more exporter hosts through
  the backend for 30 days without manual repair.
- The integration tests run in lab-config CI against the oldest supported and
  the latest Flight Control release.
- No open bugs that cause lost or interrupted leases.

## Backward Compatibility

- **Configuration.** Every schema change is additive. Existing repositories
  keep working, and hosts without `management.flightctl` use SSH as before.
- **Older lab-config versions.** lab-config decodes resources strictly, so an
  older binary rejects `FlightctlInstance` documents and the new fields.
  Repositories that adopt the backend must require the lab-config version that
  introduces it, which CI pipelines already pin.
- **`containerImage` semantics.** The field becomes enforced for hosts managed
  by Flight Control and stays ignored for SSH hosts. Hosts whose
  `containerImage` is stale will be switched to it when they move to Flight
  Control; the migration guide calls this out.
- **Jumpstarter components.** No controller, CRD, protocol or exporter change.
  `DrainExporters` needs a controller that supports `Exporter.spec.enabled`,
  which lab-config already maps from `ExporterInstance.spec.enabled`.
- **Flight Control versions.** The backend speaks `v1beta1`, which Flight
  Control supports throughout 1.x, and sends `Flightctl-API-Version: v1beta1`
  so that newer servers keep answering in that version. The oldest supported
  Flight Control release is fixed during implementation (see Unresolved
  Questions).
- **Switching back.** A host moved back to SSH must have its `jumpstarter.`
  config providers removed from its Device first, as described in the
  migration section.

## Consequences

### Positive

- Exporter hosts can live anywhere with outbound connectivity; CI needs no
  network path to them.
- CI holds no SSH keys; host identity is verified cryptographically.
- Hosts converge continuously, including those that were offline during
  `apply`, and configuration changes are applied atomically with rollback.
- The bootc image becomes declared state with staged downloads, maintenance
  windows and automatic rollback.
- OS updates can be made lease-aware with `DrainExporters`.
- Host and exporter status are visible in Flight Control and in lab-config
  output without logging in to hosts.
- Labs that already use Flight Control manage exporter hosts with the same
  tools as their other edge devices.

### Negative

- A Flight Control deployment becomes a prerequisite for this backend, and
  secret staging requires it to run in Kubernetes.
- Changes take effect asynchronously, so `apply` no longer proves that a host
  is updated unless `--wait` is used.
- lab-config maintains a second backend, a generated client and the
  activation hook script.
- Exporter hosts must run the Flight Control agent, which adds a component to
  the exporter host image.
- `DrainExporters` keeps exporters out of service until a later run sees the
  update completed.

### Risks

- Flight Control behavior this design relies on (hook failure semantics,
  quadlet update behavior, render-time secret resolution) may change between
  releases; the integration tests are meant to catch that.
- Someone with device patch permissions can edit the `jumpstarter.` providers
  directly; lab-config overwrites such edits on the next run, but a hostile
  edit could run code on the host in between.
- A future Flight Control release may start encrypting or redacting device
  spec fields, or add `mode` to secret files, making some mitigations here
  unnecessary; they are cheap to remove.

## Rejected Alternatives

- **Keep SSH only.** Leaves every problem in the Motivation in place and
  ignores the stated direction of the lab-config README.
- **Replace SSH with Flight Control.** Not every host can run the agent
  (non-bootc hosts, desk setups, hosts owned by other teams); per-host
  selection costs little.
- **Run the existing SSH command sequence through the Flight Control
  console.** Keeps every weakness of imperative reconciliation, depends on an
  interactive WebSocket feature authorized separately, and gains none of the
  declarative benefits.
- **Ansible pull or AAP as the backend.** Solves reachability but not
  declared OS images, rollback or status, and adds another control plane
  instead of reusing the edge-management system organizations already run.
- **A Jumpstarter meta-operator that manages hosts.** The README mentions
  this as a possible long-term evolution; it would still need a host
  management mechanism, and this backend is reusable from an operator.
- **Flight Control applications, the Fleet API and other per-decision
  alternatives** are covered in the Design Decisions.

## Prior Art

- **Flight Control / Red Hat Edge Manager** itself: agent-based, declarative
  management of bootc devices, which this JEP builds on.
- **Kubernetes server-side apply**: field ownership per manager; DD-3 adopts
  the idea through a naming convention because Flight Control has no field
  managers.
- **kured** (Kubernetes Reboot Daemon) and **Zincati with FleetLock** on
  Fedora CoreOS: coordinate reboots by cordoning and draining or by taking a
  lock first. `DrainExporters` is the same pattern, with leases in place of
  pods.
- **Argo CD and Flux**: GitOps reconciliation with pruning gated by explicit
  configuration, mirrored by lab-config's `--prune`.
- **LAVA**: dispatchers (the equivalent of exporter hosts) are typically
  provisioned by administrators with general-purpose configuration management
  tools, outside of LAVA, which is the situation this JEP improves on.

## Unresolved Questions

1. Should `DrainExporters` become the default once labs run lab-config on a
   schedule?
2. Should lab-config offer an opt-in `ownership: Full` mode that replaces the
   whole Device spec, for strictly GitOps-managed hosts?
3. Upstream Flight Control requests: a `mode` field for secret-derived files,
   a retryable "defer" outcome for lifecycle hooks that would allow a
   device-side lease gate, and secret delivery for deployments without
   Kubernetes. Should this JEP wait for any of them?
4. What is the oldest Flight Control release to support? The design needs
   `v1beta1`, `systemd.matchPatterns`, lifecycle hooks, `secretRef` and JSON
   Patch `test`; `status.dependencySync` is only used for reporting.
5. Should `ExporterHost.spec.containerImage` eventually be renamed to
   `osImage`, with a deprecation period?
6. Should the SSH backend adopt `DrainExporters` and start enforcing
   `containerImage` with `bootc switch`?
7. Should the productization part of #1242 be a separate JEP?

## Future Possibilities

These are not part of this proposal.

- **Fleet mode** for pools of identical hosts, to use Flight Control rollout
  policies for OS images.
- **Exporter tokens from a file or from device identity**, so that no static
  token needs to be distributed at all.
- **Location labels**: sync `PhysicalLocation`, rack and tray into device
  labels for selection and for the Flight Control UI.
- **Recovering hung hosts** through `ExporterHost.spec.power`, using the
  host's PDU outlet when the device stops reporting.
- **Enrollment automation**: approve enrollment requests that match hosts
  declared in Git, for example by TPM endorsement key or serial number.
- **Status feedback**: surface Flight Control host status as conditions on
  the Jumpstarter `Exporter` objects of that host.
- **Image builds**: produce exporter host images with Flight Control's image
  builder API.

## Implementation Plan

1. **Backend interface.** Introduce `HostBackend` and adapt the SSH
   implementation, with no behavior change.
2. **Core backend.** `FlightctlInstance`, device binding, the owned fragment,
   secret staging and inline opt-in, merge and patch, dry-run diff, status
   reporting, `Immediate` strategy and `updatePolicy`, lint rules.
3. **Lifecycle.** Digest pinning, `--wait`, `DrainExporters`.
4. **Validation.** Integration tests in CI, HiL validation, documentation,
   graduation to stable.

## Implementation History

- 2026-10-23: Issue [#1242](https://github.com/jumpstarter-dev/jumpstarter/issues/1242) opened.
- 2026-10-27: JEP drafted.

## References

- [jumpstarter#1242: JEP: productization and flightctl integration for exporter management](https://github.com/jumpstarter-dev/jumpstarter/issues/1242)
- [jumpstarter-lab-config](https://github.com/jumpstarter-dev/jumpstarter-lab-config), analyzed at [`42b3d18`](https://github.com/jumpstarter-dev/jumpstarter-lab-config/tree/42b3d1812a19b5bbddf73e9cd3eac900cebf9d2b)
- jumpstarter-lab-config [#22](https://github.com/jumpstarter-dev/jumpstarter-lab-config/issues/22), [#34](https://github.com/jumpstarter-dev/jumpstarter-lab-config/issues/34), [PR #37](https://github.com/jumpstarter-dev/jumpstarter-lab-config/pull/37), [PR #39](https://github.com/jumpstarter-dev/jumpstarter-lab-config/pull/39)
- [Flight Control](https://github.com/flightctl/flightctl), analyzed at [`3c8fd02`](https://github.com/flightctl/flightctl/tree/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f)
- Flight Control documentation: [Managing Devices](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/user/using/managing-devices.md), [Managing Fleets](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/user/using/managing-fleets.md), [Device API Statuses](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/user/references/device-api-statuses.md), [Encryption at rest](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/user/installing/configuring-encryption.md), [Auto-syncing dependencies](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/user/using/auto-syncing-dependencies.md), [Authentication overview](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/user/installing/configuring-auth/overview.md), [API versioning](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/developer/architecture/api-versioning.md), [Building OS Images](https://github.com/flightctl/flightctl/blob/3c8fd02c24cd6ede1f2e0e47ac082085d94ec56f/docs/user/building/building-images.md)
- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md), Phase 1 (`Exporter.spec.enabled`)
- [bootc](https://github.com/bootc-dev/bootc), [greenboot](https://github.com/fedora-iot/greenboot), [podman-systemd.unit(5)](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [kured](https://github.com/kubereboot/kured), [Zincati](https://github.com/coreos/zincati) and its [FleetLock protocol](https://github.com/coreos/zincati/blob/main/docs/development/fleetlock/protocol.md)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
