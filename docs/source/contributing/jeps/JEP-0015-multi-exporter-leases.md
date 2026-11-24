# JEP-0015: Multi-Exporter Leases

| Field             | Value                                                    |
| ----------------- | -------------------------------------------------------- |
| **JEP**           | 0015                                                     |
| **Title**         | Multi-Exporter Leases                                    |
| **Author(s)**     | @kirkbrauer (Kirk Brauer, kbrauer@hatci.com)             |
| **Status**        | Discussion                                               |
| **Type**          | Standards Track                                          |
| **Created**       | 2026-09-01                                               |
| **Updated**       | 2026-11-24                                               |
| **Discussion**    | [PR #1069](https://github.com/jumpstarter-dev/jumpstarter/pull/1069), [issue #47](https://github.com/jumpstarter-dev/jumpstarter/issues/47) |
| **Requires**      | JEP-0014                                                 |
| **Supersedes**    |                                                          |
| **Superseded-By** |                                                          |

---

## Abstract

This JEP lets one `Lease` hold more than one exporter. A lease can contain
a list of members, and each member has a role name and selects one
exporter. The controller binds all the required members or none, and the
API server makes sure that only one lease holds an exporter. A lab
administrator can publish the members as a `LeaseTemplate` that a client
requests by its name. JEP-0016 adds port forwards between the members of a
lease.

## Motivation

Today, a lease gives a client exclusive access to one exporter. Some tests
use two or more exporters:

- A phone and a head unit, for a phone projection test.
- Two ECUs, for a CAN gateway test.
- A BLE peripheral and its central.
- A DUT and a serial companion.

Such a test must get one lease for each exporter. This causes four
problems:

- **Acquisition:** No operation gets all the exporters or none of them. A
  test can hold one device while it waits for a second device. Two tests
  can each hold a device that the other test must have.
- **Lifetime:** Each lease expires independently, and the client releases
  each lease independently.
- **Policy and telemetry:** The leases have no shared identity.
- **Connectivity:** No managed data path exists between two exporters.
  JEP-0016 solves this problem.

The exporter stays the unit of allocation. An exporter owns a DUT and its
harness, and the harness can contain many connected devices. If two clients
lease parts of one harness, they can both control the same assembly. Thus
this JEP puts independent exporters together in one lease. A pool of N
phones and M head units can then supply N × M pairs without fixed wiring
for each pair.

The lab knows the exporters that belong together. The test does not. The
labels, the bus names, and the wiring are different in each lab, and they
change.

If each test contains selectors and labels, one lab change breaks all those
tests. The same test also cannot run in a second lab that uses different
labels. A test must give only the name of the setup that it uses. The lab
must define that setup.

### User Stories

- **As a** test engineer, **I want to** get a phone and a head unit in one
  lease. My test then does not hold one device while it waits for the
  other.
- **As a** test engineer, **I want to** keep the other devices when one
  device fails. I can then collect logs before I release the lease.
- **As a** lab administrator, **I want to** attach two exporters to one
  head unit. A test can then lease one of them, and no other test can
  control that head unit.
- **As a** lab administrator, **I want to** publish a named definition of a
  head unit, a phone, and an ECU on one CAN bus. Users then lease a correct
  set and do not use our labels.
- **As a** test engineer, **I want to** refer to that definition by its
  name. The same test then runs in each lab that publishes the definition.

## Proposal

### Terms

| Term | Meaning |
| --- | --- |
| Scalar lease | A lease that uses the top-level `selector` or `exporterRef`. It binds one exporter, as it does today. |
| Member | An entry in `Lease.spec.members[]`. It has a name and selects one exporter. |
| Required member | A member that is not optional. |
| Role | The name of a member, for example `phone`. |
| Member-form lease | A lease that contains `spec.members[]`. |
| Bind | To give an exporter to a member or to a scalar lease. |
| Hold | A lease holds an exporter or an exclusion group when it owns the claim for it. |
| Constraint | A rule for a set of members. Their exporters must have the same value of one label. |
| Exclusion group | A set of exporters that serve one DUT. Only one lease can hold the group. |
| Claim | An `ExporterClaim` object. It shows that one lease holds one exporter or one exclusion group. |
| Lease template | A `LeaseTemplate` object. An administrator publishes it. It gives a name to a set of members and constraints. |

### Scope

This JEP adds five concepts. Two of them are new resources.

| Concept | Location | Function |
| --- | --- | --- |
| Members | `Lease.spec.members[]` | Each member has a role and the current `selector` or `exporterRef` fields. |
| Constraints | `Lease.spec.constraints[]` | The listed members must bind exporters that have the same value of one label (DD-5). |
| Exclusion groups | `Exporter.spec.exclusionGroup` | An administrator sets the same value on the exporters that serve one DUT (DD-7). |
| Claims | New `ExporterClaim` resource | One object for each held exporter or exclusion group. Only the controller writes claims (DD-3). |
| Lease templates | New `LeaseTemplate` resource | A namespaced definition of members, constraints, defaults, and documentation (DD-10). |

This JEP includes:

- Lease members: a maximum of eight for each lease. The controller binds
  all the required members or none.
- An access policy check for each member (DD-9).
- Constraints that the controller solves at bind time (DD-5).
- Members that use the exporters of an `ExporterSet` from JEP-0014. Thus
  one lease can contain physical devices and virtual devices (DD-15).
- Exclusive claims for scalar leases and member-form leases (DD-3).
- Lease templates, with their discovery and expansion (DD-10 to DD-12).
- CLI support for templates and for ad-hoc members.
- Python support for templates, with typed classes (DD-13).
- Shell commands and metadata for each role (DD-14).
- The export of a lease as a Mobly testbed file. Mobly is the Google test
  framework for tests with many devices.

This JEP does not include:

| Item | Where it is, or what this JEP does |
| --- | --- |
| Port forwards between members | JEP-0016 |
| Constraints other than a shared label value | *Future Possibilities* |
| A lease for one DUT of many on one exporter | A member binds a full exporter (DD-6) |
| New members or constraints in test code | The client libraries request a template by name. A request can only add labels to a role (DD-11, DD-12) |
| New roles or constraints in a request for a template | A request can only make a template narrower (DD-12) |
| Driver types for a member | The ExporterClass proposal. Until then, the Python class does this check (DD-13) |
| A limit on resources across the members of a lease | *Future Possibilities* |

### Lease templates

A lab administrator writes a `LeaseTemplate`. The template tells the
controller the exporters that belong together.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: LeaseTemplate
metadata:
  name: ivi
  namespace: lab-madrid
spec:
  description: "AAOS head unit, phone, and body ECU on one CAN bus"
  docs:
    - https://wiki.example.com/labs/ivi
  image: https://wiki.example.com/labs/ivi.png
  instructions: |
    Set the head unit to on before you start the phone.
  members:
    - name: headunit
      selector: { matchLabels: { device-type: aaos-headunit } }
    - name: phone
      selector: { matchLabels: { device-type: phone } }
      optional: true
    - name: ecu
      selector: { matchLabels: { device-type: ecu } }
  constraints:
    - { members: [headunit, ecu],   sameLabel: bus }
    - { members: [headunit, phone], sameLabel: rf-domain }
  defaults:
    duration: 2h
    sharedWith: [lab-support]
```

The fields have these functions:

- **`members`** uses the same schema as `Lease.spec.members[]`. Each member
  has a role and one of `selector` or `exporterRef`.
- **`constraints`** uses the same schema as `Lease.spec.constraints[]`.
  Here, the head unit and the ECU must be on the same bus. The controller
  selects the value at bind time. Any bus is satisfactory if its head unit
  and its ECU are free (DD-5).
- **`optional: true`** lets the lease become ready without that member. On
  the client, the role is then `None`.
- **`description`, `docs`, `image`, and `instructions`** are for persons.
  `jmp describe lease-template` shows them. The controller does not read
  the `docs` and `image` links.
- **`defaults`** contains `duration` and `sharedWith`. The controller uses
  each value when a request does not give it. No other request field has a
  template default.
- **`defaults.sharedWith`** shares the lease with the listed clients, as
  `spec.sharedWith` does for a scalar lease. The controller removes the
  client that makes the request from this list.
- A template cannot define hooks. Hooks stay in the exporter configuration.

Administrators apply templates with `kubectl apply` or with
`jmp admin apply -f`. Clients can list, read, and request all the templates
in their namespace. Clients cannot create or change a template.

A member can also select the exporters of an `ExporterSet`. The selector
must agree with the labels that the set gives to its exporters. Here, the
ECU is a physical device and the head unit is a virtual device:

```yaml
  members:
    - name: ecu
      selector: { matchLabels: { device-type: ecu } }
    - name: headunit
      selector: { matchLabels: { device: aaos-cuttlefish, virtual: "true" } }
```

If the set has exporters but none is free, it creates one. The lease holds
nothing while it waits (DD-15).

### How to request a lease

Clients find and request templates with the current commands:

```console
$ jmp get lease-templates
NAME         MEMBERS                  AVAILABLE   DESCRIPTION
ivi          headunit, phone*, ecu    2/3         AAOS head unit, phone, and body ECU…
projection   phone, headunit          2/2         Virtual phone and head unit

$ jmp describe lease-template ivi
$ jmp shell -t ivi
$ jmp create lease -t ivi --duration 4h --without phone --member headunit=soc=sa8775p
ivi-4fj2k
```

- The examples use short lease names. The controller gives each lease the
  `lease_id` of the request or a generated ID, as it does today.
- A `*` shows an optional member.
- `AVAILABLE` is the number of members that have a minimum of one exporter
  that the client can lease now. It is only a guide. It does not do the
  full assignment or apply the constraints.
- `--duration` and `--share` replace the template defaults. `--share`
  replaces the full list. Without `--share`, the lease gets the list of the
  template. The owner can then remove a client with
  `jmp update lease --share-remove`.
- `--begin-time`, `--tag`, and `--context` operate as they do for a scalar
  lease. They have no template default.
- `--member ROLE=SELECTOR` adds labels to the selector of a role.
- `--without ROLE` excludes an optional role. The controller does not bind
  an excluded role.
- `-l`/`--selector` and `-n`/`--name` do not refer to a role. The CLI
  rejects them when you use `-t` (DD-12).

The CLI can also make a member-form lease without a template. Use this for
exploration and for fault isolation:

```console
$ jmp create lease \
    --member phone=device-type=phone \
    --member headunit=device-type=headunit \
    --same-label rf-domain=phone,headunit \
    --duration 45m
lease-9m2qd

$ jmp get leases lease-9m2qd
NAME          SELECTOR   EXPIRES AT            REMAINING   CLIENT      EXPORTER
lease-9m2qd              2026-09-01 10:45:00   44m         ci-runner   phone=rack3-phone-4,
                                                                       headunit=rack3-hu-2
```

Only the CLI has this ad-hoc path. The client libraries request a template
or attach to a lease that exists (DD-11).

### How to use a lease in a shell

In a shell, each role is a top-level name of the `j` command:

```console
$ jmp shell --lease lease-9m2qd
jumpstarter ⚡ lease-9m2qd ➤ j phone adb shell getprop ro.product.model
phone-under-test
jumpstarter ⚡ lease-9m2qd ➤ j headunit power on
```

The shell sets two environment variables for its child processes:

- `JMP_LEASE` contains the lease name, as it does today.
- `JMP_LEASE_MEMBERS` contains a JSON object. For each role, it gives the
  exporter name, the exporter labels, and the local socket. An optional
  role that has no exporter has the value `null`.

```json
{
  "phone": {
    "exporter": "rack3-phone-4",
    "labels": { "device-type": "phone" },
    "socket": "/tmp/jumpstarter-x1/phone.sock"
  },
  "headunit": {
    "exporter": "rack3-hu-2",
    "labels": { "device-type": "headunit" },
    "socket": "/tmp/jumpstarter-x1/headunit.sock"
  }
}
```

The shell removes `JUMPSTARTER_HOST`, `JMP_EXPORTER`, and
`JMP_EXPORTER_LABELS` from the environment of its child processes, because
these describe one exporter. Thus `env()` and `env_with_metadata()` fail in
this shell. Their error message tells the user to call `env_members()`
(DD-14). `env_members()` is the function for a member-form shell. It gives
the members and their `metadata`.

A script for one exporter can also attach to the lease through `JMP_LEASE`.
It then gets the `Dial` error with the list of roles (*API / Protocol
Changes*).

### How to use a lease in Python

In Python, a member-form lease is a class that refers to a template. Each
attribute is a role. The annotation of the attribute lists the driver
clients that the test expects on that exporter. The class contains no
selectors, because the template contains them.

```python
from datetime import timedelta

from jumpstarter.client.lease import LeaseMembers
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter_driver_adb.client import AdbClient
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_power.client import PowerClient


class Phone(CompositeClient):
    adb: AdbClient


class Headunit(CompositeClient):
    power: PowerClient


class Projection(LeaseMembers, template="projection"):
    phone: Phone
    headunit: Headunit


config = ClientConfigV1Alpha1.load("default")

with config.lease(Projection, duration=timedelta(minutes=45)) as lease:
    with lease.connect() as members:           # members: Projection
        print(members.metadata["phone"].name)  # the exporter of the role
        members.headunit.power.on()            # PowerClient.on
        print(members.phone.adb.devices())     # AdbClient.devices
```

A type checker reads `members.phone` as `Phone`. It finds a role that the
class does not declare, a driver that the member class does not list, and
an incorrect call. These rules apply:

- **Template:** `template=` gives the name of the template that the class
  requests. A class without `template=` can only attach to a lease that
  exists.
- **Roles:** An attribute with a `CompositeClient` subclass as its
  annotation is a role. The role name is the attribute name with `-` in the
  place of `_`. `member(role=...)` sets a different name.
- **Reserved names:** `by_role` and `metadata` are attributes of
  `LeaseMembers`. A role with the name `by-role` or `metadata` must use
  `member(role=...)` on a different attribute.
- **Optional roles:** If a role is optional in the template, its annotation
  must include `| None`. Only optional roles can use `| None`.
- **Drivers:** The attributes of the member class are driver names with
  client classes. `| None` shows a driver that can be absent.
- **Check before the request:** The client reads the template first. It
  raises `TypeError` and creates no lease if one of these conditions is
  true:
  - The class declares a role that the template does not contain.
  - The class does not declare a required role.
  - The class and the template do not agree on an `optional` flag.
- **Check at `connect()`:** The client compares each bound exporter with
  its member class. It uses the client class that the exporter reports for
  each driver. On a mismatch, it raises `TypeError` with the role and the
  driver.
- **Role that the class does not declare:** If that role is optional, the
  client excludes it, as `--without` does.
- **`by_role`:** A read-only mapping from each role to its client, for
  generic tools.
- **`metadata`:** A read-only mapping from each role to its
  `ExporterMetadata`. The value is `None` for a role without an exporter.

`config.lease` accepts three forms for its first argument:

| Call | Result of `connect()` |
| --- | --- |
| `config.lease(Projection)` | An instance of `Projection` |
| `config.lease(template="ivi")` | A `LeaseMembers` object. Use `by_role` to get the clients. |
| `config.lease(selector=...)` or a selector string | One driver client, as today |

The request form sets the type of the result (DD-2). The client does not
read a string as a template name. `member_selectors={"headunit":
"soc=sa8775p"}` makes a role narrower, as `--member` does.
`config.lease(M, lease_name=...)` attaches to a member-form lease that
exists. `connect()` then compares `M` with the members of that lease.

`JumpstarterTest` accepts a `members` class as an alternative to `selector`.
Its `client` fixture then gives an instance of that class. In `jmp shell`,
the fixture attaches to the lease of the shell. Out of a shell, it requests
the template of the class.

```python
class TestProjection(JumpstarterTest):
    members = Projection

    def test_power(self, client: Projection) -> None:
        client.headunit.power.on()
```

### What the controller stores

The controller expands a template when a client requests it. The result is
a member-form lease. The `ivi` request above becomes this lease:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  name: ivi-4fj2k
  namespace: lab-madrid
spec:
  clientRef: { name: ci-runner }
  templateRef:                 # a record only; the controller does not read it at bind time
    name: ivi
    generation: 3
  duration: 4h                 # the request replaces the default of 2h
  sharedWith: [lab-support]    # the template default
  members:
    - name: headunit
      selector: { matchLabels: { device-type: aaos-headunit, soc: sa8775p } }
    - name: phone
      selector: { matchLabels: { device-type: phone } }
      optional: true
      excluded: true           # --without phone
    - name: ecu
      selector: { matchLabels: { device-type: ecu } }
  constraints:                 # the same as in the template
    - { members: [headunit, ecu],   sameLabel: bus }
    - { members: [headunit, phone], sameLabel: rf-domain }
status:
  members:
    - { name: headunit, exporterRef: { name: hu-03 },  state: Bound }
    - { name: phone,    state: Omitted, reason: Excluded }
    - { name: ecu,      exporterRef: { name: ecu-11 }, state: Bound }
```

- The `rf-domain` constraint has no effect, because only one of its members
  has an exporter (DD-5).
- A change to the template has no effect on this lease (DD-10).
- A template with one member also expands into a member-form lease. Thus
  the role name and the result type stay the same (DD-2).
- The client must pass the `ExporterAccessPolicy` for each member. A
  template gives no access (DD-9).
- Only template expansion sets `excluded`. A template cannot contain it,
  and a request with ad-hoc members cannot contain it.

### Exclusion groups

Some labs attach two exporters to one DUT. For example, one exporter
controls power and the serial console, and a second exporter controls the
cameras and CAN. An administrator sets the same `spec.exclusionGroup` value
on these exporters.

Only one lease can hold an exclusion group. That lease can bind one or more
exporters of the group. No other lease can bind the other exporters of the
group until that lease ends (DD-7).

### Mobly testbed export

A lease that has members with ADB can become a Mobly testbed file. Do the
export in `jmp shell` and then run the Mobly suite in the same shell:

```console
$ jmp shell --lease lease-9m2qd
jumpstarter ⚡ lease-9m2qd ➤ jmp get leases lease-9m2qd -o mobly > testbed.yml
jumpstarter ⚡ lease-9m2qd ➤ mobly_test.py -c testbed.yml --test_bed lease-9m2qd
```

The export writes one Android device for each member that has ADB. It uses
the role names as device labels.

### API / Protocol Changes

All the changes add fields, resources, or RPCs. No current field changes.

**`LeaseSpec`** gets two optional lists and a template reference:

```go
// Member names are list-map keys, thus the API server rejects duplicates.
// All the lists have a maximum size. This keeps the rules in the CEL cost limit.
// +kubebuilder:validation:XValidation:rule="!has(self.constraints) || (has(self.members) && self.constraints.all(c, c.members.all(n, self.members.exists(m, m.name == n))))",message="constraints must reference declared members"
type LeaseSpec struct {
    // ... all current fields do not change ...

    // +listType=map
    // +listMapKey=name
    // +kubebuilder:validation:MaxItems=8
    Members []LeaseMember `json:"members,omitempty"`

    // +kubebuilder:validation:MaxItems=4
    Constraints []LeaseConstraint `json:"constraints,omitempty"`

    // ClientService sets this field when it expands a template.
    TemplateRef *LeaseTemplateReference `json:"templateRef,omitempty"`
}

// +kubebuilder:validation:XValidation:rule="!has(self.excluded) || !self.excluded || (has(self.optional) && self.optional)",message="only an optional member can be excluded"
// +kubebuilder:validation:XValidation:rule="(((has(self.selector) && ((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0))) ? 1 : 0) + ((has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0) ? 1 : 0)) == 1",message="exactly one of selector or exporterRef.name is required"
type LeaseMember struct {
    // +kubebuilder:validation:MaxLength=63
    // +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
    Name          string                       `json:"name"`
    Selector      metav1.LabelSelector         `json:"selector,omitempty"`
    ExporterRef   *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
    Optional      bool                         `json:"optional,omitempty"`
    // Only template expansion sets this field, for --without (DD-12).
    Excluded      bool                         `json:"excluded,omitempty"`
    AllowDisabled bool                         `json:"allowDisabled,omitempty"`
}

type LeaseConstraint struct {
    // +kubebuilder:validation:MinItems=2
    // +kubebuilder:validation:MaxItems=8
    Members []string `json:"members"`
    // A label key, for example "bus" or "rf-domain". It is the only rule
    // today. A later rule becomes a second optional field (DD-5).
    SameLabel string `json:"sameLabel"`
}

type LeaseTemplateReference struct {
    Name       string `json:"name"`
    Generation int64  `json:"generation"` // metadata.generation of the template
}
```

The current CEL rules stay, with these additions:

- A member-form lease must have a minimum of one required member. A lease
  with only optional members can bind no exporter.
- The top-level rule "one of selector or exporterRef is required" gets a
  third alternative, `members`. The scalar form and `members` cannot be in
  the same lease.
- `members`, `constraints`, and `templateRef` cannot change after
  creation.
- Each rule uses `has()` before it reads an optional field.

**`LeaseStatus`** keeps `exporterRef` and gets two lists and one counter:

```go
type LeaseStatus struct {
    // ... all current fields do not change ...
    // ExporterRef stays nil for all member-form leases (DD-2).

    Members []LeaseMemberStatus `json:"members,omitempty"`
    // The exclusion groups that this lease holds (DD-7).
    ExclusionGroups []string `json:"exclusionGroups,omitempty"`
    // A counter of bind attempts. Each claim records it (DD-3).
    BindAttempt int64 `json:"bindAttempt,omitempty"`
}

type LeaseMemberStatus struct {
    Name        string                       `json:"name"`
    State       string                       `json:"state"` // Pending | Bound | Omitted | Lost
    Reason      string                       `json:"reason,omitempty"`
    Message     string                       `json:"message,omitempty"`
    ExporterRef *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
    Priority    int                          `json:"priority,omitempty"`
    SpotAccess  bool                         `json:"spotAccess,omitempty"`
}
```

| Member state | Meaning |
| --- | --- |
| `Pending` | The member has no exporter at this time. |
| `Bound` | The member has an exporter. |
| `Omitted` | An optional member has no exporter. The reason is `Excluded` or one of the current reasons, for example `NoAccess`. |
| `Lost` | A bound member lost its exporter. The exporter went offline, or an administrator deleted it, or it released the lease (DD-8). |

**`ExporterSpec`** gets one optional field that an administrator sets:

```go
    // Exporters with the same value serve the same DUT (DD-7).
    // +kubebuilder:validation:MaxLength=63
    ExclusionGroup string `json:"exclusionGroup,omitempty"`
```

**`ExporterStatus`** gets one field that only the lease controller writes:

```go
    // The lease that gets or holds this exporter (DD-7). The controller
    // sets it before it creates the claims and removes it when the attempt
    // fails or the lease ends.
    ClaimRef *ClaimLeaseReference `json:"claimRef,omitempty"`
```

**`ExporterClaim`** is a new namespaced resource. Only the controller
writes it (DD-3).

```go
// The object name is "exporter-" or "group-" and then the first 32 hex
// digits of the SHA-256 of the exporter name or the group name.
// The object has a controller owner reference to its lease.
// +kubebuilder:validation:XValidation:rule="self == oldSelf",message="spec is immutable"
// +kubebuilder:validation:XValidation:rule="has(self.exporter) != has(self.exclusionGroup)",message="exactly one of exporter or exclusionGroup is required"
type ExporterClaimSpec struct {
    Exporter       string              `json:"exporter,omitempty"`
    ExclusionGroup string              `json:"exclusionGroup,omitempty"`
    LeaseRef       ClaimLeaseReference `json:"leaseRef"`
}

type ClaimLeaseReference struct {
    Name string    `json:"name"`
    UID  types.UID `json:"uid"`
    // status.bindAttempt of the lease when it created the claim (DD-3).
    Attempt int64 `json:"attempt"`
}
```

**`LeaseTemplate`** is a new namespaced resource. It has no status.

```go
// A template member cannot set `excluded`. Only a request can exclude a role.
// +kubebuilder:validation:XValidation:rule="self.members.all(m, !has(m.excluded) || !m.excluded)",message="a template member cannot set excluded"
// +kubebuilder:validation:XValidation:rule="!has(self.constraints) || self.constraints.all(c, c.members.all(n, self.members.exists(m, m.name == n)))",message="constraints must reference declared members"
type LeaseTemplateSpec struct {
    Description  string   `json:"description,omitempty"`
    Docs         []string `json:"docs,omitempty"`         // URLs
    Image        string   `json:"image,omitempty"`        // URL
    Instructions string   `json:"instructions,omitempty"` // plain text

    // +listType=map
    // +listMapKey=name
    // +kubebuilder:validation:MinItems=1
    // +kubebuilder:validation:MaxItems=8
    Members     []LeaseMember     `json:"members"`
    // +kubebuilder:validation:MaxItems=4
    Constraints []LeaseConstraint `json:"constraints,omitempty"`

    Defaults LeaseTemplateDefaults `json:"defaults,omitempty"`
}

// Duration and SharedWith are the only defaults.
type LeaseTemplateDefaults struct {
    Duration *metav1.Duration `json:"duration,omitempty"`
    // The same rules as LeaseSpec.SharedWith.
    // +listType=set
    // +kubebuilder:validation:MaxItems=10
    SharedWith []string `json:"sharedWith,omitempty"`
}
```

A template uses the lease types and the same CEL rules. Thus a valid
template expands into a valid lease. A template has no field for hooks.

**Client protocol** (`jumpstarter.client.v1`):

```protobuf
enum LeaseMemberState {
  LEASE_MEMBER_STATE_UNSPECIFIED = 0;
  LEASE_MEMBER_STATE_PENDING = 1;
  LEASE_MEMBER_STATE_BOUND = 2;
  LEASE_MEMBER_STATE_OMITTED = 3;
  LEASE_MEMBER_STATE_LOST = 4;
}

message LeaseMember {
  string name = 1;
  oneof selection {
    string selector = 2;      // A label selector, as in Lease.selector.
    string exporter_name = 3;
  }
  bool optional = 4;
  bool allow_disabled = 5;
  // Template expansion sets this field. CreateLease rejects a request that sets it.
  bool excluded = 6 [(google.api.field_behavior) = OUTPUT_ONLY];
}

message LeaseConstraint {
  repeated string member_names = 1;
  oneof rule {
    string same_label = 2;    // The only rule in this JEP (DD-5).
  }
}

// How a request uses a template (DD-12).
message LeaseTemplateUse {
  string template = 1 [(google.api.resource_reference) = {type: "jumpstarter.dev/LeaseTemplate"}];
  // Optional roles only.
  repeated string excluded_members = 2 [(google.api.field_behavior) = INPUT_ONLY];
  // Role to selector. The controller adds it to the template selector with AND.
  map<string, string> member_selectors = 3 [(google.api.field_behavior) = INPUT_ONLY];
  int64 generation = 4 [(google.api.field_behavior) = OUTPUT_ONLY];
}

message LeaseMemberStatus {
  string name = 1;
  LeaseMemberState state = 2;
  optional string exporter = 3 [(google.api.resource_reference) = {type: "jumpstarter.dev/Exporter"}];
  optional string reason = 4;
  optional string message = 5;
  int32 priority = 6;
  bool spot_access = 7;
}

message Lease {
  // Fields 1 to 18 do not change. `selector` is necessary only for a scalar
  // lease. The controller sets `exporter` only for a scalar lease.
  repeated LeaseMember members = 19 [(google.api.field_behavior) = IMMUTABLE];
  repeated LeaseConstraint constraints = 20 [(google.api.field_behavior) = IMMUTABLE];
  optional LeaseTemplateUse template = 21 [(google.api.field_behavior) = IMMUTABLE];
  repeated LeaseMemberStatus member_statuses = 22 [(google.api.field_behavior) = OUTPUT_ONLY];
}

message LeaseTemplateMember {
  LeaseMember member = 1;
  // The number of exporters that the client can lease now.
  int32 available = 2 [(google.api.field_behavior) = OUTPUT_ONLY];
}

message LeaseTemplate {
  option (google.api.resource) = {
    type: "jumpstarter.dev/LeaseTemplate"
    pattern: "namespaces/{namespace}/leasetemplates/{leasetemplate}"
    singular: "leaseTemplate"
    plural: "leaseTemplates"
  };
  string name = 1 [(google.api.field_behavior) = IDENTIFIER];
  string description = 2;
  repeated string docs = 3;
  optional string image = 4;
  optional string instructions = 5;
  repeated LeaseTemplateMember members = 6;
  repeated LeaseConstraint constraints = 7;
  optional google.protobuf.Duration default_duration = 8;
  int64 generation = 9;
  repeated string default_shared_with = 10;
}

message GetLeaseTemplateRequest {
  string name = 1;
}

message ListLeaseTemplatesRequest {
  string parent = 1;
  int32 page_size = 2;
  string page_token = 3;
}

message ListLeaseTemplatesResponse {
  repeated LeaseTemplate lease_templates = 1;
  string next_page_token = 2;
}

service ClientService {
  // ... the current RPCs do not change ...
  rpc GetLeaseTemplate(GetLeaseTemplateRequest) returns (LeaseTemplate);
  rpc ListLeaseTemplates(ListLeaseTemplatesRequest) returns (ListLeaseTemplatesResponse);
}
```

`CreateLease` accepts only one of three request forms. For all other
requests, it returns `INVALID_ARGUMENT` with the names of the fields and
creates nothing.

| Form | Fields that the request sets | Fields that the request must not set |
| --- | --- | --- |
| Scalar | `selector`, `exporter_name`, or the two together, as today | `members`, `constraints`, `template` |
| Template | `template`, with optional `excluded_members` and `member_selectors` | `selector`, `exporter_name`, `members`, `constraints` |
| Members | `members`, with optional `constraints` | `selector`, `exporter_name`, `template`, and `excluded` on a member |

There is no RPC to create, change, or delete a template.

**Controller protocol** (`jumpstarter.v1`): `DialRequest` gets one field.

```protobuf
message DialRequest {
  string lease_name = 1;             // no change
  optional string member_name = 2;   // NEW: necessary for a member-form lease
}
```

`Dial` without `member_name` on a member-form lease returns
`INVALID_ARGUMENT` with the list of roles. `Dial` for a member that is not
`Bound` returns `FAILED_PRECONDITION` with the member state and the reason.
The lease RPCs on
`ControllerService` (`RequestLease`, `GetLease`, `ListLeases`) stay scalar
only. For a member-form lease, `GetLeaseResponse.exporter_uuid` is empty.

**CLI:**

- `jmp create lease` and `jmp shell` accept `-t`/`--lease-template NAME`,
  with `--without ROLE` and `--member ROLE=SELECTOR`.
- `jmp create lease` and `jmp shell` accept ad-hoc `--member ROLE=SELECTOR`
  and `--same-label KEY=ROLE,ROLE`.
- `jmp get lease-templates` and `jmp describe lease-template NAME` are new.
- `jmp get leases` shows the exporter of each role and the template name.
- `jmp get leases NAME -o mobly` writes a Mobly testbed file.
- `j ROLE DRIVER ...` operates in a member-form shell.
- `jmp admin apply -f FILE` accepts `LeaseTemplate` manifests.
- `jmp delete lease` does not change. `jmp update lease` operates on a
  member-form lease as *Readers of `status.exporterRef`* describes.

### Hardware Considerations

- **Range:** Physical radio devices must be in range of each other. Put a
  label such as `rf-domain: rack-3` on the exporters and use a `sameLabel`
  constraint. The controller does not measure radio signals.
- **Shared buses:** Use a label such as `bus` and a `sameLabel` constraint
  for devices on one CAN bus or one harness.
- **One DUT with many exporters:** Set an exclusion group on these
  exporters (DD-7).
- **Member loss:** The lease keeps the other members. The client can then
  collect logs (DD-8).

## Design Decisions

This table is a summary of the decisions. Each subsection gives the
alternatives and the rationale.

| Decision | Result |
| --- | --- |
| DD-1 | `Lease` gets members. There is no new group resource. |
| DD-2 | `status.exporterRef` stays for scalar leases only. |
| DD-3 | The API server enforces exclusive claims. |
| DD-4 | The controller assigns members with augmenting paths. |
| DD-5 | A constraint gives its members a shared label value. |
| DD-6 | A member binds a full exporter. |
| DD-7 | One lease holds all the exporters of a DUT. |
| DD-8 | A lost member degrades the lease. The other members stay. |
| DD-9 | The access policy applies to each member. |
| DD-10 | The controller expands a `LeaseTemplate` when it creates the lease. |
| DD-11 | Only templates and the CLI make member-form leases. |
| DD-12 | A request can only make a template narrower. |
| DD-13 | Typed Python classes refer to a template. |
| DD-14 | Each role has shell commands and metadata. |
| DD-15 | A member can use the exporters of an `ExporterSet`. |
| DD-16 | `Ready` waits for the `beforeLease` hooks of the members. |

### DD-1: Extend `Lease`

**Alternatives considered:**

1. **Extend `Lease`** with `spec.members[]` and `status.members[]`.
2. **A new `LeaseGroup` resource** that owns one child `Lease` for each
   exporter.
3. **Client coordination only.** The client gets N leases and connects them
   with a tag.

**Decision:** Option 1.

**Rationale:** With option 1, the controller writes all the members in one
status update. A lease cannot store a partial result.

With option 2, each child lease binds independently. The controller must
then have time limits, a release-and-retry procedure, and a procedure for
contention. Option 3 has the same partial-result problem. Options 2 and 3
also must have a method to select a member for `Dial`.

### DD-2: Keep `status.exporterRef` for scalar leases only

**Alternatives considered:**

1. **Keep the scalar field and add a list.** `status.exporterRef` stays
   nil for each member-form lease. `status.members[]` contains the result.
2. **Replace the scalar field with a list** and change each reader.
3. **Fill the two fields.** `status.exporterRef` gets the first member.

**Decision:** Option 1.

**Rationale:** Current readers continue to operate for scalar leases. For a
member-form lease, an old reader gets nil. An N-1 client that attaches to
such a lease sends no `member_name`, and `Dial` returns `INVALID_ARGUMENT`.
The client does not connect to a device.

With option 3, an old reader connects to an arbitrary role. With option 2,
all readers must change at the same time.

`Lease.exporter` in `ClientService` and `GetLeaseResponse.exporter_uuid`
obey the same rule. The request form sets the result type:

- A scalar request gives one driver client.
- A request with `members` or a template gives a `LeaseMembers` object,
  also when it has only one member.

Thus a role name such as `headunit` stays valid when a template gets more
members.

### DD-3: Exclusive claims that the API server enforces

**Alternatives considered:**

1. **One claim object for each exporter and each exclusion group.** The
   object name comes from the exporter name or the group name. The API
   server lets only one object with that name exist.
2. **Keep the claims of the controller in memory** until its cache shows
   them.
3. **Read the active leases from the API server**, not from the cache,
   before each bind. `main` does this now
   ([#1370](https://github.com/jumpstarter-dev/jumpstarter/pull/1370)).
4. **Use the `Exporter` object as a lock**, with a write to
   `status.leaseRef`.
5. **Find conflicts and repair them** after they occur.

**Decision:** Option 1, for scalar leases and for member-form leases.

**Rationale:** Two leases must not control one DUT, not for a short time
either.

Before #1370, the controller read the active leases from its cache. Two
leases then got the same exporter in some conditions. `main` now uses
option 3. Option 3 is correct only while one controller process with one
worker binds leases. It does not stop a former leader that continues to
reconcile. This JEP makes the problem larger:

- A member-form lease binds many exporters in one reconcile.
- An exclusion group is a second key. No lease field shows the exporters
  of a group that the lease did not bind.

Only the API server can give this guarantee for more than one writer.
Object names are unique in a namespace. When two clients create an object
with the same name, one create operation fails.

- Options 2 and 3 are correct only if one controller process writes at a
  time. Leader election does not guarantee this.
- Option 4 has no object for an exclusion group. Its writes also compete
  with the exporter heartbeat.
- Option 5 lets two clients control one DUT before the repair.

The controller uses claims as follows:

- **Keys:** A lease claims each exporter that it binds. It also claims each
  exclusion group of those exporters.
- **Attempt number:** `status.bindAttempt` is a counter, and each claim
  records the number of its attempt. To start an attempt, the controller
  increments the counter in a status update. It then reads the claims of
  the lease from the API server and deletes each claim with a smaller
  number. If the status update fails because a different reconcile changed
  the lease, the controller stops.
- **When an attempt starts:** The controller starts an attempt before it
  creates claims. It also starts one when a lease that is not bound owns
  claims. Thus a lease that is not bound keeps no claims from a reconcile
  that stopped.
- **Mark the exporters:** After the assignment (DD-4), the controller
  writes `status.claimRef` on each exporter that it selected, in sorted
  name order. Each write is a conditional update of an exporter that the
  controller read from the API server. The attempt fails if that exporter
  has a `claimRef` of a different active lease. It also fails if the
  exclusion group of the exporter is not the group of the assignment. From this write until the controller
  removes the mark, admission rejects a change to the group (DD-7).
- **Get the claims, then write:** The controller then creates the claims
  in sorted name order. When all the claims exist, it writes the members
  in one status update.
- **Remove the marks:** The controller removes its `claimRef` marks when
  an attempt fails and when the lease ends. It removes only a mark with
  the UID of the lease and with its own attempt number or a smaller one.
  The controller ignores and replaces a mark if its lease ended or does not
  exist, as it does for an old claim.
- **Conflict:** If a create operation fails, the controller deletes the
  claims of its attempt. It sets `Pending` with the reason `NotAvailable`
  and puts the lease back in the queue.
- **Old reconcile:** The status update that binds the lease fails if a
  different reconcile changed the lease. The controller then deletes the
  claims of its attempt. A reconcile deletes only the claims of its own
  attempt or of a smaller attempt. The claims of a bound lease have the
  largest number. Thus an old leader cannot delete them.
- **Deletes:** Each delete of a claim has a UID precondition. The UID is
  that of the claim that the reconcile created or read. All the attempts
  of a lease use the same claim names. Thus the precondition prevents the
  delete of a newer claim with the same name.
- **Bound lease:** A reconcile of a bound lease deletes each claim of the
  lease with a number smaller than `status.bindAttempt`. An old leader can
  create such a claim after the bind.
- **No deadlock:** All leases create claims in the same order. Two leases
  that use the same exporters try the lowest shared claim first. The lease
  that fails holds nothing that the other lease must have.
- **No new assignment:** A bound lease keeps its claims until it ends. The
  controller does not release or select again the members of a bound lease.
  A `Lost` member keeps its claim (*Lost members*).
- **Release:** The reconcile that ends a lease writes `status.ended` and
  then deletes its claims. `status.ended` does not go back to false. Each
  claim also has an owner reference to its lease.
- **Old claims:** A claim can stay after its lease ends, for example after
  a controller stop. The candidate filter ignores a claim if its lease
  ended or does not exist. The create operation for that claim then fails,
  and the controller reads the owner from the API server. If the owner
  ended or does not exist, the controller deletes the claim and creates it
  again.

The exporter reconciler sets `Exporter.status.leaseRef` when two conditions
are true:

- A claim for the exporter exists.
- The status of that lease shows the exporter as bound.

Thus an exporter refers to only one lease, and only after the bind.

Claims give exclusive access. They do not give fair access. In a busy pool,
a lease with four members can wait for a long time behind leases with one
member. It holds nothing while it waits (*Future Possibilities*).

### DD-4: Assign members with augmenting paths

**Alternatives considered:**

1. **First fit.** Each member, in sequence, gets its best free candidate.
2. **First fit, smallest candidate list first.**
3. **Augmenting paths.** Each member gets its best free candidate. If none
   is free, members that have an exporter move to other candidates of
   theirs to make one free.
4. **Minimum-cost assignment** by rank or priority.

**Decision:** Option 3.

**Rationale:** First fit can fail when a valid assignment exists. Example:
member A can use exporters `x` and `y`, and member B can use only `x`. A
gets `x`, and B gets nothing.

The candidate order does not change, thus each retry gives the same result.
The lease waits and the two exporters stay idle. Option 2 decreases this
problem but does not remove it.

Option 3 has these properties:

- It binds when an assignment of the required members exists.
- It gives the same result as first fit when first fit binds all members.
- When it fails, its search shows the roles that compete for the exporters.
- It is small: a lease has a maximum of eight members.

Option 4 changes the result when there is no contention. Its result is also
difficult to explain in a status message.

### DD-5: Co-location by a shared label value

**Alternatives considered:**

1. **`sameLabel` constraints.** A constraint lists members and one label
   key. The bound exporters must have the same value. The controller
   selects the value at bind time.
2. **A fixed value in each selector**, for example `rf-domain: rack-3`.
3. **An expression language** for member labels.
4. **A topology model** of buses, racks, and RF domains.

**Decision:** Option 1. The protobuf message uses a `oneof`, and the CRD
type can get more optional fields. Thus other rules can come later.

**Rationale:** The controller knows the free buses and racks only at bind
time. Option 2 limits a request to one bench. The lease then waits while an
equivalent bench is idle.

Label equality is sufficient for a shared CAN bus, one RF domain, and one
harness. Labs keep this data as exporter labels today. Options 3 and 4 are
larger designs.

The controller applies constraints as follows:

- It joins constraints that have the same member and the same label key
  into one constraint.
- It gets the candidate values from the candidates of the first filter
  stage.
- It ignores a value if a required member of the constraint has no
  candidate with that value.
- It selects one value for each constraint in sequence, in sorted order.
  It uses a value only if the candidates permit it together with the values
  that it selected before. Thus it does not try combinations that no bench
  has.
- For each complete combination, it does the DD-4 assignment with only the
  exporters that have those values.
- The first combination that assigns all the required members is the
  result. For that combination, the assignment then binds as many optional
  members as possible.
- An exporter without the label cannot satisfy the constraint.
- The controller ignores an optional member that has no exporter. If the
  optional member binds, it must satisfy the constraint. A constraint with
  fewer than two bound members has no effect.
- If the search tries more than 1,024 combinations, the controller stops
  it. It reports `Unsatisfiable` with the reason `ConstraintLimit`. This
  limit is a proposal (*Unresolved Questions*).

### DD-6: A member binds a full exporter

**Alternatives considered:**

1. **Bind exporters.** A member binds one exporter and all the DUTs behind
   it.
2. **Bind one DUT.** Add an inventory of the DUTs behind each exporter.

**Decision:** Option 1.

**Rationale:** Option 2 must have a device identity, a model of the shared
harness, and a claim for each device. Option 1 does not. The lease format
lets a later JEP add option 2:

- The member selection is a `oneof`, thus a later JEP can add a device
  source.
- Member status can get a device reference.
- Leases refer to exporters by name, not by UUID. Names stay the same
  after a restart.

### DD-7: One lease holds all the exporters of a DUT

**Alternatives considered:**

1. **An exclusion group that an administrator sets.** Exporters with the
   same `spec.exclusionGroup` serve one DUT. Only one lease holds the
   group. That lease can bind a subset of its exporters.
2. **Always lease the full group.**
3. **A DUT inventory resource.**
4. **A group label that the exporter reports.**

**Decision:** Option 1.

**Rationale:** Today, two clients can lease two exporters of one DUT. One
client can cycle the power while the other client writes the flash memory.
Labs that divide a DUT across exporters make this more frequent.

- Option 2 adds exporters that the test did not request. One offline
  exporter in the group also blocks all leases of the DUT.
- Option 3 must have the device identity that DD-6 does not include.
- Option 4 is not safe. An exporter writes its own labels when it
  registers. An exporter that an attacker controls can thus go into the
  group of a different DUT and block it.

A lease that holds a group gets no access to the exporters that it did not
bind. The lease also records its groups in `status.exclusionGroups`. Thus a
later change to `spec.exclusionGroup` does not release a DUT.

A change to the group of an exporter that a lease holds can give one DUT to
two leases. Thus admission rejects a change to `spec.exclusionGroup` while
`status.claimRef` or `status.leaseRef` is set. This is a CEL transition
rule on `Exporter`.

The controller writes `status.claimRef` before it creates a claim (DD-3).
The API server puts that write and a group change in a sequence, because
they change the same object:

- If the group change is first, the controller reads the new group. The
  attempt fails, and the subsequent reconcile uses the new group.
- If the mark is first, admission rejects the group change.

The mark stays until the lease ends. Thus the rule also applies in the time
between the claim and `status.leaseRef`, and to the exporter of a `Lost`
member.

A group also stays in use during cleanup. The controller removes a
candidate if an exporter of its group is in cleanup, for example in an
`afterLease` hook.

### DD-8: A lost member degrades the lease

**Alternatives considered:**

1. **Degrade the lease.** Set `Ready=False` and `Degraded`, and keep the
   other members.
2. **End the lease** immediately.
3. **Continue** with no report.

**Decision:** Option 1.

**Rationale:** The client can collect logs from the other members before it
releases the lease. Option 2 removes that access. Option 3 can hide an
incomplete test.

The member state becomes `Lost` with a reason. The client raises an error
on the subsequent call to that role. This decision applies only after bind.
Before bind, the controller binds all required members or none.

A member becomes `Lost` when its exporter goes offline, when an
administrator deletes the exporter, or when the exporter releases the
lease. *Lost members* gives the causes, the reasons, and the effects.

For a scalar lease, a release by the exporter ends the lease, as it does
today. For a member-form lease, the controller does not set `spec.release`.

### DD-9: Access policy applies to each member

**Alternatives considered:**

1. **Evaluate `ExporterAccessPolicy` for each member**, as for a scalar
   lease.
2. **A new policy resource** with rules for the shape and size of a lease.

**Decision:** Option 1.

**Rationale:** A member-form lease must not give more access than N scalar
leases. A template describes a setup. It gives no access. Option 2 adds a
resource before the project has experience with these leases.

- The lease priority is the minimum of the member priorities.
- The policy of each member must permit the lease duration. As today, an
  exporter is not a candidate if its policy has a smaller
  `maximumDuration`.
- `status.members[].priority` keeps the value for each member.

### DD-10: A `LeaseTemplate` that the controller expands at creation

**Alternatives considered:**

1. **A namespaced `LeaseTemplate` resource.** The controller copies it into
   the new lease. The lease records the template name and generation.
2. **A live reference.** The lease stores only `templateRef`, and the
   controller reads the template at each reconcile.
3. **A file in the test repository** that the client expands.

**Decision:** Option 1.

**Rationale:** The template belongs to the lab. Thus it is in the lab
namespace, near the exporters that it describes. The persons who connect
the exporters keep it up to date. With option 3, each repository has a
copy, and each copy becomes incorrect when the lab changes.

With option 1, the scheduler does not change, because it gets a member-form
lease. A lease also keeps its meaning. With option 2, a template change
changes active leases, and a deleted template breaks them.

Clients have no Kubernetes access. Thus they read templates through
`ClientService`. A template has no status. `ClientService` calculates
`AVAILABLE` when a client lists templates.

A template can give defaults for the duration and for the shared clients,
because a regular lease has these two values. It cannot define hooks. Hooks
stay in the exporter configuration.

### DD-11: Only templates and the CLI make member-form leases

**Alternatives considered:**

1. **Templates from all clients. Ad-hoc members from the CLI only.**
2. **Ad-hoc members from all clients.**
3. **Templates only.**

**Decision:** Option 1.

**Rationale:** Selectors and constraints describe a lab, not a test. With
option 2, test code contains lab data, and a lab change breaks the test.
With option 1, a test gives a template name and its roles. Each lab
publishes a template with that name for its own exporters.

Some work has no template: a new pair of devices, fault isolation, or a
template check before its release. With option 3, that work must wait for
an administrator. This work is interactive and short, thus the CLI is the
correct tool.

This rule is a limit of the client API. It is not an authorization rule.
The CLI and the libraries call the same `CreateLease` RPC. A server limit on
ad-hoc requests is a future possibility.

### DD-12: A request can only make a template narrower

**Alternatives considered:**

1. **Narrower only.** A request can add labels to the selector of a role
   and exclude optional roles. It also sets the duration, the begin and end
   times, the tags, the context, and the shared clients.
2. **Free change.** A request can change all fields of the lease.
3. **No change.** A request gives only the template name and a duration.

**Decision:** Option 1.

**Rationale:** An exporter that matches the narrower selector also matches
the template selector. Thus the constraints stay correct. An excluded role
stays in the lease without an exporter. For the controller, it is the same
as an optional member that did not bind.

With option 2, the template does not describe the lease. With option 3, a
lab must publish one template for each variant.

A request cannot give an exporter name for a role. Put `exporterRef` in the
template. The CLI also rejects `-l`/`--selector` and `-n`/`--name` with
`-t`, because these options do not refer to a role.

### DD-13: Typed Python classes that refer to a template

**Alternatives considered:**

1. **A `LeaseMembers` subclass that refers to a template.** The class
   declares roles and driver types. The template contains the selectors.
2. **A `LeaseMembers` subclass that contains its selectors.**
3. **Dictionaries**, for example `config.lease(members={...})`.
4. **Dynamic attributes** that the client adds at connect time.

**Decision:** Option 1.

**Rationale:** Today, `connect()` gives a tree without types. An incorrect
role or driver name fails only when the test gets to that line. With a
class, a type checker finds these errors before the request.

- Options 2 and 3 put the lab setup in test code. DD-11 excludes this.
- Option 4 gives no static types, and a role can hide a client attribute.

Each member type is a `CompositeClient` subclass. Exporter client trees
use this base class today. A code generator can make the same shape when
ExporterClass gives a language-neutral description of the drivers. This JEP
defines no interface identifiers.

### DD-14: Shell commands and metadata for each role

**Alternatives considered:**

1. **Roles as names of `j`, and one variable for all members.**
   `j ROLE ...` gets the drivers of a member. `JMP_LEASE_MEMBERS` describes
   all members.
2. **Use the variables for one exporter** and set them to the first member.
3. **One socket with the roles as the top level of one client tree.**
   `env()` then gives that tree.

**Decision:** Option 1.

**Rationale:** Tests use `env_with_metadata()` to record the device that
they used. A member-form lease has one record for each role.

- With option 2, a current script gets an arbitrary member. DD-2 prevents
  this error.
- With option 3, a current script gets a tree that does not contain its
  driver names. It also gets the metadata of no exporter.
- With option 1, that script fails with a clear message. `env_members()`
  gives the members with the metadata of each role.

### DD-15: A member can use the exporters of an `ExporterSet`

**Alternatives considered:**

1. **Select by labels.** A member selector agrees with the labels of the
   exporters of an `ExporterSet`, as a scalar lease does in JEP-0014.
2. **A new reference field**, for example `exporterSetRef`, as a third
   selection source for a member.
3. **No support.** A member-form lease uses only exporters that exist.

**Decision:** Option 1.

**Rationale:** Some setups have physical devices and virtual devices
together, for example a physical ECU and a virtual head unit. With option
3, such a setup must have two leases, and this JEP removes that problem.
The first version of this JEP permitted only virtual devices or only
physical devices in one lease. This decision replaces that rule.

In JEP-0014, a lease does not refer to an `ExporterSet` by name. It selects
exporters by labels, and the set uses the same labels to count demand.
Option 1 keeps this model:

- The member schema and its CEL rules do not change.
- One template operates in a lab with physical phones and in a lab with
  virtual phones, if the labels are the same.
- Option 2 puts a second selection model in the lease.

The exporter-set controller counts demand for each member, and the lease
controller waits for the set (*Members and `ExporterSet`*).

### DD-16: `Ready` waits for the `beforeLease` hooks

**Alternatives considered:**

1. **`Ready` at bind.** A scalar lease is `Ready` when it has an exporter.
2. **`Ready` when the `beforeLease` hook of each bound member is
   complete.**
3. **A ready state for each member only.** Each client examines the
   members.

**Decision:** Option 2 for member-form leases. Scalar leases do not change.

**Rationale:** A test with many devices can start only when all its devices
are ready. With option 1, the client waits for each exporter in sequence
when it connects. A failure of the last hook then comes after the work on
the other members started. With option 3, each client must contain the
same wait. With option 2, the lease gives one signal, and its message gives
the roles that are not ready (*Lease state*).

## Design Details

### Template expansion

`ClientService.CreateLease` expands a template request in the namespace of
the caller. It does these steps before it writes the lease:

1. Read the `LeaseTemplate`. If it does not exist, return `NOT_FOUND`.
2. Examine the request. Return `INVALID_ARGUMENT` with the role or the
   field if one of these conditions is false:
   - Each excluded role and each `member_selectors` key is a template role.
   - Each excluded role is optional.
   - The request does not exclude a role and also make it narrower.
   - The request does not make an `exporterRef` member narrower.
   - No `member_selectors` entry gives a different value for a label key of
     the template selector.
3. Copy `members`. Set `excluded: true` on each excluded role. Add each
   `member_selectors` entry to the selector of its role with AND.
4. Copy `constraints` without changes.
5. Set the duration. Use the request duration. If the request has none and
   does not give the begin time and the end time together, use
   `defaults.duration`. If there is no default, the current rules for a
   request without a duration apply.
6. Set `spec.sharedWith`. Use the request list. If the request has none,
   use `defaults.sharedWith` without the client that makes the request.
7. Copy the begin time, the end time, the tags, and the context from the
   request. These fields have no template default.
8. Set `spec.templateRef` to the template name and its
   `metadata.generation`.
9. Create the lease through the current path. Thus the CEL rules and the
   access policy apply. The checks of the shared clients and the lease
   name also operate as they do today.

### Bind procedure

`reconcileStatusExporterRef` becomes `reconcileStatusMembers`. The scalar
path uses the same code with one synthetic member and writes
`status.exporterRef`.

```{mermaid}
flowchart TD
    resolve["For each member: get candidates from exporterRef or selector<br/>Remove exporters without access<br/>Remove disabled exporters, but not for exporterRef with allowDisabled"]
    precheck{"Does each required member<br/>have a candidate?"}
    reject["Set the reason for that member<br/>Create no claims"]
    filter["For each member: remove offline, claimed, group-held,<br/>cleanup, and spot-held exporters<br/>Sort with orderApprovedExporters"]
    assign["For each combination of constraint values (DD-5):<br/>assign required members, then optional members (DD-4)"]
    complete{"Does a combination assign<br/>each required member?"}
    classify["Report the first filter stage that fails<br/>Give the roles that compete<br/>Create no claims"]
    requeue["Put the lease back in the queue"]
    claim{"Start an attempt (DD-3)<br/>Create claims in sorted order<br/>All created?"}
    rollback["Delete the claims of this attempt<br/>Pending: NotAvailable"]
    commit["Write status.members, status.exclusionGroups,<br/>and the priority in one Status().Update()"]

    resolve --> precheck
    precheck -->|No| reject
    precheck -->|Yes| filter --> assign --> complete
    complete -->|No| classify --> requeue
    complete -->|Yes| claim
    claim -->|No| rollback --> requeue
    claim -->|Yes| commit
```

**Checks for one member.** The current checks apply to each member and
report their current reasons with the role: `ExporterNotFound`,
`ExporterDisabled`, `AllDisabled`, `NoAccess`, and the `Unsatisfiable`
result for spot access. `SelectorMismatch` cannot occur, because a member
has only one selection source.

`allowDisabled` operates as it does for a scalar lease. The controller
keeps a disabled exporter as a candidate only for a member that has
`exporterRef` and `allowDisabled: true`. For a member with a `selector`,
the field has no effect.

- A required member that fails makes the lease `Unsatisfiable`. The lease
  then ends, as a scalar lease does today.
- An optional member that fails becomes `Omitted` with that reason.
- An optional member that gets no exporter in the assignment also becomes
  `Omitted`. Its reason is that of the filter stage that removed its
  candidates, for example `NotAvailable`.
- An excluded member becomes `Omitted` with the reason `Excluded`. The
  controller does no checks for it.

**Assignment.** The controller assigns the required members first, in spec
sequence, and then the optional members. An optional member cannot remove
the exporter of a required member. The candidate order is total, because
the name is the last sort key. Thus the result is always the same.

- The assignment binds as many optional members as possible, in spec
  sequence.
- When first fit binds all the members, the result is the same as first
  fit.
- In all other conditions, the assignment uses the shortest augmenting
  path. Thus it moves few members.

**Failure classification.** Each filter stage keeps a subset of the
candidates of the stage before it. If the assignment fails, the controller
does the assignment again for each stage in sequence. It reports the first
stage that fails:

| Stage: the candidates are also... | Condition | Reason |
| --- | --- | --- |
| Selected, enabled, and approved by policy | `Unsatisfiable` | `InsufficientExporters` or `ConstraintUnsatisfiable` |
| Registered and online | `Pending` | `Offline` |
| Not claimed by a different active lease or its exclusion group | `Pending` | `NotAvailable` |
| `Available` and not in cleanup | `Pending` | `NotReady` |
| Not held with spot access | `Pending` | `NotAvailable` |

- Each stage does the full constraint search.
- If the members select an `ExporterSet`, too few exporters give `Pending`
  with the reason `NotAvailable`, not `Unsatisfiable` (*Members and
  `ExporterSet`*).
- With one member, the result is the same as today.
- `InsufficientExporters` occurs only with two or more members. An example
  message is: *roles `phone`, `phone-2` must have 2 different exporters;
  only 1 is available: `px-01`.*
- `ConstraintUnsatisfiable` occurs when an assignment exists without the
  constraints but not with them. The message gives the constraint and the
  values.

**Exclusion groups.** The controller removes a candidate if a different
active lease holds its group. It also removes a candidate if an exporter of
its group is in cleanup (DD-7). Many members of one lease can bind
exporters of the same group. The exporters of the group that the lease did
not bind get no `status.leaseRef`. Thus they cannot release the lease.

Spot access eviction is not in `main`. When it is, it must replace the
claims of the full group.

**Cache.** The assignment reads claims from the cache. Thus it can select
an exporter that a different lease claimed a moment before. The create
operation for the claim then fails, and the controller tries again (DD-3).

### Lease state

```{mermaid}
flowchart TD
    pending["Pending<br/>The lease holds no exporters"]
    assignable{"Can the controller assign<br/>the required members?"}
    unsatisfiable["Unsatisfiable<br/>The lease holds no exporters"]
    bound["Bound<br/>The lease holds all its claims<br/>The beforeLease hooks run"]
    ready["Ready"]
    degraded["Degraded<br/>The lease keeps its members"]
    ended["Ended"]

    pending --> assignable
    assignable -->|"Not now"| pending
    assignable -->|"Not possible"| unsatisfiable
    unsatisfiable --> ended
    assignable -->|Yes| bound
    bound -->|"All hooks are complete"| ready
    bound -->|"A member is lost"| degraded
    ready -->|"A member is lost"| degraded
    pending -->|"Release or expiry"| ended
    bound -->|"Release or expiry"| ended
    ready -->|"Release or expiry"| ended
    degraded -->|"Release or expiry"| ended
```

The diagram shows the states of a lease. `Pending` and `Bound` are also
member states. The lease uses the current condition types `Pending`,
`Ready`, `Unsatisfiable`, and `Invalid`. This JEP adds `Degraded`. `Bound`
is not a condition type.

- `Unsatisfiable` and `Invalid` end the lease, as they do today.
- `Ready` is true when all these conditions are true (DD-16):
  - Each required member is `Bound`.
  - No member is `Lost`.
  - The `beforeLease` hook of each bound member is complete.
- The controller reads the hook state from
  `Exporter.status.exporterStatus`. `LeaseReady` shows that the hook is
  complete. An exporter without the hook reports `LeaseReady` immediately.
- While a hook runs, `Ready` is false with the reason `BeforeLeaseHook`.
  The message gives the roles that are not complete. The controller adds no
  time limit. The hook has its own time limit in the exporter
  configuration.
- A hook failure has one of these results:
  - Action `endLease`: the exporter releases the lease, and the member is
    `Lost`.
  - Action `exit`: the exporter stops, and the member is `Lost`.
  - Action `warn`: the hook is complete.
  - An error without an action: the exporter reports
    `BeforeLeaseHookFailed`. `Ready` stays false with that reason.
- A client that requests a lease waits for `Ready` before it gives the
  members to the test. It stops the wait and raises an error with the role
  and the reason when a member is `Lost` or a hook fails.
- A client that attaches to a lease that exists does not wait for `Ready`
  if the lease is `Degraded`. It connects to each `Bound` member. A call to
  a `Lost` role raises an error with the role and the reason. Thus a user
  can attach to a degraded lease and collect logs from the other members
  (DD-8). `jmp shell --lease` shows the lost roles as a warning.
- Expiry, `status.ended`, the `jumpstarter.dev/lease-ended` label, and a
  release by the client (`spec.release`) do not change.

### Lost members

A bound member becomes `Lost` for one of these causes (DD-8):

| Cause | Reason |
| --- | --- |
| The exporter is offline. It unregisters, or the controller gets no heartbeat for the offline time limit (60 seconds today). | `Offline` |
| An administrator deletes the exporter. | `ExporterDeleted` |
| The exporter releases the lease with `ReleaseLease` or with a status report. A hook with the failure action `endLease` does this. | `ExporterReleased` |

A hook with the failure action `exit` stops the exporter. Its member is
then `Lost` with the reason `Offline`.

These rules apply to a `Lost` member:

- **Final:** The member does not become `Bound` again in that lease.
- **Controller start:** After a start, the controller has no current
  heartbeat data. It does not make a member `Lost` with the reason
  `Offline` until the offline time limit is complete after its start. Thus
  a controller restart does not degrade the leases.
- **`Dial`:** A call for that member returns `FAILED_PRECONDITION`.
- **Exporter session:** The controller removes `status.leaseRef` from the
  exporter. The exporter then ends its session and runs its `afterLease`
  hook, as it does at the end of a scalar lease.
- **Claim:** The lease keeps the claim until it ends. Thus no other lease
  gets that exporter in this time. Claims then change only at the bind and
  at the end of a lease, and the procedure of DD-3 stays small. The DUT can
  also be in a state that the test caused.
- **Watches:** The lease controller must also get the exporter events for
  bound leases and for deleted exporters. Today it gets them only for
  pending leases.

### Readers of `status.exporterRef`

A member-form lease has no `status.exporterRef` (DD-2). These controller
paths read the field today. They do not change for a scalar lease, with one
exception: `Exporter.status.leaseRef` also depends on the claim (DD-3).

| Path | Today | For a member-form lease |
| --- | --- | --- |
| `ControllerService.Listen` | Accepts only the exporter in `status.exporterRef`. It keeps one queue for each lease name. | Accepts the exporter of each bound member. The queue key is the lease and the exporter. |
| `ControllerService.Dial` | Connects to the exporter in `status.exporterRef`. | Connects to the exporter of `member_name`. |
| `ExporterReconciler.reconcileStatusLeaseRef` | Sets `Exporter.status.leaseRef` from `status.exporterRef`. | Sets it when a claim for the exporter exists and the lease shows the exporter as a `Bound` member (DD-3). The reconciler also watches claims. |
| `assignedLease`, for `Status` and `UpdateLeaseHooks` | Accepts the lease only if `status.exporterRef` is the exporter. | Accepts a lease that has the exporter as a `Bound` member. Each member keeps its own hook record. |
| Release by the exporter (`ReportStatus`, `ReleaseLease`) | The exporter in `status.exporterRef` can set `spec.release`. | The member of that exporter becomes `Lost`. The lease is `Degraded` and keeps the other members (DD-8). |
| `reconcileSharedWithPolicies` | Applies the policy for `status.exporterRef` to `spec.sharedWith`. Before the bind, it gives the full list. | Lists a client in `status.sharedWith` only if the policy lets it use each bound member. Before the bind, the list is empty. The status update that binds the members also writes the list. Thus the lease is not bound with a list that the policy did not examine. |
| `ClientService.UpdateLease` | Reads `status.exporterRef` to know that the lease started. It checks the policy of that exporter for a transfer and for a new shared client. | Reads `status.beginTime` to know that the lease started. It checks the policy of each bound member. |
| Owner reference | The lease has a controller owner reference to its exporter. | No owner reference to an exporter. Kubernetes deletes an object when all its owners are deleted, and a lease must stay when its exporters are deleted. The controller watches the exporters and makes the member of a deleted exporter `Lost`. |
| `reconcileStatusBeginEndTimes` | Sets `status.beginTime` and `Ready` when `status.exporterRef` is set. | Sets `status.beginTime` when the members bind. Sets `Ready` when the `beforeLease` hooks are complete. |
| `countPendingLeases` in the exporter-set controller | Counts a pending lease without `status.exporterRef` if `spec.selector` agrees with the labels of the set. | Counts each member of a pending lease, as *Members and `ExporterSet`* describes. |
| `leaseWantsPoolMember` and `findExporterSetsForLease` in the exporter-set controller | Read `spec.selector` to find the sets of a lease. | Also read the selector of each member. |
| Client code that shows the lease of an exporter | Reads `Lease.exporter`. | Also reads `member_statuses`. |
| Lease acquisition metric | Counts a success when `status.exporterRef` is set. | Counts a success when the members bind. |
| `kubectl get leases` | The `Exporter` column shows `status.exporterRef.name`. | The column is empty. `jmp get leases` shows the exporter of each role. |

### Members and `ExporterSet`

An `ExporterSet` creates exporters when pending leases select its labels
(JEP-0014). For a member-form lease, the exporter-set controller counts one
unit of demand for each member that obeys all these conditions:

- The lease did not end, and its members are not bound.
- The member has a `selector`. It has no `exporterRef`.
- The member is not excluded.
- The selector agrees with the labels that the set gives to its exporters.

The set compares the demand with its free exporters and creates exporters
up to `maxReplicas`. It does not remove an exporter while a counted member
can use it. Thus the set keeps an exporter for a lease that waits for a
different member.

The lease controller has one change for these members. Usually, a lease
with fewer candidates than members is `Unsatisfiable` with the reason
`InsufficientExporters`. If those members select the labels of an
`ExporterSet`, the lease stays `Pending` with the reason `NotAvailable`,
because the set can create exporters. The lease controller reads the
`ExporterSet` objects for this check.

A new exporter is a candidate when it is registered and online. The
subsequent reconcile then binds all the members together. Until then, the
lease holds no exporter.

As for a scalar lease today, the selector must find a minimum of one
exporter of the set. If the set has no exporter, the member has no
candidate, and the lease is `Unsatisfiable`.

### Failure modes

| Failure | Behavior |
| --- | --- |
| The template does not exist | `CreateLease` returns `NOT_FOUND` and creates no lease. |
| The request excludes a required role or makes an unknown role narrower | `CreateLease` returns `INVALID_ARGUMENT` with the role. |
| The request uses two request forms | `CreateLease` returns `INVALID_ARGUMENT` with the fields. |
| A constraint refers to a member that is not in the lease | CEL rejects the lease at admission. |
| A template or an ad-hoc request sets `excluded` | Admission rejects the template. `CreateLease` rejects the request. |
| The Python class does not agree with its template | The client raises `TypeError` before `CreateLease`. |
| No label value satisfies a constraint | The lease is `Unsatisfiable` with the reason `ConstraintUnsatisfiable`. |
| There are more than 1,024 value combinations | The lease is `Unsatisfiable` with the reason `ConstraintLimit`. |
| A different lease holds a necessary claim | The controller deletes the claims of its attempt, sets `Pending` with `NotAvailable`, and tries again. |
| A claim stays after its lease ends | The subsequent lease that uses it deletes it after a live check of the owner. |
| The controller stops between the claims and the status write | The subsequent reconcile starts a new attempt and deletes the claims of the old attempt (DD-3). |
| An old leader continues to reconcile after a new leader binds the lease | Its status update fails. It deletes only the claims of its own attempt. |
| The exporter of a member goes offline, or an administrator deletes it | The member is `Lost` and the lease is `Degraded`. The other members stay. |
| The exporter of a member releases the lease | The member is `Lost` with the reason `ExporterReleased`. The lease is `Degraded`. The other members stay. |
| A `beforeLease` hook of a member fails | *Lease state* gives the result for each failure action. |
| An administrator changes the exclusion group of an exporter that has `status.claimRef` or `status.leaseRef` | Admission rejects the change (DD-7). |
| An administrator changes the exclusion group between the assignment and the mark | The attempt fails. The subsequent reconcile uses the new group. |
| A lease has no required member | CEL rejects the lease at admission. |
| A client attaches to a lease with a `Lost` member | The client connects to the `Bound` members. A call to the `Lost` role raises an error. |
| A client in `defaults.sharedWith` does not exist | `CreateLease` returns the current error for that client. The administrator must correct the template. |
| An `ExporterSet` is at `maxReplicas` and has no free exporter | The lease stays `Pending` with `NotAvailable`, as a scalar lease does. |
| Two members select an `ExporterSet` that has one exporter | The lease stays `Pending` with `NotAvailable`. The set creates a second exporter. |
| A member selects an `ExporterSet` that has no exporter | The lease is `Unsatisfiable`, as a scalar lease is today. |
| An administrator changes or deletes a template | No effect on the leases that exist. |

### Security

- **Access policy:** The controller evaluates each member against
  `ExporterAccessPolicy`, as it does for a scalar request (DD-9).
- **Shared access:** `spec.sharedWith` applies to the full lease.
  `status.sharedWith` lists a shared client only if the policy lets it use
  each bound member. `Dial` uses that list for each `member_name`. The
  list is empty before the bind, and the status update that binds the
  members writes it. Thus a shared client cannot connect to a member
  before the controller examined the policy.
- **DUT exclusivity:** A lease that holds an exclusion group gets no access
  to the exporters that it did not bind. Only administrators set groups.
- **Claims:** Only the controller creates or deletes `ExporterClaim`
  objects. Clients and exporters have no access to them.
- **Immutable fields:** `members`, `constraints`, and `templateRef` cannot
  change after creation.
- **Templates:** Only administrators create or change templates.
  `ClientService` gives read-only access in the namespace of the caller.
  This JEP adds no access control for templates. Each client in a namespace
  can request each template of that namespace. The access policy for each
  member applies to the request. Clients show `docs` and `image` as links
  and `instructions` as plain text. They do not run `instructions`.
- **Template sharing:** `defaults.sharedWith` gives no access by itself.
  The shared-access check applies to each listed client.
- **Ad-hoc requests:** The CLI-only rule is not an authorization control. A
  client that makes its own `CreateLease` request can send `members`. The
  access policy sets the limit (DD-11).

### Observability

The JEP-0013 correlation fields get `lease_member` and `lease_template`.
They stay in log bodies and exemplars, as `lease_id` does. They are not
metric labels. The lease acquisition metric records the number of members.

## Test Plan

### Unit Tests

- **CEL rules** for `Lease` and `LeaseTemplate`:
  - The scalar form and `members` cannot be in the same lease.
  - Each member has only one selection source.
  - Role names are DNS labels, and they are unique.
  - Constraints refer only to members of the lease.
  - Only an optional member can have `excluded`.
  - A template rejects `excluded` on a member.
  - `members`, `constraints`, and `templateRef` are immutable.
  - The CRDs install on the oldest supported Kubernetes version and stay in
    the CEL cost limit.
- **Bind procedure:**
  - A reconcile that cannot assign all the required members creates no
    claims.
  - Two members with the same selector bind two different exporters.
  - The DD-4 example binds.
  - An optional member does not remove the exporter of a required member.
  - Without contention, the result is the same as first fit.
  - Each stage of the failure classification reports its reason and the
    roles.
- **Constraints:** The controller selects a shared value at bind time. An
  exporter without the label does not satisfy a constraint. The controller
  ignores an optional member that has no exporter. It reports
  `ConstraintUnsatisfiable` and `ConstraintLimit`. Two constraints with
  many benches stay below the limit.
- **Claims:**
  - Two leases with an old cache do not hold the same exporter or group.
  - A create operation that fails deletes the claims of that attempt.
  - Two leases with the same exporters in opposite member sequence complete
    without a deadlock.
  - The controller deletes an old claim only when its owner ended.
  - A controller stops after one claim. The subsequent reconcile makes a
    different assignment and leaves no claim of the old attempt.
  - An old leader creates a claim and stops. A new leader binds the lease.
    The old leader then continues and does not delete a claim of the bound
    lease.
  - An old leader creates a claim after the bind. The subsequent reconcile
    of the bound lease deletes it.
  - A claim of an ended lease does not remove its exporter from the
    candidates.
- **Exclusion groups:** One lease can bind many exporters of a group.
  Admission rejects a group change on an exporter that has
  `status.claimRef` or `status.leaseRef`. A group change between the
  assignment and the mark makes the attempt fail. An exporter is not a
  candidate while an exporter of its group is in cleanup.
- **Scalar leases:** The current lease controller tests pass without
  changes. A member-form lease with one member has no `status.exporterRef`.
- **Priority and duration:** The lease priority is the minimum member
  priority. An exporter is not a candidate for a member if its policy does
  not permit the duration.
- **Request forms:** `CreateLease` rejects each mixed form and creates no
  lease.
- **Template expansion:** Each step, each error, the `duration` default,
  the `sharedWith` default, and the `templateRef` record.
- **Hooks:** `Ready` stays false until the `beforeLease` hook of each bound
  member is complete. A member without a hook does not delay `Ready`. Each
  failure action gives its result.
- **Lost members:** Each cause gives its reason. The lease becomes
  `Degraded`, and the other members stay. The exporter of the member gets
  no `status.leaseRef`, and the claim stays. A scalar lease ends after a
  release by its exporter, as today.
- **`ExporterSet`:**
  - The exporter-set controller counts each applicable member of a lease
    that is not bound. It ignores `exporterRef` members, excluded members,
    and leases that are bound.
  - The set does not remove an exporter while a counted member can use it.
  - A lease with two members and a set with one exporter stays `Pending`.
- **`Dial`:** Without `member_name`, a member-form lease returns
  `INVALID_ARGUMENT` with the roles. A member that is not `Bound` returns
  `FAILED_PRECONDITION`. A shared client gets access only when the policy
  lets it use each member.
- **`UpdateLease`:** A transfer and a new shared client operate on a
  member-form lease that started.
- **Python client:** A selector gives one client. A `LeaseMembers` subclass
  gives that class. The checks raise `TypeError` for each mismatch. A call
  to a `Lost` role raises an error. `env()` fails in a member-form shell.

### Integration Tests

These tests use a kind cluster with the controller and mock exporters
(`e2e/`):

- **Bind:** A template with two members binds two mock exporters. Each role
  connects to its exporter.
- **Contention:** A small pool gets more scalar and member-form leases than
  it can serve. The test restarts the controller and changes the leader.
  The status of each lease shows all its required members bound or none. No
  exporter or group has two holders. A lease that is not bound owns no
  claims after its subsequent reconcile.
- **Cleanup:** Expiry and release leave no claims.
- **Physical and virtual members:** A template has one member for a mock
  exporter and one member for an `ExporterSet` with no free exporter. The
  set creates an exporter, and the lease then binds the two members.
- **CLI:** `jmp get lease-templates`, `jmp describe lease-template`,
  `jmp shell -t`, ad-hoc `--member`, and `jmp get leases -o mobly`.
- **Compatibility:**
  - An N-1 client with an N controller, for the scalar workflow.
  - An N client that requests a template from an N-1 controller.
  - An N-1 client that attaches to a member-form lease. `Dial` returns
    `INVALID_ARGUMENT`.
  - An N-1 exporter with an N controller.
  - An upgrade with bound scalar leases. Each lease gets its claim, and no
    exporter loses its `status.leaseRef`.

### Hardware-in-the-Loop Tests

- **Physical devices:** A phone and a head unit are on exporters on
  different lab hosts. One lease binds them through an `rf-domain`
  constraint. The test completes a projection session through the air.
- **Exclusion group:** Two exporters serve one DUT. A second lease cannot
  bind the second exporter while the first lease is active.

### Manual Verification

- `jmp shell -t` and `jmp shell --lease`: calls with a role prefix operate,
  and Ctrl+C leaves no held leases.
- A current Mobly suite runs without changes against an exported testbed.
- `jmp get leases` and `kubectl get leases` show scalar leases and
  member-form leases together.

## Acceptance Criteria

**Leases**

- [ ] `Lease` has `spec.members[]`, `spec.constraints[]`, and
      `status.members[]`, with the CEL rules.
- [ ] A reconcile that cannot assign or claim each required member binds
      no member and deletes the claims of its attempt.
- [ ] No two active leases hold the same exporter or exclusion group. This
      is true with concurrent requests, an old cache, a controller restart,
      and a leader change, when all controller Pods run version N (DD-3).
- [ ] A lease binds when an assignment exists (DD-4).
- [ ] A `sameLabel` constraint binds members with a shared value that the
      controller selects at bind time (DD-5).
- [ ] The controller evaluates `ExporterAccessPolicy` for each member, for
      the owner and for shared clients.
- [ ] The lease priority is the minimum member priority. The policy of
      each member permits the lease duration.
- [ ] `status.exporterRef` does not change for scalar leases and is nil for
      each member-form lease. The current scalar tests pass without changes.
- [ ] Each path in *Readers of `status.exporterRef`* operates with
      member-form leases.
- [ ] `Ready` waits for the `beforeLease` hook of each bound member.
- [ ] When an exporter releases a member-form lease, its member becomes
      `Lost` and the lease keeps the other members (DD-8).
- [ ] `Dial` gets `member_name`. It refuses a member that is not `Bound`.
- [ ] A member can select the exporters of an `ExporterSet`, and the set
      creates an exporter for a pending member (DD-15).
- [ ] After an upgrade, each bound lease has its claims before the
      controller binds a new lease.

**Templates and clients**

- [ ] The `LeaseTemplate` CRD exists, with `GetLeaseTemplate` and
      `ListLeaseTemplates`.
- [ ] `CreateLease` accepts only one request form.
- [ ] `CreateLease` expands a template as *Template expansion* describes.
      Only `duration` and `sharedWith` have template defaults.
- [ ] A template that sets `excluded` on a member fails admission.
- [ ] A request can make selectors narrower and exclude optional roles. It
      cannot add roles or constraints (DD-12).
- [ ] The CLI has `-t`, `--without`, `--member`, and `--same-label`.
      `jmp get lease-templates` and `jmp describe lease-template` show the
      templates.
- [ ] `jmp get leases -o mobly` writes a testbed file with one device for
      each member that has ADB.
- [ ] `config.lease(M)` gives a typed `LeaseMembers` instance. The check
      before the request rejects a class that does not agree with its
      template (DD-13).
- [ ] `JumpstarterTest` accepts a `members` class.
- [ ] A member-form shell sets `JMP_LEASE_MEMBERS`, and `j` shows the roles
      (DD-14).

**Topology**

- [ ] A phone and a head unit on exporters on different lab hosts complete
      a projection session in one lease.

## Backward Compatibility

All the schema changes and protocol changes add items.

- **CRD:** `Lease` gets optional fields. `LeaseTemplate` and
  `ExporterClaim` are new. `Exporter` gets the optional
  `spec.exclusionGroup`. No current field changes its type, meaning, or
  default. Each lease that exists today stays valid. The controller role
  gets read access to templates and create, read, and delete access to
  claims.
- **`status.exporterRef`:** It does not change for a scalar lease. It is
  nil for a member-form lease. An N-1 client that attaches to a member-form
  lease gets `INVALID_ARGUMENT` from `Dial` (DD-2).
- **`Ready`:** A scalar lease is `Ready` at bind, as today. A member-form
  lease is `Ready` after the `beforeLease` hooks (DD-16).
- **Protocol:** There are new fields in current messages and two new
  `ClientService` RPCs. An N-1 client operates with an N controller.
- **N client, N-1 controller:** A request with `members` or `template`
  contains no `selector` and no `exporter_name`. The current `CreateLease`
  rejects it with `INVALID_ARGUMENT`. Thus the controller does not create a
  lease for one device in error. An N-1 controller also returns
  `UNIMPLEMENTED` for `ListLeaseTemplates`. For each of these errors, the
  client shows a message that the controller is too old.
- **Operator upgrade:** The upgrade adds CRD fields. A conversion webhook
  is not necessary. These rules apply to the leases that exist:
  - At its start, the N controller creates claims for the bound leases,
    oldest first. It does this before it binds a new lease.
  - Until a bound lease has its claim, the exporter reconciler sets
    `status.leaseRef` from `status.exporterRef`, as today.
  - If two leases hold one exporter, the older lease gets the claim. The
    controller sets `Invalid` with the reason `ExporterConflict` on the
    newer lease. That lease then ends.
  - An N-1 controller that runs during the upgrade can bind a lease
    without a claim. The N controller creates that claim in its subsequent
    reconcile of the lease, with the same conflict rule. Thus the guarantee
    of DD-3 starts when no N-1 controller runs.
- **Rollback:** Remove the member-form leases before a rollback.
- **Scalar leases:** They also get claims. Thus they get the same
  exclusivity guarantee.
- **`ExporterSet`:** The resource does not change. The exporter-set
  controller also counts the members of member-form leases that are not
  bound. The lease controller gets read access to `ExporterSet` objects.

## Consequences

### Positive

- One lease gets all its members in one status write. The members have one
  lifetime.
- No two leases hold one exporter or one exclusion group (DD-3).
- The lab keeps the setup data in one template. A test runs without changes
  in each lab that publishes that template.
- A constraint lets a request use a free bench with the correct wiring.
- One lease can contain physical devices and virtual devices (DD-15).
- `jmp get lease-templates` shows the templates and their documentation.

### Negative

- Readers must read the scalar form and the member form.
- A test cannot define a new setup. An administrator must publish a
  template, or the user must make a lease with the CLI (DD-11).
- A typed class and its template can become different. The check finds
  this only at run time.
- The constraint search multiplies the bind work by the number of value
  combinations (DD-5).
- A client cannot release one member before the others.
- The bind procedure writes one claim for each exporter and group, and one
  more status update for the attempt number.
- When a member moves to a different candidate, the lease priority can
  decrease (DD-4, DD-9).

### Risks

- **Contention:** In a small, busy pool, a lease with many members can wait
  behind leases with fewer members (DD-3).
- **Other readers:** A reader that is not in *Readers of
  `status.exporterRef`* can think that each bound lease has that field.
  Examine the other readers before the release.
- **Capacity:** There is no limit on resources across the members of one
  lease.
- **Many templates:** Labs can publish many almost equal templates. The
  narrower request (DD-12) decreases this risk.
- **Idle pool exporters:** An `ExporterSet` can create an exporter for a
  member while a physical member of the same lease is not free. The new
  exporter is then idle until the lease binds or ends.
- **Held exporters:** The exporter of a `Lost` member can report that it is
  available while the claim of the lease holds it.
- **Upgrade:** The guarantee of DD-3 starts only when no N-1 controller
  runs.
- **Template access:** Each client in a namespace can request each template
  of that namespace. Access control for templates is a future possibility.

## Rejected Alternatives

The Design Decisions give the alternatives for each decision. These are
the higher-level alternatives:

- **Keep leases that the client manages.** This keeps the partial results
  and the independent lifetimes.
- **Add a `LeaseGroup` or a `LeaseSet`.** Child leases must have a recovery
  procedure for partial results (DD-1).
- **Lease devices independently in one exporter.** Without a DUT inventory,
  two clients can control one assembly (DD-6).
- **Make a test runner for tests with many devices.** Current runners can
  use the leased devices. This JEP supplies only the allocation.
- **A different JEP for templates.** Without templates, the Python API and
  the CLI must each have an ad-hoc method for members. DD-11 keeps
  that method only in the CLI.
- **One JEP for leases and port forwards.** The first version of this JEP
  did this. The two parts are now JEP-0015 and JEP-0016, because the one
  document was too large to review.

## Prior Art

- **LAVA MultiNode** gives named device roles in one job.
- **Mobly** supplies the testbed format for the export command.
- **Kubernetes gang scheduling** shows the coordination that is necessary
  when claims are in different objects.
- **Kubernetes controller expectations** let a controller count its own
  writes before its cache shows them. DD-3 uses API server claims as an
  alternative.

## Unresolved Questions

For the review:

- **Synchronization:** A test with roles in different processes must
  coordinate its steps. Is coordination in the client sufficient, or must
  the lease supply a shared signal?
- **Member recovery:** `Lost` is final in this JEP. Must a member be able
  to become `Bound` again after a short exporter restart?
- **Claim of a `Lost` member:** The claim stays until the lease ends. Must
  the controller release it earlier when the exporter released the lease?

For the implementation:

- The format of `jmp get leases` for many members.
- A fast calculation of `AVAILABLE` for large namespaces.
- The combination limit for constraints. The proposal is 1,024.

## Future Possibilities

These items are not part of this JEP:

- **Fair scheduling:** Sort the leases that wait by priority and age.
  Reserve exporters for a lease as they become free.
- **Assignment by priority:** When many assignments exist, select the one
  with the highest minimum member priority.
- **More constraints:** Add rules to the `LeaseConstraint` `oneof`, for
  example `differentLabel` or label expressions.
- **Template parameters:** Let a request set values such as an Android
  version.
- **`Device` resource:** An inventory of the DUTs behind each exporter. A
  member can then select one DUT (DD-6).
- **Typed members through ExporterClass:** A member refers to an exporter
  class. The controller then binds only exporters with the correct drivers.
- **Code generation from templates:** Generate the typed lease class in
  each language from a template.
- **Policy and quota:** Limits across the members of a lease, and the
  release of one member.
- **Template access control:** A list of clients or a policy that limits
  the clients that can request a template.
- **Limits on ad-hoc requests:** A policy that limits ad-hoc member
  requests, for example to administrators (DD-11).
- **Sharing for each member:** Share only some members of a lease with a
  client.

## Implementation History

- 2026-09-01: JEP proposed, with port forwards in the same document
  ([#1069](https://github.com/jumpstarter-dev/jumpstarter/pull/1069)).
- 2026-11-24: Changes from the review. The `status.claimRef` mark stops
  group changes. A client can attach to a degraded lease. The bind writes
  the shared-client list. A lease has no owner references to exporters.
- 2026-11-23: The port forwards moved to JEP-0016. The text follows
  ASD-STE100. The review questions about exporter release, hooks,
  `ExporterSet` members, and template sharing became decisions.

## References

- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md)
- [JEP-0013: Metrics, Tracing, and Log Observability](JEP-0013-observability-telemetry-logs.md)
- JEP-0016: Multi-Exporter Port Forwarding (the second part of the first
  version of this JEP)
- ExporterClass Mechanism (draft, not submitted)
- [jumpstarter-dev/jumpstarter#47](https://github.com/jumpstarter-dev/jumpstarter/issues/47)
  — `LeaseTemplate`
- [jumpstarter-dev/jumpstarter#942](https://github.com/jumpstarter-dev/jumpstarter/pull/942)
  — lease sharing
- [Mobly](https://github.com/google/mobly)
- [LAVA MultiNode](https://docs.lavasoftware.org/lava/multinode.html)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
