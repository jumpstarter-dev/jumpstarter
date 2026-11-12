# JEP-0018: mTLS Workload Identity as the Default Authentication Mechanism

| Field             | Value                                                                  |
| ----------------- | ---------------------------------------------------------------------- |
| **JEP**           | 0018                                                                   |
| **Title**         | mTLS Workload Identity as the Default Authentication Mechanism         |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                                              |
| **Status**        | Draft                                                                  |
| **Type**          | Standards Track                                                        |
| **Created**       | 2026-11-11                                                             |
| **Updated**       | 2026-11-12                                                             |
| **Discussion**    | https://github.com/jumpstarter-dev/jumpstarter/pull/1336               |
| **Requires**      |                                                                        |
| **Supersedes**    |                                                                        |
| **Superseded-By** |                                                                        |

---

## Abstract

Jumpstarter authenticates clients and exporters with bearer tokens: OIDC tokens
or controller-signed JWTs sent in the `authorization` header. This JEP makes
**mutual TLS with SPIFFE-style X.509 identities** the default instead. The
controller and routers verify a client certificate against one or more
configured **trust domains**, map its SPIFFE ID to a Jumpstarter username with
the same CEL mapping model the JWT authenticators already use, and authorize it
exactly as today. Certificates come from pluggable **identity issuers**: any
platform that meets a small, vendor-neutral **integration contract** (issue
X.509-SVIDs, publish a trust bundle, deliver credentials through a standard
channel), or a new **built-in enrollment endpoint** where the controller acts
as a registration authority in front of a cert-manager issuer. Clients and
exporters obtain credentials through pluggable **credential sources** (exec,
SPIFFE Workload API, enrollment, file). No Jumpstarter component depends on, or
is named after, a specific platform. The JEP documents **reference
integrations** to prove the contract: Teleport as a fully worked commercial
example, OpenZiti with SPIRE as a fully worked open-source example, and SPIRE
alone as the vendor-neutral conformance target. Bearer tokens keep
working throughout, and are phased out by default only for new installations.

## Motivation

### Bearer tokens are the wrong credential for exporters

Exporters run on small hosts in labs, on benches, and increasingly on untrusted
networks. Today each one holds a long-lived controller-signed JWT (one year by
default, `authentication.internal.tokenLifetime`). Token rotation exists
(`RotateExporterCredential`, allowed in the second half of the token's life,
with a lineage check so only the current or previous token can rotate), but the
token is still a bearer credential: anyone who copies the exporter config file
can impersonate the exporter from anywhere until the token expires.

Rotation also writes the new token **back into the exporter config file**
(`_persist_rotated_token`). That conflicts with declarative host management.
With Flight Control or any GitOps tool reconciling the exporter config, either
the fleet overwrites the rotated token, or the device drifts from its declared
state. A read-only config is tolerated (the write failure is logged and the
new token is kept in memory), but only until the token on disk falls out of
the current-or-previous lineage: after the second rotation a restarted
exporter is locked out. Per-device tokens also mean per-device secrets have to be minted,
delivered, and protected for every host, which blocks the goal of
secret-free, identical fleet templates.

### Clients have no way to reuse an existing identity

Users already have a corporate identity, and often an active session in an
access or identity platform: an access proxy such as Teleport, a SPIFFE/SPIRE
deployment, a service mesh, or a corporate PKI. Today they must still run
`jmp login`, and
CI pods must be given a token. There is no way to say "this caller already has a
verifiable workload identity; trust it."

### Identity is entangled with network reachability

Labs reach the controller through very different paths: a LAN, a Tailscale or
NetBird tailnet, plain WireGuard, an application-layer access proxy, or a
public Ingress. These paths rewrite source addresses (subnet routers, routing
peers, proxy tunnels), so the controller cannot derive identity from
the network. Bearer tokens survive these paths, but so does a stolen token. A
credential bound to a private key survives every path and is useless when
copied.

### TLS trust is weak at bootstrap

The client supports `tls.insecure`, which fetches the controller's certificate
chain without verifying it and uses that chain as the trust root for the
connection. Nothing is stored, so this is weaker than trust on first use: the
chain is fetched again, unverified, for every new channel. It exists because
there is no good way to distribute the CA. A design that distributes a trust
bundle together with the identity removes the reason to enable it.

### Groups are not available for authorization

Authorization today matches a single username against the object's internal
subject or its `spec.username`. Identity sources that carry team or role
information cannot pass it through, which limits what `ExporterAccessPolicy`
(which selects clients by label only) and the proposed driver access rules can
express.

### User Stories

- **As a** new team member with an existing session in my organization's
  access platform (for example Teleport), **I want to** run `jmp` and
  immediately get access appropriate to my team, **so that** onboarding needs
  no Jumpstarter-specific steps from an administrator.
- **As a** platform team running a different identity system than our partner
  labs, **I want** Jumpstarter to accept identities from whatever platform each
  site uses, **so that** we are not forced onto one vendor to share hardware.
- **As a** lab administrator, **I want to** deploy exporters to Raspberry Pis on
  untrusted networks using Flight Control and FDO, **so that** devices onboard
  themselves with no per-device secrets in my fleet templates or Git.
- **As a** developer on a LAN or a tailnet with no access platform, **I want
  to** log in once with the corporate IdP and receive a short-lived
  certificate, **so that** I get the same security properties anyway.
- **As a** CI pipeline author, **I want** Tekton tasks to authenticate with an
  automatically mounted workload certificate, **so that** pipelines store no
  Jumpstarter secrets.
- **As a** security engineer, **I want** an offboarded user or a decommissioned
  device to lose access within the credential TTL without hunting down tokens,
  **so that** revocation is predictable.

## Proposal

### Concepts

- **Identity document:** an X.509 certificate with exactly one URI SAN holding a
  SPIFFE ID (`spiffe://<trust-domain>/<path>`), used for TLS client
  authentication. Jumpstarter does not require a full SPIFFE implementation; it
  requires the X.509-SVID format.
- **Trust domain:** a named set of CA certificates (a *bundle*) that is
  authoritative for SPIFFE IDs under `spiffe://<trust-domain>/`. The controller
  verifies each certificate only against the bundle of the trust domain named
  in its SPIFFE ID.
- **Identity issuer:** anything that issues identity documents. Jumpstarter
  ships one (the **enrollment endpoint**) and accepts any other issuer that
  meets the integration contract.
- **Integration contract:** the requirements an external identity platform
  must meet for Jumpstarter to use it with configuration alone (see
  [Integrating an identity platform](#integrating-an-identity-platform)).
- **Reference integration:** a documented, end-to-end tested configuration of
  one platform against the contract. Reference integrations are documentation
  and test fixtures, not code paths.
- **Transport:** how bytes reach the controller and routers (LAN, overlay
  network, access proxy tunnel). Transport is independent of identity;
  Jumpstarter only requires that TLS is not terminated in between.
- **Credential source:** the client- or exporter-side component that obtains
  and renews credentials. Bearer tokens become one credential source among
  several.
- **Identity mapping:** CEL expressions that turn a verified SPIFFE ID into a
  Jumpstarter username, object name, and groups, and restrict which principal
  kinds (`Client`, `Exporter`) the identity may act as.

### Architecture overview

```{mermaid}
flowchart LR
  subgraph Issuers
    EX["External issuers meeting the contract<br/>(e.g. SPIRE, Teleport, csi-driver-spiffe)"]
    EN[Jumpstarter enrollment endpoint]
  end
  CM[cert-manager Issuer] --> EN
  ROOT[(Corporate root CA)] -.chains.-> CM
  ROOT -.optional chaining.-> EX
  TM[trust-manager Bundle] --> CTRL
  TM --> RTR

  subgraph Callers
    C[jmp / Python client]
    E[Exporter]
  end
  EX -- "X.509-SVID<br/>(Workload API, exec, or file)" --> C
  EX -- "X.509-SVID<br/>(Workload API, exec, or file)" --> E
  EN -- certificate --> C
  EN -- certificate --> E

  C -- mTLS --> CTRL[Controller]
  E -- mTLS --> CTRL
  C -- mTLS + bound router token --> RTR[Router]
  E -- mTLS + bound router token --> RTR
```

The controller and routers never hold a signing key for client identities. They
verify certificates, map identities, and (for the built-in issuer) relay
certificate requests to cert-manager under a narrow policy.

### What a deployment looks like

#### Controller configuration

The `authentication` block gains an `x509` list next to the existing `jwt`
list. The `grpc` block gains a client authentication mode.

```yaml
authentication:
  internal:
    prefix: "internal:"
  jwt: []                       # unchanged; OIDC and internal tokens keep working
  bearer:
    allowedPrincipals: [Client, Exporter]   # default; see "Authentication chain"
  x509:
    # Any external issuer that meets the integration contract
    - trustDomain: workload.example.com
      bundle:
        configMapRef:
          name: jumpstarter-trust-workload
          key: bundle.pem
      mappings:
        - principals: [Client]
          match: 'spiffe.segments[0] == "jumpstarter" && spiffe.segments[1] == "users"'
          username: '"corp:" + spiffe.segments[2]'
          name: 'spiffe.segments[2]'
          groups: 'spiffe.segments.size() > 3 ? ["team:" + spiffe.segments[3]] : []'
        - principals: [Exporter]
          match: 'spiffe.segments[0] == "jumpstarter" && spiffe.segments[1] == "exporters"'
          # username defaults to the full SPIFFE ID, name to the last path segment

    # Built-in enrollment (LAN, tailnets, anywhere without an external issuer).
    # No mappings: identities in the enrollment trust domain are issued by the
    # controller and resolved structurally (see "Identity mapping").
    - trustDomain: jumpstarter.lab.example.com
      bundle:
        configMapRef:
          name: jumpstarter-trust-internal
          key: bundle.pem

enrollment:
  enabled: true
  trustDomain: jumpstarter.lab.example.com
  issuerRef:
    group: cert-manager.io
    kind: ClusterIssuer
    name: jumpstarter-identity
  clientCertificateDuration: 8h
  exporterCertificateDuration: 24h

grpc:
  keepalive: {}                 # unchanged
  tls:
    clientAuth: Optional        # Disabled | Optional | Required

leasePolicy:
  maxTags: 10                   # existing field
  maxDuration: 8h               # new global ceiling; see "Credential lifetime and leases"
```

`username` defaults to the full SPIFFE ID. `Client.spec.username` and
`Exporter.spec.username` hold the mapped value, so the username comparison in
`BasicAuthorizer` is unchanged. How the caller is resolved to an object *name*
does change; see [Identity mapping](#identity-mapping).

The controller and the router both parse this ConfigMap strictly
(`yaml.UnmarshalStrict`), and the operator renders it from the `Jumpstarter`
custom resource. The new keys therefore need matching fields in the operator's
`Jumpstarter` API, and a controller or router older than this JEP refuses to
start when they are present (see
[Backward Compatibility](#backward-compatibility)).

#### Trust distribution with trust-manager

Each trust domain's bundle is a ConfigMap, typically produced by a trust-manager
`Bundle` (or `ClusterBundle`, depending on the trust-manager release). The
controller and routers watch the referenced ConfigMaps and reload without a
restart, so CA rotation is an overlap rather than an outage.

```yaml
apiVersion: trust.cert-manager.io/v1alpha1
kind: Bundle
metadata:
  name: jumpstarter-trust-internal
spec:
  sources:
    - secret:
        name: corp-root-ca
        key: ca.crt
  target:
    configMap:
      key: bundle.pem
```

Server trust is separate from client trust. The bundle that verifies the
controller's and routers' **serving** certificates is what clients already
receive in the `caBundle` field of `/v1/auth/config` (the operator's
`CA_BUNDLE_PEM`), and what exporters receive through their host management
(for example a Flight Control fleet template). When the serving certificates
and the enrollment issuer chain to the same corporate root, one trust-manager
`Bundle` can feed both.

#### Built-in enrollment

For deployments without an external issuer, the controller exposes an **EST**
endpoint ([RFC 7030](https://www.rfc-editor.org/rfc/rfc7030)) on its existing
gRPC listener (`:8082`). That listener already terminates TLS in the
controller pod, is exposed with TLS passthrough, and already routes HTTP/1.1
requests that are not gRPC to an HTTP handler.

The login service is deliberately **not** used: it serves plain HTTP on `:8086`
and the operator exposes it with *edge* TLS termination, so a client
certificate can never reach it and bearer tokens would be visible to the
ingress. It keeps serving `/v1/auth/config`, which gains an `enrollment` block
advertising the EST URL and the enrollment trust domain.

| Endpoint                                   | Authenticated by                                         | Result                    |
| ------------------------------------------ | -------------------------------------------------------- | ------------------------- |
| `/.well-known/est/cacerts`                 | none                                                      | trust bundle              |
| `/.well-known/est/simpleenroll`            | bearer token (OIDC, internal, or one-time enrollment token) | first certificate      |
| `/.well-known/est/simplereenroll`          | current client certificate (mTLS)                         | renewed certificate       |

The flow:

1. The caller generates a key pair locally. The private key never leaves the
   caller and may live in a TPM or OS keystore.
2. The caller builds a CSR whose only name is the URI SAN
   `spiffe://<trust-domain>/jumpstarter/ns/<namespace>/clients/<name>` (or
   `.../exporters/<name>`). The trust domain comes from `/v1/auth/config`; the
   kind, namespace, and name are the ones the caller already sends as
   `jumpstarter-kind`, `jumpstarter-namespace`, and `jumpstarter-name` metadata.
   It sends the CSR authenticated as above, with the same three values as HTTP
   headers.
3. The controller authenticates and authorizes the caller exactly as it does
   for a gRPC call, which resolves it to its `Client` or `Exporter` object, and
   computes the SPIFFE ID for that object itself. **The CSR is rejected unless
   it contains exactly that URI SAN and nothing else** (no DNS, IP, or email
   SANs, no other URIs). The controller cannot rewrite the request: a CSR is
   signed by the caller's key, and cert-manager issuers and approver-policy
   both act on its contents.
4. The controller creates a cert-manager `CertificateRequest` carrying the
   caller's CSR against `enrollment.issuerRef`, with the configured duration
   and client-auth usages, waits for issuance, and returns the chain.
5. Renewal uses `simplereenroll`, authenticated by the certificate being
   renewed. The controller re-checks that the `Client`/`Exporter` still exists
   before renewing.

The controller is a registration authority, not a CA. cert-manager's
**approver-policy** constrains what the controller's service account can
request, so a compromised controller cannot mint serving certificates,
identities outside its trust domain, or long-lived credentials:

```yaml
apiVersion: policy.cert-manager.io/v1alpha1
kind: CertificateRequestPolicy
metadata:
  name: jumpstarter-identity
spec:
  allowed:
    uris:
      values: ["spiffe://jumpstarter.lab.example.com/jumpstarter/*"]
      required: true
    usages: ["client auth", "digital signature"]
  constraints:
    maxDuration: 24h
  selector:
    issuerRef:
      group: cert-manager.io
      kind: ClusterIssuer
      name: jumpstarter-identity
```

The operator installs this policy and binds it to the controller's service
account when enrollment is enabled. cert-manager is already an optional
operator integration (`spec.certManager`, used for serving certificates);
enrollment additionally needs approver-policy with cert-manager's default
approver disabled, since the default approver would approve the controller's
requests without consulting the policy. Any cert-manager issuer works as the
backend: a CA issuer holding an intermediate signed by a corporate root, Vault,
step-ca, AWS Private CA, and others.

#### Client experience

`ClientConfig` gains an optional `credentials` block. The existing `token` and
`refresh_token` fields are equivalent to `credentials.type: token` and keep
working unchanged.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  name: default
  namespace: lab-a
endpoint: jumpstarter.lab.example.com:443
tls:
  ca: <base64 trust bundle>
credentials:
  type: enrollment            # token | oidc | enrollment | exec | spiffe | file
  enrollment:
    keyStore: file            # file today; os-keystore and tpm in future
    renewBefore: 33%
```

`jmp login` on an enrollment-enabled controller runs the existing OIDC flow,
then immediately enrolls and stores the certificate instead of the bearer token:

```console
$ jmp login https://jumpstarter.lab.example.com --ca-sha256 3f9a…c1
Logged in as alice@example.com
Enrolled certificate spiffe://jumpstarter.lab.example.com/jumpstarter/ns/lab-a/clients/alice (expires in 8h)
```

`--ca-sha256` pins the root of the returned trust bundle, following kubeadm's
`--discovery-token-ca-cert-hash`. The administrator publishes one hash, and the
first connection is verified rather than trusted on first use. The hash covers
the `caBundle` used for the gRPC and EST connections; the login endpoint that
serves `/v1/auth/config` sits behind the ingress's own certificate, which
today is verified against the system trust store or skipped with
`--insecure-tls`. With `--ca-sha256` the fetch may be unverified, because the
bundle it returns is checked against the hash before anything is sent over a
connection that trusts it.

A user whose platform provides a command-line issuer configures an `exec`
source instead, and never runs `jmp login`:

```yaml
credentials:
  type: exec
  exec:
    command: /usr/local/bin/issue-svid   # any issuer CLI
    args: ["--ttl=1h", "--out=${JMP_CREDENTIAL_DIR}"]
    format: spiffe-dir                   # spiffe-dir | json
    interactive: IfAvailable             # Never | IfAvailable | Always
    renewBefore: 33%
```

The exec contract is modeled on Kubernetes client-go credential plugins:

- `jmp` runs the command when it has no valid certificate or the current one
  is inside `renewBefore`. It sets `JMP_CREDENTIAL_DIR` (a private `0700`
  directory) and `JMP_ENDPOINT`.
- `spiffe-dir`: the command writes `svid.pem` (leaf and intermediates),
  `svid_key.pem`, and `svid_bundle.pem` to `JMP_CREDENTIAL_DIR`, the layout
  several SPIFFE tools already produce.
- `json`: the command prints `{"certificate": "<PEM>", "privateKey": "<PEM>",
  "bundle": "<PEM>"}` to stdout.
- Expiry is read from the certificate itself. A non-zero exit means no
  credential is available, and stderr is shown to the user.
- `interactive` controls whether the command may prompt (for example to start
  a platform re-login) when stdin is a terminal.

Named presets for common platform CLIs may ship as shorthand that expands to
an `exec` configuration. Presets are data, not code paths; see the
[Teleport reference integration](#reference-integration-teleport-commercial)
for one.

(exporter-experience)=

#### Exporter experience

An exporter whose host runs a SPIFFE Workload API agent as a sidecar (a SPIRE
agent, Teleport's `tbot`, or similar) reads its identity from the agent's
socket. If the cluster is reachable only through local tunnels (an access
proxy client, an SSH bastion, or similar), `endpointOverrides` points the
exporter at them:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  name: pi-07
  namespace: lab-a
endpoint: grpc.jumpstarter.example.com:443
credentials:
  type: spiffe
  spiffe:
    socket: unix:///run/spiffe/workload.sock
endpointOverrides:
  "grpc.jumpstarter.example.com:443":
    address: 127.0.0.1:8443
  "router-0.jumpstarter.example.com:443":
    address: 127.0.0.1:8444
export:
  # unchanged
```

`endpointOverrides` maps an advertised endpoint to a dial address. TLS server
name verification still uses the advertised host name, so tunnels do not weaken
server authentication. Overrides apply to the controller endpoint and to router
endpoints the controller returns in `ListenResponse` and `DialResponse`
(`router_endpoint`), which is what makes per-application tunnels workable. The
controller picks a router per dial by label match, so every router an exporter
or client might be sent to needs an override.

The exporter config now contains no secrets. It is identical across a fleet
except for the device name, and the exporter never writes to it.

### Identity mapping

Mappings are evaluated in order; the first entry whose `match` is true wins. A
certificate that matches no mapping is rejected. CEL variables:

| Variable              | Type           | Example                                        |
| --------------------- | -------------- | ---------------------------------------------- |
| `spiffe.id`           | string         | `spiffe://workload.example.com/jumpstarter/users/alice/hu` |
| `spiffe.trustDomain`  | string         | `workload.example.com`                         |
| `spiffe.path`         | string         | `/jumpstarter/users/alice/hu`                  |
| `spiffe.segments`     | list(string)   | `["jumpstarter", "users", "alice", "hu"]`      |
| `cert.notAfter`       | timestamp      |                                                |
| `cert.subject`        | map            | `{"commonName": "…"}`                          |

`principals` restricts which object kinds the identity can act as. This matters
because client provisioning auto-creates `Client` objects for authenticated
users: without the restriction, an exporter identity could provision itself as
a client.

#### Resolving the object

Authentication only yields a username. The object a caller acts as is chosen
separately, by `MetadataAttributesGetter`, and its rules do not fit
certificates as they stand:

- For controller-signed tokens and Kubernetes service accounts, the caller
  names the object in `jumpstarter-name` metadata.
- For every other username, the object name is **derived from the username**
  (`normalizeOIDCUsername`: drop everything up to the first `:`, lowercase,
  replace other characters with `-`), and a caller-supplied name that differs
  is rejected.

A username of `spiffe://lab.example.com/jumpstarter/exporters/pi-07` would
therefore be forced onto an object named
`lab-example-com-jumpstarter-exporters-pi-07`, and an exporter config that
says `metadata.name: pi-07` would be refused with a name mismatch. In all
cases the namespace is whatever the caller sends in `jumpstarter-namespace`.

X.509 identities therefore get their own resolution rule:

- Each mapping has a `name` expression. It defaults to the last path segment
  of the SPIFFE ID and must evaluate to a valid object name. A caller-supplied
  `jumpstarter-name` must equal it.
- A mapping may also set `namespace`. When it does, a caller-supplied
  `jumpstarter-namespace` must equal it; when it does not, the caller's value
  is used, as for OIDC today.
- `BasicAuthorizer` then compares the mapped `username` with the object's
  `spec.username`, as it does for OIDC users, and may provision a `Client`
  (never an `Exporter`) when provisioning is enabled.

Identities in the **enrollment trust domain** are not mapped at all. Their
path, `/jumpstarter/ns/<namespace>/{clients,exporters}/<name>`, is written by
the controller and names the object directly, so the controller resolves the
object from the path and requires no `spec.username`. This is the certificate
counterpart of a controller-signed token, which is matched against the
object's internal subject rather than `spec.username`. It also means an object
that was created with an OIDC `spec.username` or with an internal token keeps
working unchanged after it enrolls. Unlike the internal subject, the path does
not contain the object's UID; see Unresolved Questions.

This fits the direction of
[#1319](https://github.com/jumpstarter-dev/jumpstarter/pull/1319) and
[#1320](https://github.com/jumpstarter-dev/jumpstarter/pull/1320), which bind
each kind of credential to its own identity (internal tokens to the internal
subject, external tokens to `spec.username`) and remove `Usernames()`.
Certificates become a third kind with the two rules above.

#### Groups

`groups` is carried in the authenticated user info. This JEP does not change
authorization; it makes groups available to `ExporterAccessPolicy` and driver
access rules in follow-up work.

### Credential lifetime and leases

Short-lived credentials make one failure mode more visible: a user's access
platform session (for example an access proxy certificate capped by a maximum
session lifetime) can expire in the middle of a lease.

- **Global ceiling.** `leasePolicy.maxDuration` is added to the existing
  `leasePolicy` block (which today holds only `maxTags`) and caps every lease.
  A request or extension beyond it is rejected with `INVALID_ARGUMENT`. This
  differs from the per-policy `maximumDuration` in `ExporterAccessPolicy`,
  which is evaluated during exporter selection: a lease that asks for longer
  simply does not match that policy. Operators should set the ceiling at or
  below the session lifetime of their access platform.
- **Client preflight.** Credential sources report their expiry. Before creating
  or extending a lease, `jmp` compares the requested end time with the
  credential's renewable horizon and, depending on configuration, warns, clamps
  the duration, or triggers re-authentication first. This is a usability layer;
  the controller does not trust client-reported expiry.
- **Expiry events.** The client core emits a "credential expires in N minutes"
  event so the CLI, Python API, and MCP server can prompt before the cliff, then
  re-dial automatically once credentials are renewed. Leases already live on the
  controller and survive client disconnects.
- **Prefer extension over long leases.** Each extension re-authenticates the
  caller, so an offboarded user loses hardware at the next extension rather
  than at the end of a long lease.

Long-running hardware operations (flashing in particular) should not depend on
the client connection at all. The exporter-side asynchronous job model planned
for the fastboot and QDL drivers is the intended protection; this JEP only
ensures a client credential expiry is recoverable rather than fatal.

### Integrating an identity platform

Jumpstarter separates three concerns that platforms often bundle together:
**identity** (who the caller is), **transport** (how bytes reach the
controller), and **host management** (how exporter hosts are configured). A
platform may provide any subset. The contract below says what each part must
do for Jumpstarter to use it with configuration alone.

#### Identity requirements

A platform used as an external issuer must:

| ID | Requirement |
| --- | --- |
| **I1** | Issue X.509 certificates with exactly one SPIFFE ID URI SAN and the client-authentication extended key usage. |
| **I2** | Publish the CA bundle for its trust domain in PEM form, so it can be delivered to the cluster as a ConfigMap (statically, through trust-manager, or by a sync job). Rotation should overlap old and new roots. |
| **I3** | Deliver credentials to callers through at least one standard channel: the SPIFFE Workload API, a command-line tool usable as an `exec` source, or files on disk refreshed before expiry. |
| **I4** | Let the administrator control the SPIFFE ID path, either fixed per workload or templated from user or host attributes, so identities can be mapped to Jumpstarter principals. |

A platform that cannot meet I1 to I4 but provides an OIDC identity provider
integrates through **built-in enrollment** instead: its IdP becomes the
bootstrap for `jmp login`, and cert-manager issues the certificate.

#### Transport requirements

A platform that also carries traffic must:

| ID | Requirement |
| --- | --- |
| **T1** | Forward TCP without terminating TLS, so the client certificate reaches the controller and routers intact. |
| **T2** | Make the controller and **every router endpoint** reachable, either through DNS that resolves the advertised names, or through local listeners mapped with `endpointOverrides`. |
| **T3** | Keep long-lived HTTP/2 streams open: no idle timeout below the gRPC keepalive interval, and no teardown of established connections when credentials renew. |

#### Lifetime requirements

| ID | Requirement |
| --- | --- |
| **L1** | Machine credentials renew without human interaction. |
| **L2** | For human users, either the platform's session lifetime is at least `leasePolicy.maxDuration`, or the platform supports re-authentication that `jmp` can trigger through the `exec` source's `interactive` mode. |

(integration-tiers)=

#### Integration tiers

Platforms fall into three groups:

- **Identity and transport in one product.** The platform issues the
  certificate and carries the traffic.
- **Transport that accepts SPIFFE identities.** The platform carries the
  traffic and authenticates callers with certificates from an external issuer
  such as SPIRE, so one certificate serves both layers.
- **Transport with user single sign-on.** The platform carries the traffic and
  signs users in with OIDC, but issues no certificates. Jumpstarter identity
  then comes from built-in enrollment, bootstrapped by the same IdP.

| Platform | Licensing | Group | Identity path | Transport |
| --- | --- | --- | --- | --- |
| **Teleport** (reference, commercial) | Commercial | Identity and transport | Native X.509-SVIDs (`tsh`, `tbot`) | TCP applications (VNet, `tbot` tunnels) |
| **OpenZiti + SPIRE** (reference, open source) | Apache 2.0; NetFoundry offers a commercial service | Transport that accepts SPIFFE identities | SPIRE X.509-SVIDs, trusted by OpenZiti as a third-party CA | OpenZiti overlay (tunnelers) |
| SPIRE (conformance target) | Apache 2.0, CNCF | Identity only | Native X.509-SVIDs (agent Workload API) | Any |
| cert-manager `csi-driver-spiffe` | Apache 2.0, CNCF | Identity only | Native X.509-SVIDs (pod volume) | Cluster network |
| Smallstep | Commercial; `step-ca` open source | Identity only | Device certificates via ACME `device-attest-01`, or as a cert-manager issuer behind built-in enrollment | None |
| Tailscale | Commercial | Transport with user SSO | `tsidp` as OIDC issuer, then built-in enrollment | Tailnet |
| NetBird | BSD-3 core; commercial cloud | Transport with user SSO | Its IdP as OIDC issuer, then built-in enrollment | NetBird mesh |
| HashiCorp Boundary | BSL; commercial HCP | Transport with user SSO | Corporate IdP, then built-in enrollment | TCP targets via workers |
| Pomerium | Apache 2.0 core; commercial | Transport with user SSO | Corporate IdP, then built-in enrollment | TCP over HTTP CONNECT |
| Cloudflare Zero Trust | Commercial SaaS | Transport with user SSO | Access IdP, then built-in enrollment | Cloudflare Tunnel and WARP |
| Twingate | Commercial | Transport with user SSO | Corporate IdP, then built-in enrollment | Connectors and clients |
| WireGuard or plain LAN | Open source | Transport only | Corporate IdP, then built-in enrollment | Direct |

Only the two reference integrations are fully worked in this JEP. The other rows
describe how each platform would fit; they become reference integrations when
someone contributes documentation and an e2e fixture, which does not need its
own JEP. No row requires platform-specific Jumpstarter code; that is a design
rule, not an observation (see [Platform neutrality](#platform-neutrality)).

### Reference integration: Teleport (commercial)

This integration shows a commercial platform that provides both identity and
transport. It is the deployment the authors run: a cluster with no public
Jumpstarter endpoint, users on Teleport Connect, and exporters on untrusted
networks reaching the cluster only through Teleport. Teleport is used here under
a commercial license; features such as the X.509 issuer override require
Teleport Enterprise.

> Configuration snippets in this section are illustrative and must be checked
> against the Teleport release in use. Items still to verify are listed at the
> end of the section.

#### Topology

```{mermaid}
flowchart LR
  subgraph Laptop
    U[jmp / Python client]
    TC["Teleport Connect<br/>(VNet + tsh)"]
  end
  subgraph Edge["Exporter host (untrusted network)"]
    EX[Exporter]
    TB["tbot<br/>(Workload API + app tunnels)"]
  end
  subgraph Cluster[Kubernetes cluster]
    TP["Teleport proxy + app service<br/>(Helm chart)"]
    CTRL[Controller]
    RTR[Routers]
  end
  U -- mTLS inside VNet --> TC
  TC -- outbound --> TP
  EX -- mTLS to localhost --> TB
  TB -- outbound --> TP
  TP -- TCP --> CTRL
  TP -- TCP --> RTR
```

Teleport is installed with its Helm chart in the same cluster as the
Jumpstarter controller. Its app service registers the controller and each
router as **TCP applications** pointing at their ClusterIP services, so
Jumpstarter needs no Ingress or Route. Two layers of mutual authentication
are in play: Teleport's outer layer (network access, audit) and Jumpstarter's
inner mTLS, which terminates in the controller and router pods.

```yaml
# Teleport app service (illustrative). Service names depend on the Jumpstarter
# custom resource: the operator names router services <name>-router-<index>
# (port 8083); the controller gRPC service listens on 8082.
app_service:
  enabled: true
  apps:
    - name: jumpstarter-controller
      uri: tcp://jumpstarter-grpc.jumpstarter-lab.svc.cluster.local:8082
      public_addr: grpc.jumpstarter.example.com
      labels: { app: jumpstarter }
    - name: jumpstarter-router-0
      uri: tcp://jumpstarter-router-0.jumpstarter-lab.svc.cluster.local:8083
      public_addr: router-0.jumpstarter.example.com
      labels: { app: jumpstarter }
```

The controller and router serving certificates must include each
`public_addr` as a SAN.

#### How Teleport meets the contract

| Requirement | Teleport mechanism |
| --- | --- |
| I1 | Workload Identity issues X.509-SVIDs. |
| I2 | The Workload Identity trust bundle is exported and published as a ConfigMap. With the **X.509 issuer override** (Teleport Enterprise 17.4.5 or later), SVIDs chain to the corporate root, so the cert-manager trust bundle already covers them. |
| I3 | Users: `tsh workload-identity issue-x509` through an `exec` source. Exporters: `tbot`'s Workload API service through the `spiffe` source. |
| I4 | `workload_identity` resources set the SPIFFE ID path, templated from the user's name and traits. |
| T1 | TCP applications pass TLS through untouched. |
| T2 | Users: VNet resolves each `public_addr`. Exporters: one `tbot` application tunnel per app, mapped with `endpointOverrides`. |
| T3 | gRPC keepalives hold connections open; `tbot` tunnel `idle_timeout` is left at its default of none. |
| L1 | `tbot` renews automatically (default 1-hour credentials renewed every 20 minutes; up to 24 hours). |
| L2 | User certificates are capped by `max_session_ttl`; set `leasePolicy.maxDuration` at or below it, and use `interactive: IfAvailable` so `tsh` can prompt for re-login. |

#### Identities

```yaml
# Teleport workload identity for users (illustrative)
kind: workload_identity
version: v1
metadata:
  name: jumpstarter-user
  labels: { app: jumpstarter }
spec:
  spiffe:
    id: /jumpstarter/users/{{ user.name }}/{{ user.traits.team }}
---
# Teleport workload identity for exporter bots (illustrative)
kind: workload_identity
version: v1
metadata:
  name: jumpstarter-exporter
  labels: { app: jumpstarter }
spec:
  spiffe:
    id: /jumpstarter/exporters/{{ user.bot_name }}
```

A `jumpstarter-user` role, granted to users through an IdP group, allows the
Jumpstarter TCP applications and these workload identities. Onboarding a team
member is then entirely an IdP group change, and offboarding stops issuance;
Teleport locks can cut access immediately.

The matching controller entry:

```yaml
authentication:
  x509:
    - trustDomain: teleport.example.com
      bundle:
        configMapRef: { name: jumpstarter-trust-teleport, key: bundle.pem }
      mappings:
        - principals: [Client]
          match: 'spiffe.segments[0] == "jumpstarter" && spiffe.segments[1] == "users"'
          username: '"teleport:" + spiffe.segments[2]'
          groups: 'spiffe.segments.size() > 3 ? ["team:" + spiffe.segments[3]] : []'
        - principals: [Exporter]
          match: 'spiffe.segments[0] == "jumpstarter" && spiffe.segments[1] == "exporters"'
```

#### Users

Users run Teleport Connect, which provides VNet and `tsh`. The client config
uses an `exec` source and never runs `jmp login`:

```yaml
credentials:
  type: exec
  exec:
    command: tsh
    args:
      - workload-identity
      - issue-x509
      - --name-selector=jumpstarter-user
      - --credential-ttl=1h
      - --output=${JMP_CREDENTIAL_DIR}
    format: spiffe-dir
    interactive: IfAvailable
```

`jmp` may ship this as a `teleport` preset.

#### Exporters

Exporters run `tbot`, not Teleport Connect, which is a desktop application.
`tbot` joins with the TPM join method where hardware has one, otherwise with a
bound keypair and a one-time registration secret:

```yaml
# tbot configuration (illustrative)
version: v2
proxy_server: teleport.example.com:443
onboarding:
  join_method: bound_keypair
  token: jumpstarter-exporters
storage:
  type: directory
  path: /var/lib/tbot
credential_ttl: 24h
renewal_interval: 20m
services:
  - type: workload-identity-api
    listen: unix:///run/spiffe/workload.sock
    selector:
      name: jumpstarter-exporter
  - type: application-tunnel
    app_name: jumpstarter-controller
    listen: tcp://127.0.0.1:8443
  - type: application-tunnel
    app_name: jumpstarter-router-0
    listen: tcp://127.0.0.1:8444
```

The exporter config is the one shown in
[Exporter experience](#exporter-experience), with `endpointOverrides` mapping
`grpc.jumpstarter.example.com:443` and each router endpoint to the matching
tunnel.

#### Bootstrap order on untrusted networks

When Teleport is the only path into the cluster, every step before `tbot` is
running must reach its own server:

1. FDO's rendezvous and owner-onboarding services must be publicly reachable.
2. Flight Control's agent API is designed for public exposure with mTLS. If
   policy requires it to sit behind Teleport too, FDO must deliver the `tbot`
   join material first, and the Flight Control agent then runs through a
   `tbot` tunnel.
3. Flight Control delivers the `tbot` Quadlet and configuration, the exporter
   Quadlet, and the exporter config.

(to-verify-teleport)=

#### To verify: Teleport

- Whether `workload_identity` SPIFFE ID templates accept user traits as shown.
- Whether `disconnect_expired_cert` applies to TCP application sessions.
- How VNet handles ports for single-port TCP applications, and whether
  multi-port applications are preferable for routers.
- An automated way to export the Workload Identity trust bundle when the
  issuer override is not used.
- Per-session MFA prompts on long-lived TCP application connections.

### Reference integration: OpenZiti and SPIRE (open source)

This integration shows a fully open-source stack where identity and transport
come from different projects. SPIRE issues the certificates; OpenZiti carries
the traffic and trusts SPIRE as a third-party CA, so the same certificate can
authenticate a caller to both the overlay and Jumpstarter. The scenario matches
the Teleport one: no public Jumpstarter endpoint, and exporters on untrusted
networks that only make outbound connections. NetFoundry offers OpenZiti as a
commercial hosted service for teams that don't want to run the overlay
themselves.

> Configuration snippets in this section are illustrative and must be checked
> against the OpenZiti and SPIRE releases in use. Items still to verify are
> listed at the end of the section.

#### Topology

```{mermaid}
flowchart LR
  subgraph Laptop
    U[jmp / Python client]
    ZDE["Ziti Desktop Edge<br/>(intercept)"]
  end
  subgraph Edge["Exporter host (untrusted network)"]
    EX[Exporter]
    SA[SPIRE agent]
    ZT["ziti-edge-tunnel<br/>(intercept)"]
  end
  subgraph Cluster[Kubernetes cluster]
    ER[Ziti edge router]
    ZC[Ziti controller]
    ZH["ziti-host pod<br/>(run-host)"]
    SS[SPIRE server]
    CTRL[Controller]
    RTR[Routers]
  end
  U -- mTLS, intercepted --> ZDE
  ZDE -- outbound --> ER
  EX -- mTLS, intercepted --> ZT
  ZT -- outbound --> ER
  EX -. Workload API .-> SA
  SA -. attestation .-> SS
  ER --- ZH
  ZH -- TCP --> CTRL
  ZH -- TCP --> RTR
```

OpenZiti's controller, edge router, and `ziti-host` charts and SPIRE's charts
are installed with Helm in the same cluster as Jumpstarter. The `ziti-host` pod
runs `ziti-edge-tunnel` in hosting mode and forwards each Jumpstarter service
to its ClusterIP service, without elevated permissions. Only the Ziti edge
router (and, in one bootstrap option, the SPIRE server) is reachable from
outside the cluster.

#### Services

Each Jumpstarter endpoint becomes an OpenZiti service. The intercept address
is the **advertised hostname**, so intercepting tunnelers capture the exact
names the controller hands out:

```bash
# Controller (repeat for each router, e.g. router-0.jumpstarter.example.com -> :8083)
ziti edge create config jmp-grpc-intercept intercept.v1 \
  '{"protocols":["tcp"],"addresses":["grpc.jumpstarter.example.com"],"portRanges":[{"low":443,"high":443}]}'
ziti edge create config jmp-grpc-host host.v1 \
  '{"protocol":"tcp","address":"jumpstarter-grpc.jumpstarter-lab.svc.cluster.local","port":8082}'
ziti edge create service jmp-grpc \
  --configs jmp-grpc-intercept,jmp-grpc-host --role-attributes jumpstarter

ziti edge create service-policy jmp-bind Bind \
  --service-roles '#jumpstarter' --identity-roles '#jumpstarter-host'
ziti edge create service-policy jmp-dial Dial \
  --service-roles '#jumpstarter' --identity-roles '#jumpstarter-users,#jumpstarter-exporters'
```

Because the intercepted names are the advertised names, callers with an
intercepting tunneler need no `endpointOverrides` and no DNS changes, and TLS
server name verification works unchanged.

#### How OpenZiti and SPIRE meet the contract

| Requirement | Mechanism |
| --- | --- |
| I1 | SPIRE issues X.509-SVIDs. |
| I2 | SPIRE publishes its trust bundle to a ConfigMap (for example with its Kubernetes bundle notifier plugin). |
| I3 | Exporters: the SPIRE agent's Workload API through the `spiffe` source. Users: built-in enrollment (see below). |
| I4 | SPIRE registration entries set the SPIFFE ID for each workload. |
| T1 | OpenZiti carries opaque TCP; Jumpstarter's TLS passes through untouched. |
| T2 | Intercept addresses match the advertised controller and router names. |
| T3 | gRPC keepalives hold connections open; overlay idle behavior is still to verify. |
| L1 | SPIRE rotates SVIDs automatically. |
| L2 | Users' transport session follows the IdP; their Jumpstarter certificate lifetime is `enrollment.clientCertificateDuration`, aligned with `leasePolicy.maxDuration`. |

#### Exporter identities

There are two options, depending on whether tunnelers can pick up a rotated
certificate without dropping connections (see
[To verify: OpenZiti](#to-verify-openziti)).

**Option A: one shared identity.** SPIRE is registered in OpenZiti as a
third-party CA that reads the SPIFFE ID from the URI SAN, and each exporter's
OpenZiti identity uses its SPIFFE ID as the external ID. The same SVID then
authenticates the exporter to the overlay and to Jumpstarter.

```bash
# Register SPIRE as a third-party CA, matching on the spiffe:// URI SAN
ziti edge create ca spire ./spire-bundle.pem \
  --location SAN_URI --matcher SCHEME --matcher-criteria spiffe --parser NONE --auth
ziti edge verify ca spire --cert ./verification.pem

# One OpenZiti identity per exporter, keyed by SPIFFE ID
ziti edge create identity pi-07 \
  --external-id spiffe://lab.example.com/jumpstarter/exporters/pi-07 \
  --role-attributes jumpstarter-exporters

# Matching SPIRE registration entry
spire-server entry create \
  -parentID <agent SPIFFE ID> \
  -spiffeID spiffe://lab.example.com/jumpstarter/exporters/pi-07 \
  -selector unix:user:jumpstarter
```

The exporter reads the SVID from the Workload API. The tunneler reads the same
SVID from files that `spiffe-helper` writes for its identity configuration.

**Option B: separate identities.** The exporter's OpenZiti identity is enrolled
natively with a one-time token delivered by host management, and SPIRE (or
built-in enrollment) provides only the Jumpstarter identity. This is the safe
default until tunneler certificate reload is confirmed: under Option A, a
tunneler that must restart for each rotated SVID would drop every tunneled
connection at each rotation, violating T3.

In both options the controller trusts the SPIRE trust domain:

```yaml
authentication:
  x509:
    - trustDomain: lab.example.com
      bundle:
        configMapRef: { name: spire-bundle, key: bundle.crt }
      mappings:
        - principals: [Exporter]
          match: 'spiffe.segments[0] == "jumpstarter" && spiffe.segments[1] == "exporters"'
```

and the exporter config needs no `endpointOverrides` when the tunneler runs in
intercept mode:

```yaml
credentials:
  type: spiffe
  spiffe:
    socket: unix:///run/spire/agent/public/api.sock
```

Intercept mode needs `CAP_NET_ADMIN` and `/dev/net/tun`. Where that is not
allowed, the tunneler's proxy mode binds local ports without privileges, and
`endpointOverrides` maps the advertised names to them, as in the Teleport
integration.

#### Users

Users run Ziti Desktop Edge (Windows, macOS) or `ziti-edge-tunnel` (Linux) in
intercept mode and sign in to OpenZiti with the corporate IdP through an
**external JWT signer** that matches a token claim (for example `email`) to
each identity's external ID. Their Jumpstarter identity comes from `jmp login`
against the same IdP followed by built-in enrollment. One IdP sign-in covers
both layers, although each user holds two credentials: an OpenZiti session and
a Jumpstarter certificate.

#### Bootstrap order on untrusted networks

1. **Option A:** the SPIRE agent must reach the SPIRE server before the overlay
   exists, so the SPIRE server's agent endpoint is exposed directly; it is
   designed for this, with node attestation and mTLS.
   **Option B:** the overlay comes up first from its enrollment token, and the
   SPIRE agent can then reach the SPIRE server through it.
2. FDO and Flight Control work as in the Teleport integration. Flight Control
   delivers the SPIRE agent configuration and join material (or the OpenZiti
   enrollment token for Option B), the tunneler and exporter Quadlets, and the
   exporter config.

(to-verify-openziti)=

#### To verify: OpenZiti

- Whether `ziti-edge-tunnel` and Ziti Desktop Edge reload a rotated identity
  certificate without dropping established connections. Option A depends on
  this.
- External JWT signer (OIDC) login support on each tunneler platform,
  particularly Linux.
- Idle timeouts and edge router failover behavior for long-lived intercepted
  TCP connections.
- Whether OpenZiti's automatic enrollment for third-party CAs can create the
  identity from the SPIFFE ID on first connection, removing the per-exporter
  `create identity` step.
- The SPIRE bundle publication mechanism and its ConfigMap key names.

### Comparing the reference integrations

| | Teleport (commercial) | OpenZiti + SPIRE (open source) |
| --- | --- | --- |
| Group | Identity and transport | Transport that accepts SPIFFE identities |
| Identity issuer | Teleport Workload Identity | SPIRE |
| User client | Teleport Connect (VNet) | Ziti Desktop Edge or `ziti-edge-tunnel` |
| User's Jumpstarter identity | SVID from `tsh` through `exec`; no `jmp login` | Built-in enrollment after OIDC `jmp login` |
| Exporter agents | `tbot` | SPIRE agent and `ziti-edge-tunnel` |
| Exporter credentials | `tbot`'s bot identity plus the workload SVID, from one agent | One SVID for both layers (Option A) or two identities (Option B) |
| Router reachability | VNet DNS for users; per-app tunnels and `endpointOverrides` for exporters | Intercept addresses match advertised names; no overrides |
| Trust bundle | Exported, or covered by the corporate root through the Enterprise issuer override | Published by SPIRE to a ConfigMap |
| User session cliff | Teleport `max_session_ttl` | IdP session for the overlay; enrollment certificate lifetime for Jumpstarter |
| Hosting | Helm chart in the cluster, or Teleport Cloud | Helm charts in the cluster, or NetFoundry's hosted service |

Both integrations use the same Jumpstarter code paths: `x509` trust domain
entries, the `spiffe`, `exec`, and `enrollment` credential sources, and
`endpointOverrides` where a transport only offers local listeners. Every
difference between them is configuration.

### Other reference integrations

These follow the same pattern and are documented in less detail. The
remaining rows of the [integration table](#integration-tiers) (Boundary,
Pomerium, Cloudflare Zero Trust, Twingate, Smallstep) are candidates for future
reference integrations.

- **SPIRE.** Register entries with SPIFFE IDs under `/jumpstarter/...`. Users
  and exporters use the `spiffe` source against the SPIRE agent socket. The
  trust bundle comes from the SPIRE server or its federation endpoint. SPIRE is
  the **conformance target** for the contract because it is a vendor-neutral
  CNCF reference implementation of SPIFFE.
- **cert-manager `csi-driver-spiffe`.** In-cluster callers (Tekton tasks, CI
  pods, in-cluster agents) mount a volume and use the `file` source. The trust
  domain is the cluster's.
- **Tailscale.** `jmp login` uses `tsidp` (Tailscale's OIDC provider) as its
  issuer, so login is silent for anyone already on the tailnet, followed by
  built-in enrollment. Exporters are tagged and receive an enrollment token
  from host management. Whether tailnet application capabilities can be
  projected into `tsidp` token claims, to become Jumpstarter groups, is to be
  verified.
- **NetBird.** `jmp login` uses the IdP behind NetBird, so one `groups` claim
  drives both NetBird access policies and Jumpstarter groups. Exporters
  receive a one-off setup key and an enrollment token from host management.
- **WireGuard or plain LAN.** Built-in enrollment with the corporate IdP, plus
  `jmp login --ca-sha256` for verified first contact.

None of these overlays can identify callers by source IP once traffic passes a
subnet router, routing peer, or proxy, which is why identity always comes from
the certificate.

### Host management

Host management is the third, independent axis. With Flight Control (Red Hat
Edge Manager) and FDO, FDO onboarding delivers the Flight Control enrollment
configuration, and Flight Control then delivers the identity agent's
configuration (or a Jumpstarter enrollment token), the exporter Quadlet, and
the exporter config. No step needs a per-device secret in Git or in the fleet
template, other than one-time bootstrap credentials consumed on first use.
Ansible or any GitOps tool can do the same; Jumpstarter only requires that the
exporter config is read-only to the exporter.

### API / Protocol Changes

| Surface | Change | Compatibility |
| --- | --- | --- |
| Controller config | `authentication.x509`, `authentication.bearer`, `enrollment`, `grpc.tls.clientAuth`, `leasePolicy.maxDuration` | Additive; absent means today's behavior. Read by the controller and the routers |
| Controller listener (`:8082`) | EST endpoints under `/.well-known/est/` | Additive |
| Login service | `/v1/auth/config` gains `enrollment` and `trustDomains` fields | Additive |
| Operator `Jumpstarter` CRD | Fields mirroring the controller config above; approver-policy and RBAC resources when enrollment is enabled | Additive |
| `ClientConfig` / `ExporterConfig` | `credentials`, `tls.clientCertificate` (for `file`), `endpointOverrides` | Additive; `token` still accepted |
| `jmp` CLI | `jmp login --ca-sha256`; `jmp admin create exporter --enrollment` (one-time token) | Additive |
| `Client` / `Exporter` / `Lease` CRDs | None required | n/a |
| gRPC `.proto` | None required | n/a |
| Router token | One token per side instead of one shared token; optional claim binding each to its holder's SPIFFE ID | Internal to controller and router |

### Hardware Considerations

- **Clock.** Short-lived certificates need correct time. Many SBCs (including
  Raspberry Pis) have no RTC and boot with a stale clock, which makes fresh
  certificates "not yet valid." Exporter hosts must have time synchronization
  before the exporter starts, and the exporter must report clock-related TLS
  failures explicitly.
- **Key storage.** File-based keys are the baseline. TPM-backed keys are
  preferred where hardware exists; Raspberry Pis need a TPM add-on board.
- **Rotation during hardware operations.** TLS authenticates at handshake time,
  so established connections survive certificate rotation. Only new
  connections use the new certificate. Rotation never interrupts an in-flight
  flash or power operation.
- **Offline exporters.** An exporter that is offline longer than its
  certificate lifetime cannot renew with `simplereenroll` and needs a fresh
  bootstrap credential (see Unresolved Questions). With an external identity
  agent, recovery follows that platform's re-attestation rules instead.
- **Data path.** Through an access proxy, client-to-exporter traffic may cross
  the proxy twice. Bulk artifacts should be pulled by the exporter from a
  registry rather than streamed from the client.

## Design Decisions

### DD-1: Credential type

**Alternatives considered:**

1. **X.509 certificates over mutual TLS** - The caller proves possession of a
   private key in the TLS handshake.
2. **JWT-SVIDs or other short-lived bearer tokens** - Already usable through
   the existing `jwt` authenticators.
3. **Identity asserted by the network or a proxy** - Overlay source addresses,
   Tailscale WhoIs, or headers injected by an application proxy.

**Decision:** X.509 certificates over mutual TLS.

**Rationale:** A bearer token of any lifetime can be replayed by whoever copies
it, which is the exporter problem this JEP starts from. Network position does
not survive subnet routers, routing peers, or tunnels, and works on one
overlay at a time. A key-bound credential survives every transport and is
useless when copied. JWT-SVIDs stay available through the `jwt` list.

### DD-2: How platforms integrate

**Alternatives considered:**

1. **A vendor-neutral contract** - SPIFFE X.509-SVIDs, a PEM trust bundle, and
   generic credential sources (`spiffe`, `exec`, `file`).
2. **Platform-specific authenticators** - For example a Teleport authenticator
   type in the controller.
3. **Embedding platform client libraries** - So that no sidecar or desktop
   client is needed.

**Decision:** A vendor-neutral contract.

**Rationale:** Each platform-specific path adds code, a test matrix, and
version coupling, and makes one vendor privileged. The two reference
integrations show that a commercial and an open-source stack with different
shapes are both covered by configuration alone.

### DD-3: Trust model

**Alternatives considered:**

1. **Per-trust-domain bundles** - A certificate is verified only against the
   bundle of the trust domain named in its SPIFFE ID.
2. **A flat client CA pool** - One pool for all issuers.

**Decision:** Per-trust-domain bundles.

**Rationale:** A flat pool lets any trusted issuer mint identities for any
other domain. Per-domain verification is the SPIFFE federation model and costs
one extra lookup.

### DD-4: Where client certificates are verified

**Alternatives considered:**

1. **In the authenticator** - The TLS layer requests a certificate
   (`RequestClientCert` / `RequireAnyClientCert`) and the authenticator
   verifies it.
2. **In the TLS handshake** - `VerifyClientCertIfGiven` /
   `RequireAndVerifyClientCert` against the union of all bundles, then
   re-verified per domain.
3. **At the ingress** - TLS terminated at the ingress, certificate forwarded
   in headers.

**Decision:** In the authenticator.

**Rationale:** Per-domain verification has to happen after the handshake in
any case (DD-3), so a handshake check adds nothing but a worse failure: the
caller sees a dropped connection, which the existing client and exporter retry
as an outage, instead of `UNAUTHENTICATED` with a reason. It would also stop a
caller with a stale certificate from falling back to a still-permitted bearer
token. Forwarded headers make identity depend on header hygiene at every hop
and do not work through TCP tunnels.

### DD-5: How certificates are issued without an external issuer

**Alternatives considered:**

1. **The controller as a registration authority** in front of a cert-manager
   issuer, constrained by approver-policy.
2. **The controller as a CA** holding a signing key.
3. **No built-in issuer** - Require SPIRE or another platform everywhere.

**Decision:** The controller as a registration authority.

**Rationale:** A signing key in the controller widens the blast radius of a
controller compromise, and the operator already integrates with cert-manager
for serving certificates. Requiring an external issuer everywhere would leave
LAN and tailnet labs, the most common deployments, without a path.

### DD-6: Enrollment protocol and where it is served

**Alternatives considered:**

1. **EST on the controller's gRPC listener** (`:8082`).
2. **EST on the login service** (`:8086`).
3. **A new gRPC enrollment service.**

**Decision:** EST on the controller's gRPC listener.

**Rationale:** EST is an existing standard with existing clients.
`simplereenroll` authenticates with the certificate being renewed, so it must
be served where TLS terminates in the pod. The login service is plain HTTP
behind edge termination; the gRPC listener is exposed with passthrough and
already serves non-gRPC HTTP requests. Reusing it adds no endpoint, Service,
Ingress, or Route. A gRPC service would work but offers nothing EST does not.

### DD-7: How an X.509 identity is resolved to an object

**Alternatives considered:**

1. **An explicit `name` (and optional `namespace`) in the mapping**, plus
   structural resolution for the enrollment trust domain.
2. **Reuse the OIDC rule** - Derive the object name from the mapped username.
3. **Trust the caller's `jumpstarter-name` metadata**, as for internal tokens.

**Decision:** An explicit mapping, plus structural resolution for the
enrollment trust domain.

**Rationale:** The OIDC rule strips everything before the first `:` and
sanitizes the rest, which turns a SPIFFE ID into an unusable name and rejects
the name in the caller's config. Trusting caller metadata is only safe for
internal tokens because their subject already names the object. Controller
issued SPIFFE IDs name the object too, so they are resolved from their path;
externally issued ones need the administrator to say which part is the name.

### DD-8: What router tokens are bound to

**Alternatives considered:**

1. **The holder's SPIFFE ID**, with one token per side.
2. **The holder's certificate thumbprint** (`cnf` / `x5t#S256`, RFC 8705).
3. **No binding** - Keep one shared bearer token per stream.

**Decision:** The holder's SPIFFE ID, with one token per side.

**Rationale:** Without binding, a leaked router token gives access to a live
hardware stream for up to 30 minutes. A thumbprint binding breaks on
rotation, because the exporter's token is minted against the certificate on
its long-lived `Listen` stream, not the one it will present to the router.
Either binding needs one token per side, since today both sides receive the
same string.

### DD-9: Migration

**Alternatives considered:**

1. **Additive, opt-in, per principal kind** - Bearer tokens keep working;
   operators narrow `bearer.allowedPrincipals` when ready.
2. **Switch on upgrade** - Require certificates after a given release.

**Decision:** Additive and opt-in.

**Rationale:** Exporters are deployed on hardware that is slow and sometimes
hard to reach. A flag day would strand them. Existing exporter tokens can
bootstrap enrollment, so a fleet can migrate without new secrets being
delivered.

## Design Details

### Server-side TLS

The controller already serves gRPC and the REST gateway on one TLS listener
(`:8082`, `NextProtos: [http/1.1, h2]`, gRPC via `ServeHTTP`). Routers use
`grpc.Creds` built from `LoadTLSCredentials` on `:8083`. Both load the serving
certificate once at startup today. Both gain:

- `ClientAuth` set from `grpc.tls.clientAuth`: `Disabled` (`NoClientCert`),
  `Optional` (`RequestClientCert`), or `Required` (`RequireAnyClientCert`);
- `ClientCAs` built from the union of all trust domain bundles, used only as
  the list of acceptable CA names sent to the client;
- dynamic reload of bundles and serving certificates.

The TLS layer requests a certificate but does not verify it. Verification
happens in the authenticator, which checks the presented chain **against the
bundle of the trust domain in the certificate's SPIFFE ID only**. This is how
the Kubernetes API server handles client certificates, for two reasons that
apply here as well:

- A flat pool would let an issuer trusted for one domain mint identities in
  another. Name constraints on intermediate CAs are recommended as defense in
  depth but are not relied upon.
- A certificate rejected during the handshake surfaces to the caller as a
  dropped connection (`UNAVAILABLE`), which the Python client and exporter
  treat as "controller unavailable" and retry indefinitely. Verifying in the
  authenticator returns `UNAUTHENTICATED` with a message. It also keeps a
  caller with an expired certificate and a valid bearer token working where
  bearer tokens are still allowed.

`Required` cannot be combined with built-in enrollment. A caller enrolling for
the first time has no certificate yet, and `simpleenroll` is served on the
same listener. The controller rejects that configuration at startup. With
enrollment, the TLS layer stays `Optional` and strictness comes from
`authentication.bearer.allowedPrincipals`, which decides what a caller without
a certificate may do. EST enrollment with a bearer token is always permitted
when enrollment is enabled, whatever `allowedPrincipals` says.

The controller's health, reflection, and REST gateway handlers share the gRPC
listener and are unauthenticated today; under `Required` they need a
certificate too. Kubernetes probes are unaffected because they use the
separate health port.

TLS must terminate at the controller and router pods. Ingress and Route objects
use TLS passthrough. Terminating TLS at an ingress and forwarding certificate
headers is not supported.

### Authentication chain

`BearerTokenAuthenticator` becomes one member of a request authenticator chain:

```{mermaid}
flowchart TD
  A[Incoming RPC] --> B{Client certificate\npresented?}
  B -- yes --> C[X.509 authenticator:\nper-domain verify, map SPIFFE ID]
  C -- invalid --> J3[Reject: UNAUTHENTICATED]
  B -- no --> D{authorization\nheader?}
  D -- yes --> E[Existing JWT authenticators\ninternal + OIDC]
  D -- no --> F[Unauthenticated]
  C --> G{Bearer token\nalso present?}
  G -- no --> H[Authenticated user info]
  G -- yes --> I{Same username?}
  I -- yes --> H
  I -- no --> J[Reject: ambiguous identity]
  E --> K{Bearer allowed for\nthis principal kind?}
  K -- yes --> H
  K -- no --> J2[Reject]
```

The peer certificate is read from the gRPC peer's TLS state; gRPC's
`ServeHTTP` transport populates it from the HTTP request's TLS state. Both
paths produce the same `authenticator.Response`. The chain implements the
existing `ContextAuthenticator` interface, so `ControllerService` and
`ClientService` pick it up through `auth.Auth` without changes at the call
sites.

Three places read the bearer token directly and need attention:

- `RotateToken` and the telemetry token path call `BearerTokenFromContext`
  themselves. A certificate-only exporter has no token to rotate, so its
  credential source must not call `RotateToken`, and the controller should
  answer `FAILED_PRECONDITION` rather than "missing authorization header" if
  it does.
- The router has its own `authenticate` function for router tokens and does
  not use `ContextAuthenticator`; see the next section.
- The legacy `authentication` ConfigMap key, kept for backward compatibility,
  does not gain `x509`.

The telemetry service is a third TLS listener. It continues to accept the
controller-minted telemetry tokens returned by `GetServiceEndpoints`; client
certificates for it are out of scope for this JEP.

`authentication.bearer.allowedPrincipals` (default `[Client, Exporter]`) lets
operators stop accepting bearer tokens for exporters once their fleet has
migrated, while clients can still use them.

### Router token binding

Router tokens remain controller-minted and short-lived (HS256 with the shared
`ROUTER_KEY`, 30 minutes). Today `Dial` mints **one** token per stream and
hands the same string to both sides: to the client in `DialResponse` and to
the exporter through its `Listen` stream. The router pairs the two connections
by the token's `sub`. A single shared token cannot be bound to two different
holders, so binding requires two changes:

1. `Dial` mints two tokens with the same `sub` (the stream) and different
   `jti`, one for each side.
2. When a side authenticated with a certificate, its token carries a claim
   naming that side's **SPIFFE ID**. The router, which now also requests and
   verifies client certificates using the same trust domain configuration,
   rejects a bound token presented over a connection whose verified SPIFFE ID
   differs. Unbound tokens (bearer-authenticated callers) are accepted as
   today.

The binding is to the SPIFFE ID, not to the certificate thumbprint
(`cnf: {"x5t#S256": …}` as in
[RFC 8705](https://www.rfc-editor.org/rfc/rfc8705)). A thumbprint would break
on rotation: the exporter's token is minted when a client dials, but the only
certificate the controller has seen from the exporter is the one on its
`Listen` stream, which may have been open for the whole lease. With
20-minute renewal an exporter will usually have rotated by then, and would
present a different certificate to the router. Binding to the identity
survives rotation and still makes a leaked router token useless to anyone who
cannot authenticate as that identity.

The router reads the same ConfigMap as the controller but today uses only its
`grpc` section; it gains the `authentication.x509` trust domains (not the
mappings, which it does not need). Per-side router tokens are also what the
proposed driver access rules need, so the two proposals share this change.

### Credential sources

Credential sources are implemented in the Rust core (the `rust-core-rewrite`
branch; not on `main` yet) and exposed through its bindings. Until the core
lands, the Python client implements `token`, `oidc`, `file`, `exec`, and
`enrollment`; `spiffe` may follow in Python if needed earlier.

| Source | Obtains | Renews | Notes |
| --- | --- | --- | --- |
| `token` | bearer token | controller rotation (exporters), OIDC refresh (clients) | Today's behavior |
| `oidc` | bearer token | refresh token | Today's `jmp login` |
| `enrollment` | certificate via EST | `simplereenroll` | Bootstraps with OIDC or a one-time token |
| `exec` | certificate (or token) from a command | re-runs the command before expiry | Any issuer CLI (for example `tsh`, `step`, Vault) |
| `spiffe` | X.509-SVID from the Workload API | streamed by the Workload API | Any Workload API agent (for example SPIRE agent, `tbot`) |
| `file` | certificate and key from disk | re-reads on change | For externally managed files |

Rotation semantics: the Rust core uses a dynamic client certificate resolver so
new connections pick up rotated certificates with no channel rebuild. The
Python client rebuilds its channel after rotation; existing streams continue.
The exporter already builds its channel through a factory that is called again
after a token rotation, which is the hook for this.

Python gRPC takes the client key as PEM bytes
(`grpc.ssl_channel_credentials(root_certificates, private_key,
certificate_chain)`), so the Python implementation supports file-based keys
only. Non-exportable keys (TPM, OS keystore) need the Rust core.

The same credentials are used for the controller and for routers: both the
client and the exporter dial the router with the TLS configuration of their
controller connection.

### Platform neutrality

These rules keep Jumpstarter independent of any identity or access platform:

- No authenticator, credential source, configuration field, or CRD field is
  named after, or behaves differently for, a specific platform.
- Platform knowledge lives only in documentation, `exec` presets (data that
  expands to a generic `exec` configuration), and e2e fixtures.
- A platform-specific code path needs its own JEP explaining why the contract
  cannot be met.
- The contract is tested against SPIRE in CI on every change. Reference
  integrations for other platforms run in an e2e matrix; a failing reference
  integration blocks a release only if it is marked as required.

### Failure modes

| Failure | Behavior |
| --- | --- |
| Certificate expired, not yet valid, or not issued by its trust domain | The handshake succeeds; the authenticator returns `UNAUTHENTICATED` with a message naming the cause and the validity window; client triggers renewal |
| No certificate under `clientAuth: Required` | TLS handshake fails; the caller sees a connection error, not a gRPC status |
| SPIFFE ID matches no mapping | `UNAUTHENTICATED`, logged with the SPIFFE ID |
| Mapping allowed, but no matching `Client`/`Exporter` | Unchanged: authorization denies, or provisioning creates a `Client` where enabled and allowed. Exporters are never provisioned |
| Caller-supplied name or namespace differs from the mapped one | `INVALID_ARGUMENT`, as for OIDC name mismatches today |
| `clientAuth: Required` with enrollment enabled | Controller refuses to start |
| Trust bundle ConfigMap missing | That trust domain is disabled and logged; other domains keep working |
| cert-manager unavailable | `simpleenroll` and `simplereenroll` return `503`; existing certificates keep working until expiry |
| Both certificate and bearer token present with different identities | Rejected |

### Security analysis

| Threat | Today | With this JEP |
| --- | --- | --- |
| Exporter config copied off a device | Full impersonation until token expiry | No credential in config; key may be non-exportable |
| Client token leaked (logs, shell history, CI) | Replayable from anywhere | Certificates require the private key; bound router tokens are useless to any other identity |
| Offboarding or decommissioning | Find and revoke tokens | Issuer stops issuing; access ends within TTL; deleting the `Client`/`Exporter` still denies every new RPC |
| Compromised controller | Can mint internal tokens | Same, plus enrollment limited by approver-policy to its trust domain, client auth, and capped TTL |
| Issuer for domain A mints domain B identities | n/a | Per-domain chain verification |
| First connection to a LAN controller | `tls.insecure` trust on first use | `--ca-sha256` pin, or bundle delivered by host management |
| Downgrade to bearer during migration | n/a | `bearer.allowedPrincipals` and `clientAuth: Required` close it per deployment |

There is no CRL or OCSP support. Revocation relies on short lifetimes, the
issuer refusing to renew, and deletion of the `Client` or `Exporter` object.
Authorization looks the object up at the start of every RPC, so deletion
denies new calls immediately. It does not tear down streams that are already
open (`Listen`, `Status`, an established router stream); those end with the
lease or the connection, as they do today.

## Test Plan

### Unit Tests

- SPIFFE ID parsing and validation (exactly one URI SAN, scheme, trust domain).
- Per-domain chain verification, including a certificate from domain A's CA
  claiming a domain B SPIFFE ID.
- CEL mapping: defaults, `principals` restriction, groups, first-match order.
- Authenticator chain precedence and the ambiguous-identity rejection.
- Object resolution: mapped `name` and `namespace`, mismatch with
  caller-supplied metadata, structural resolution for the enrollment domain,
  and that an X.509 username never takes the OIDC name-derivation path.
- Enrollment: CSRs with a wrong, missing, or extra name rejected, SPIFFE ID
  derived from the principal, `CertificateRequest` construction,
  re-enrollment for deleted principals.
- Configuration validation: `clientAuth: Required` with enrollment enabled is
  rejected.
- Router tokens: one per side, SPIFFE ID binding, mismatch rejection, and a
  bound token still accepted after the holder rotates its certificate.
- `endpointOverrides` resolution with preserved TLS server name.
- Credential sources: exec output parsing, file reload, renewal scheduling.

### Integration Tests

- The kind-based e2e suite (`e2e/`) with cert-manager, approver-policy, and
  trust-manager installed: enrollment end to end, bundle reload, CA rotation
  with overlapping roots. envtest runs no controllers, so it only covers the
  controller's side of `CertificateRequest` handling. `e2e/README.md` is
  updated in the same change.
- Mixed fleet: bearer-token and certificate clients and exporters on one
  controller, leasing each other.
- `clientAuth` in each mode; `bearer.allowedPrincipals` enforcement.
- **Contract conformance:** SPIRE in-cluster as the external issuer, covering
  I1 to I4, the `spiffe` and `exec` sources, and bundle rotation. Required on
  every change.
- **Reference integration matrix:** e2e jobs for each reference integration.
  OpenZiti + SPIRE runs in CI on every release, since both are open source.
  Teleport runs in an environment with a suitable Teleport license and is
  required for Beta. Other platforms are best effort.
- Old clients and exporters (bearer only) against a new controller with
  `clientAuth: Optional`.

### Hardware-in-the-Loop Tests

- Raspberry Pi exporter using the `spiffe` source, leased across several
  certificate rotations while streaming serial and power-cycling. Run twice:
  behind `tbot` application tunnels with `endpointOverrides` (Teleport), and
  behind an intercepting `ziti-edge-tunnel` with no overrides (OpenZiti +
  SPIRE).
- Raspberry Pi with no RTC booting without network time: confirm a clear error
  and recovery once time syncs.

### Manual Verification

- Teleport reference integration: a user with only Teleport Connect and the
  `teleport` preset leases an exporter with no `jmp login`.
- OpenZiti + SPIRE reference integration: a user signed in to Ziti Desktop
  Edge runs `jmp login` once and leases an exporter whose only network path is
  the overlay; under Option A, confirm SVID rotation does not drop the lease
  session.
- LAN profile: `jmp login --ca-sha256` with a deliberately wrong hash fails
  before any credential is sent.

## Acceptance Criteria

- [ ] A controller with one external trust domain accepts a certificate from
      that domain and rejects a certificate for the same SPIFFE ID signed by
      another configured domain's CA.
- [ ] An exporter using the `spiffe` source registers, is leased, and serves a
      session with no token in its config, and its config file is never
      written.
- [ ] A client using the `exec` source leases an exporter without running
      `jmp login`.
- [ ] `jmp login` against an enrollment-enabled controller stores a
      certificate, and renews it with `simplereenroll` before expiry.
- [ ] A CSR whose names differ from the caller's computed SPIFFE ID is
      rejected, and approver-policy denies a `CertificateRequest` outside the
      enrollment trust domain.
- [ ] A router token issued to one side is rejected when presented by a
      different identity, and still accepted after its holder rotates its
      certificate.
- [ ] With no `x509` entries and `clientAuth` unset, the existing e2e suite
      passes unchanged, and a bearer-only client and exporter from the
      previous release work against a controller with `clientAuth: Optional`.
- [ ] With `bearer.allowedPrincipals: [Client]`, an exporter presenting only a
      bearer token is rejected and a client is not.
- [ ] Rotating a trust domain's CA with overlapping roots drops no established
      session and needs no restart.
- [ ] The SPIRE conformance job and both reference integrations pass e2e.

## Graduation Criteria

### Experimental

- `authentication.x509`, `grpc.tls.clientAuth: Optional`, and the `file`,
  `exec`, and `spiffe` credential sources available behind configuration.
  Bearer tokens remain the default everywhere.
- Integration contract published, with SPIRE conformance tests passing.
- Feedback wanted: mapping ergonomics, the integration contract, the Teleport
  reference integration, and router endpoint overrides.

### Beta

- Enrollment endpoint and `enrollment` credential source available.
- New installations created by the operator enable enrollment, and `jmp login`
  enrolls by default when the controller offers it.
- `jmp admin create exporter` offers one-time enrollment tokens.
- Teleport (commercial) and OpenZiti + SPIRE (open source) reference
  integrations documented and passing e2e.

### Stable (default)

- New installations default to `bearer.allowedPrincipals: [Client]`, so
  exporters authenticate only with certificates.
- At least one release cycle in Beta with a production fleet migrated.
- Existing installations keep their configuration on upgrade; nothing is
  switched off automatically.

## Backward Compatibility

- **No behavior change on upgrade.** All new configuration is additive. With no
  `x509` entries and `clientAuth` unset (`Disabled`), the controller and routers
  behave exactly as today.
- **Bearer tokens are not removed.** OIDC and internal tokens remain supported
  for `v1alpha1`. Removing them for any principal kind is an explicit operator
  choice (`bearer.allowedPrincipals`) or would require a separate deprecation
  JEP.
- **Mixed fleets work.** Bearer and certificate callers coexist; leases,
  authorization, and router sessions do not depend on how a caller
  authenticated.
- **Wire compatibility.** No `.proto` changes. Old clients never send a
  certificate, which `Optional` accepts.
- **Existing exporter tokens bootstrap enrollment.** An exporter with a current
  internal token can call `simpleenroll` with it and switch to certificates
  without a new secret being delivered.
- **Downgrade.** The controller and routers parse their configuration
  strictly, so a binary older than this JEP fails to start on a ConfigMap that
  contains `x509`, `bearer`, `enrollment`, `grpc.tls`, or
  `leasePolicy.maxDuration`. Remove those keys before rolling back.
- **Router and controller must agree.** Per-side router tokens are minted by
  the controller and checked by the router. Unbound tokens are valid on both
  old and new routers, so the controller only adds the binding claim once
  `authentication.x509` is configured, and routers must be upgraded before
  that.
- **Operator upgrade path.** cert-manager stays optional, as it is today for
  serving certificates. The operator only creates approver-policy and
  trust-manager resources when enrollment is enabled, and reports a condition
  rather than failing when their CRDs are missing.

## Consequences

### Positive

- Exporter configs hold no secrets, are identical across a fleet, and are
  never written by the exporter.
- A copied config or a leaked token no longer grants access.
- Users and CI jobs reuse an identity they already have.
- Offboarding takes effect within the credential lifetime without hunting for
  tokens.
- Groups reach the controller, ready for policy work.
- `tls.insecure` has no remaining use case once a bundle travels with the
  identity.

### Negative

- More moving parts: trust bundles, an issuer, and (with enrollment)
  cert-manager and approver-policy with the default approver disabled.
- Certificate expiry becomes an operational concern: clocks on SBCs, mid-lease
  expiry for users, and offline exporters that miss renewal.
- TLS must terminate in the controller and router pods. Deployments that
  terminate gRPC TLS at an ingress cannot use certificate authentication.
- Two authentication paths have to be maintained and tested for as long as
  bearer tokens are supported.
- Object resolution gains a third rule set alongside internal tokens and OIDC.

### Risks

- The Rust core may not land on the expected schedule, leaving the Python
  client with file-based keys only and no `spiffe` source.
- Reference integrations depend on third-party behavior that is not yet
  verified (see the "To verify" lists).
- Misconfigured mappings can match more identities than intended. The
  `principals` restriction and the first-match rule limit the damage but do
  not remove it.

## Rejected Alternatives

- **Platform-specific authenticators** (for example a dedicated Teleport
  authenticator type in the controller). Every platform would add its own code
  path, test matrix, and version coupling. The integration contract gets the
  same result with configuration.
- **Trusting identity assertions injected by an application proxy** (for
  example Teleport's `Teleport-Jwt-Assertion` header on HTTP applications).
  Header formats are platform-specific, long-lived gRPC streams would have to
  survive an HTTP application proxy, and the result is still a bearer
  credential.
- **Embedding a platform's client library** (for example Teleport's Go client,
  or OpenZiti's SDKs) so no sidecar or desktop client is needed. Client
  libraries are often single-language, their protocols change across versions,
  and embedding one makes a single transport privileged. Even where the SDK is
  open source and multi-language, as with OpenZiti, this belongs behind a
  generic transport interface in a separate JEP (see Future Possibilities).
- **Identity from network position** (Tailscale WhoIs, overlay source IPs).
  Subnet routers, routing peers, and tunnels rewrite source addresses, and it
  only works on one overlay at a time.
- **JWT-SVIDs as the default.** Already usable through the existing `jwt`
  authenticators, but they remain bearer credentials. They are kept as an
  option, not the default.
- **The controller as a CA.** Holding a signing key in the controller widens
  the blast radius of a controller compromise. A registration authority in
  front of cert-manager keeps signing policy in cert-manager.
- **A flat client CA pool.** Simpler, but lets any trusted issuer impersonate
  identities from any other domain.
- **TLS termination at the ingress with forwarded certificate headers.** Makes
  identity depend on header hygiene at every proxy hop, and does not work
  through TCP application tunnels.
- **CRL/OCSP revocation.** Operationally heavy for edge devices on intermittent
  networks; short lifetimes plus object deletion cover the requirements.
- **A bespoke gRPC enrollment service.** Possible, but EST is an existing
  standard with existing client implementations and fits the HTTP login
  service.

## Prior Art

- **Kubernetes:** X.509 client certificate authentication, the CSR API, and
  structured JWT authentication configuration with CEL claim mappings, which
  this JEP mirrors for certificates.
- **SPIFFE/SPIRE:** SPIFFE IDs, X.509-SVIDs, the Workload API, and trust domain
  federation (per-domain bundles).
- **cert-manager:** issuers, approver-policy, trust-manager, and
  `csi-driver-spiffe`.
- **Istio and Linkerd:** mesh mTLS with SPIFFE identities and short-lived,
  automatically rotated certificates.
- **kubeadm:** CA public key pinning for first contact.
- **Kubernetes client-go credential plugins:** the model for the `exec`
  credential source, including its interactive mode.
- **EST (RFC 7030)** and **certificate-bound tokens (RFC 8705).**
- **Teleport Machine & Workload Identity:** `tbot`, X.509-SVIDs, bound keypair
  and TPM joining, and the X.509 issuer override.
- **OpenZiti:** third-party CAs whose certificates map to identities by an
  external ID taken from the SPIFFE URI SAN, so SPIRE SVIDs authenticate to the
  overlay directly. This is the model for using one certificate at both the
  transport and application layers.
- **Flight Control (Red Hat Edge Manager):** device enrollment by CSR with a
  bootstrap certificate, followed by device-specific management certificates.
- **LAVA:** token-based authentication only, with no workload identity model.

### Access platforms surveyed

| Platform | Issues workload certificates | Carries TCP without terminating TLS | Headless agent for machines | Notes for Jumpstarter |
| --- | --- | --- | --- | --- |
| Teleport | Yes (X.509-SVIDs) | Yes (TCP applications) | `tbot` | User certificates capped by `max_session_ttl` |
| OpenZiti | No; accepts SPIRE SVIDs as identities | Yes (overlay) | `ziti-edge-tunnel` | Intercepts by hostname, so advertised names work unchanged |
| HashiCorp Boundary | No | Yes (TCP targets) | No dedicated machine client | Sessions default to an 8-hour maximum, after which all connections close |
| Pomerium | No | Yes, encrypted end to end if the protocol is | Not documented | TCP over HTTP CONNECT via `pomerium-cli` |
| Cloudflare Zero Trust | No | Yes (Tunnel and WARP) | WARP with service tokens | Traffic arrives with the `cloudflared` host's source IP |
| Twingate | No | Yes | Headless client with service accounts | |
| StrongDM | Claims SPIFFE-compatible workload identity ("StrongDM ID"); format unconfirmed | Yes (raw TCP resources) | Client with service tokens | |
| AWS Verified Access, Google IAP | No | Yes (TCP; AWS since February 2025) | Connectivity client, `gcloud` tunnel | Cloud-specific |
| Smallstep | Yes (device certificates, ACME `device-attest-01`) | No | Smallstep Agent | Identity only; candidate attested issuer |

Boundary's session cap and Cloudflare's source-address rewriting show that the
lease-lifetime and source-IP concerns in this JEP are not specific to any one
platform.

## Unresolved Questions

1. **SPIFFE path layout.** Is
   `/jumpstarter/ns/<namespace>/{clients,exporters}/<name>` the right default for
   built-in enrollment, and should the namespace be part of the identity? The
   internal token subject also carries the object's UID, so a deleted and
   re-created object does not accept old tokens. A path without the UID lets a
   certificate issued to the old object act as the new one until it expires.
   Options: add the UID to the path, or record the issuing object's UID in the
   certificate and compare it.
2. **Offline exporter recovery.** Should `simplereenroll` accept a recently
   expired certificate within a grace window, or must an exporter that missed
   renewal always receive a new bootstrap credential?
3. **Bootstrap token lifecycle.** Should one-time enrollment tokens be a new
   field on `Exporter` status, a separate Secret, or reuse the existing exporter
   credential Secret?
4. **Key storage on clients.** File, OS keystore, or TPM by default for `jmp`
   on workstations?
5. **Contract publication.** Should the integration contract be versioned and
   published as a standalone document, so platforms and third parties can
   claim conformance? Where should `exec` presets live: in the Jumpstarter
   repository, or with each platform?
6. **Flight Control specifics to verify:** native FDO support, and whether
   Flight Control-issued device certificates could serve directly as an
   identity issuer.
7. **Router implementation target:** Go router on `main` or the Rust router on
   `rust-core-rewrite`, depending on which is authoritative when this ships.
8. **Default `leasePolicy.maxDuration`:** unset (today's behavior) or a
   conservative default for new installations?
9. **Relationship to host management proposals.** JEP-0017
   ([#1253](https://github.com/jumpstarter-dev/jumpstarter/pull/1253)) and
   [#1242](https://github.com/jumpstarter-dev/jumpstarter/issues/1242) cover
   Flight Control management of exporter hosts. Which of them owns delivery of
   the trust bundle and bootstrap credentials should be settled there.
10. **Reference integration verification.** Items specific to each reference
   integration are tracked in that integration's section
   ([To verify: Teleport](#to-verify-teleport),
   [To verify: OpenZiti](#to-verify-openziti)) and do not block acceptance of
   this JEP.

## Future Possibilities

These are not part of this JEP.

- **Exporter auto-registration:** create `Exporter` objects on first contact
  when a SPIFFE ID matches an administrator-defined pattern, building on the
  onboarding concept in the ExporterClass proposal (not yet published in this
  repository).
- **Group-based policy subjects:** let `ExporterAccessPolicy` and driver access
  rules match on mapped groups and SPIFFE ID prefixes in addition to `Client`
  labels.
- **Hardware-attested enrollment:** TPM attestation during enrollment, for
  example via ACME `device-attest-01`, so the controller can require
  hardware-bound keys for exporters.
- **End-to-end client-to-exporter mTLS** for deployments that do not need
  router-side driver access enforcement.
- **Credential health as a lease condition:** exporters report degraded
  credential renewal so the controller stops scheduling them.
- **Conformance kit:** a packaged test suite that any identity platform can run
  to demonstrate it meets the integration contract.
- **Pluggable transports in the Rust core:** a generic transport interface that
  lets an SDK-based dialer (for example OpenZiti's) replace a sidecar tunneler,
  so exporters on constrained hosts need one process instead of two.
- **Attested issuers:** Smallstep or `step-ca` with ACME `device-attest-01` as
  the cert-manager issuer behind built-in enrollment, for TPM-attested exporter
  certificates.

## Implementation History

- 2026-11-11: JEP drafted
- 2026-11-12: Refocused on a vendor-neutral integration contract; Teleport
  documented as the first reference integration, SPIRE as conformance target
- 2026-11-12: Added OpenZiti + SPIRE as the open-source reference integration,
  framed Teleport as the commercial one, and added the access platform survey
- 2026-11-12: Numbered JEP-0018 and reviewed against the code on `main`: EST
  moved to the controller listener, object resolution for X.509 identities
  specified, router tokens split per side and bound to the SPIFFE ID,
  certificate verification moved from the handshake to the authenticator, and
  the Design Decisions, Acceptance Criteria, and Consequences sections added

## References

- `controller/internal/authentication/bearer.go` (`BearerTokenAuthenticator`)
- `controller/internal/config/oidc.go` (`LoadAuthenticationConfiguration`)
- `controller/internal/config/types.go` (`Authentication`, `LeasePolicy`)
- `controller/internal/authorization/basic.go` (`BasicAuthorizer`)
- `controller/internal/authorization/metadata.go` (`MetadataAttributesGetter`, `normalizeOIDCUsername`)
- `controller/internal/config/config.go` (`LoadConfiguration`, `LoadRouterConfiguration`)
- `controller/internal/service/controller_service.go` (TLS listener, `Dial`, `Listen`, `RotateToken`)
- `controller/internal/service/router_service.go` (router token verification)
- `controller/internal/service/tls_credentials.go` (`LoadTLSCredentials`)
- `controller/deploy/operator/internal/controller/jumpstarter/endpoints/` (passthrough for gRPC, edge termination for login)
- `controller/internal/service/auth/exporter_credentials.go` (`RotateExporterCredential`)
- `controller/internal/service/login/service.go` (`/v1/auth/config`)
- `controller/api/v1alpha1/exporteraccesspolicy_types.go` (`maximumDuration`)
- `python/packages/jumpstarter/jumpstarter/config/client.py` (`ClientConfigV1Alpha1`)
- `python/packages/jumpstarter/jumpstarter/config/exporter.py` (`_persist_rotated_token`)
- `python/packages/jumpstarter/jumpstarter/common/grpc.py` (`ssl_channel_credentials`, insecure mode)
- [#1319](https://github.com/jumpstarter-dev/jumpstarter/pull/1319) and [#1320](https://github.com/jumpstarter-dev/jumpstarter/pull/1320): binding each kind of token to its own identity
- JEP-0017 ([#1253](https://github.com/jumpstarter-dev/jumpstarter/pull/1253)): Flight Control Host Management for jumpstarter-lab-config
- Driver Access Rules for ExporterAccessPolicy (proposal in preparation; not yet published in this repository)
- [SPIFFE: X.509-SVID specification](https://github.com/spiffe/spiffe/blob/main/standards/X509-SVID.md)
- [SPIFFE: Workload API specification](https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE_Workload_API.md)
- [SPIRE documentation](https://spiffe.io/docs/latest/spire-about/)
- [Kubernetes: client-go credential plugins](https://kubernetes.io/docs/reference/access-authn-authz/authentication/#client-go-credential-plugins)
- [RFC 7030: Enrollment over Secure Transport](https://www.rfc-editor.org/rfc/rfc7030)
- [RFC 8705: OAuth 2.0 Mutual-TLS Client Authentication and Certificate-Bound Access Tokens](https://www.rfc-editor.org/rfc/rfc8705)
- [cert-manager: approver-policy](https://cert-manager.io/docs/policy/approval/approver-policy/)
- [cert-manager: trust-manager](https://cert-manager.io/docs/trust/trust-manager/)
- [cert-manager: csi-driver-spiffe](https://cert-manager.io/docs/usage/csi-driver-spiffe/)
- [Kubernetes: Structured authentication configuration](https://kubernetes.io/docs/reference/access-authn-authz/authentication/#using-authentication-configuration)
- [Teleport: Workload Identity and tsh](https://goteleport.com/docs/machine-workload-identity/workload-identity/tsh/)
- [Teleport: X.509 issuer override](https://goteleport.com/docs/ver/17.x/reference/workload-identity/issuer-override/)
- [Teleport: Bound keypair joining](https://goteleport.com/docs/reference/machine-workload-identity/bound-keypair/concepts/)
- [Teleport: Machine & Workload Identity configuration](https://goteleport.com/docs/reference/machine-workload-identity/configuration/)
- [Teleport: VNet](https://goteleport.com/docs/connect-your-client/teleport-clients/vnet/)
- [OpenZiti: Using SPIRE as a trusted authority for OpenZiti identities](https://blog.openziti.io/using-spire-as-a-trusted-authority-for-openziti-identities)
- [OpenZiti: Helm charts](https://openziti.io/helm-charts/)
- [OpenZiti: ziti-host chart](https://openziti.io/helm-charts/charts/ziti-host)
- [OpenZiti: External JWT signers](https://netfoundry.io/docs/openziti/learn/core-concepts/security/authentication/external-jwt-signers)
- [HashiCorp Boundary: Targets](https://developer.hashicorp.com/boundary/docs/domain-model/targets)
- [Pomerium: TCP over HTTP support](https://www.pomerium.com/docs/capabilities/non-http/tcp)
- [Cloudflare: Connect private networks with cloudflared](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/private-net/cloudflared/)
- [Twingate: Service accounts](https://twingate.com/docs/service-accounts-guide)
- [StrongDM: TCP connections](https://docs.strongdm.com/admin/resources/servers/rawtcp)
- [StrongDM ID](https://factory.strongdm.ai/products/strongdm-id)
- [AWS Verified Access: non-HTTP(S) support](https://aws.amazon.com/about-aws/whats-new/2025/02/aws-verified-access-zero-trust-resources-non-https-protocols)
- [Smallstep: Core concepts](https://smallstep.com/docs/platform/core-concepts/)
- [Tailscale: Application capabilities](https://tailscale.com/docs/features/access-control/grants/grants-app-capabilities)
- [NetBird: Implementing Zero Trust](https://docs.netbird.io/use-cases/security/implement-zero-trust)
- [Red Hat Edge Manager: Device enrollment](https://docs.redhat.com/en/documentation/red_hat_edge_manager/1.1/html/architecture/rhem-enrollment)
- [RHEL for Edge: Onboarding with FDO](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/composing_installing_and_managing_rhel_for_edge_images/automatically-provisioning-and-onboarding-rhel-for-edge-devices-with-fdo-client-and-server)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
