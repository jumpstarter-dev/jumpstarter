# JEP-0012: Custom User Tags on Leases

| Field              | Value                                        |
|--------------------|----------------------------------------------|
| **JEP**            | 0012                                         |
| **Title**          | Custom User Tags on Leases                   |
| **Author(s)**      | @bzlotnik (Benny Zlotnik)                   |
| **Status**         | Draft                                        |
| **Type**           | Standards Track                              |
| **Created**        | 2026-04-19                                   |
| **Updated**        | 2026-04-19                                   |
| **Discussion**     | Pending                                      |

---

## Abstract

Add support for user-defined key-value tags on Lease resources, set at
creation time and queryable when listing leases. Tags enable users to
tag leases with metadata like CI job IDs numbers for tracking and filtering.

## Motivation

Jumpstarter leases currently carry no user-defined metadata. In
production environments with multiple teams, CI pipelines, and shared
hardware pools, operators and developers need a way to annotate leases
with contextual information and later query by that information. Without
tags, correlating a lease to its originating CI job, or purpose
requires external bookkeeping.

### User Stories

- **As a** CI pipeline author, **I want to** tag leases with my CI job
  ID and pipeline name, **so that** I can correlate leases with builds.
- **As a** lab operator, **I want to** query leases by environment or
  purpose, **so that** I can audit and manage lease usage.
- **As a** developer, **I want to** annotate leases with purpose tags,
  **so that** I can distinguish between test runs and debug sessions.

### Constraints

- Tags must be immutable after creation (like selector) to avoid race
  conditions
- Must not conflict with system labels (`jumpstarter.dev/lease-ended`)
  or exporter selector matchLabels stored in `ObjectMeta.Labels`
- Must be size-limited to prevent abuse
- Must follow Kubernetes label conventions

## Proposal

The feature adds a `map<string, string> tags` field to the Lease
protobuf message and `Tags map[string]string` to the K8s LeaseSpec
CRD.

### Storage

Tags are stored in `LeaseSpec.Tags` as the source of truth. They
are also applied to K8s `ObjectMeta.Labels` with the prefix
`metadata.jumpstarter.dev/` for native K8s queryability. The prefix
avoids conflicts with existing `ObjectMeta.Labels` usage (exporter
selector matchLabels and system labels).

### Validation

- Key: max 63 characters, valid K8s label key format, must not use
  `jumpstarter.dev/` or `metadata.jumpstarter.dev/` prefixes
- Value: max 63 characters, valid K8s label value format
- Validated server-side in `CreateLease`

### Queryability

A new `tag_filter` field in `ListLeasesRequest` allows filtering
leases by tags. The server auto-prefixes keys with
`metadata.jumpstarter.dev/` before querying K8s, so users use
unprefixed keys in queries:

```shell
--tag-filter team=devops
```

not:

```shell
--tag-filter metadata.jumpstarter.dev/team=devops
```

Leases are also queryable via kubectl directly:

```shell
kubectl get leases -l metadata.jumpstarter.dev/team=devops
```

### CLI

```shell
jmp create lease -l board=rpi4 --duration 1h --tag team=devops --tag ci-job=12345
jmp get leases --tag-filter team=devops
```

Tags are displayed in `jmp get leases` output.

### Protobuf Changes

```protobuf
// In Lease message:
map<string, string> tags = 13 [(google.api.field_behavior) = IMMUTABLE];

// In ListLeasesRequest:
string tag_filter = 6 [(google.api.field_behavior) = OPTIONAL];
```

### CRD Changes

```go
// In LeaseSpec:
// +kubebuilder:validation:MaxProperties=10
Tags map[string]string `json:"tags,omitempty"`
```

## Test Plan

### Unit Tests

- Tag validation: max entries, key/value length, reserved prefixes
- `LeaseFromProtobuf` / `ToProtobuf` roundtrip with tags

### Integration Tests

- `CreateLease` with tags
- `ListLeases` with `tag_filter`

### E2E Tests

- Create lease with tags, list with filter, verify results

## Backward Compatibility

This change is fully backward compatible. The new `tags` field is
optional and defaults to an empty map. Existing leases will have empty
tags. No existing protobuf field numbers are changed.

## Consequences

### Positive

- Users can track and filter leases by custom metadata
- K8s-native queryability via label selectors
- Consistent with Exporter's existing labels field
- Enables CI/CD integration and team-based lease management

## Implementation History

- 2026-04-19: Initial proposal (Draft)

## References

- [Kubernetes Labels and Selectors](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/)
- [AIP-203: Field behavior documentation](https://google.aip.dev/203)
- Exporter labels pattern in `protocol/proto/jumpstarter/client/v1/client.proto`

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
