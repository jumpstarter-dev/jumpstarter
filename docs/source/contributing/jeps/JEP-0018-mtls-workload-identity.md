# JEP-0018: mTLS Workload Identity as the Default Authentication Mechanism

| Field             | Value                                                                  |
| ----------------- | ---------------------------------------------------------------------- |
| **JEP**           | 0018                                                                   |
| **Title**         | mTLS Workload Identity as the Default Authentication Mechanism         |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                                              |
| **Status**        | Draft                                                                  |
| **Type**          | Standards Track                                                        |
| **Created**       | 2026-11-11                                                             |
| **Updated**       | 2026-11-13                                                             |
| **Discussion**    | https://github.com/jumpstarter-dev/jumpstarter/pull/1337               |
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
the same CEL mapping model the JWT authenticators already use (certificates
the controller itself issued need no mapping), and authorize it as today. Certificates come from pluggable **identity issuers**: any
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
For someone trying Jumpstarter on a laptop or a local cluster, the commands
stay the same, apart from a one-time pairing step in direct mode, and the
secure setup becomes the default one.

## Motivation

### Bearer tokens are the wrong credential for exporters

Exporters run on small hosts in labs, on benches, and increasingly on untrusted
networks. Today each one holds a long-lived controller-signed JWT (one year by
default, `authentication.internal.tokenLifetime`). Token rotation exists
(the `RotateToken` RPC, implemented by `RotateExporterCredential`, allowed in the second half of the token's life,
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
CI pods must be given a token or use a Kubernetes service account token, which
is still a bearer credential. There is no way to say "this caller already has a
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

New users meet this first. The distributed mode guide tells them to pass
`--insecure-tls` when creating every client and exporter, and the direct mode
guide starts the exporter with `--tls-grpc-insecure`, which disables TLS
altogether. The paths people learn on are the least protected ones.

### Groups are not available for authorization

Authorization today matches a single username against the object's internal
subject or its `spec.username`. Identity sources that carry team or role
information cannot pass it through, which limits what `ExporterAccessPolicy`
(which selects clients by label only) and the proposed driver access rules can
express.

### User Stories

- **As a** newcomer trying Jumpstarter with one board and a laptop, or a local
  cluster, **I want to** follow the getting-started guide without learning
  about certificates or passing insecure flags, **so that** my first setup is
  both easy and safe.
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
    # controller and treated as internal credentials (see "Identity mapping").
    - trustDomain: jumpstarter.lab.example.com
      bundle:
        configMapRef:
          name: jumpstarter-trust-internal
          key: bundle.pem

enrollment:
  enabled: true
  trustDomain: jumpstarter.lab.example.com
  issuerRef:                    # optional; omitted means an operator-managed CA
    group: cert-manager.io
    kind: ClusterIssuer
    name: jumpstarter-identity
  clientCertificateDuration: 8h
  exporterCertificateDuration: 24h
  renewalGracePeriod: 0s        # see "New users and small deployments"

grpc:
  keepalive: {}                 # unchanged
  tls:
    clientAuth: Optional        # Disabled | Optional | Required

leasePolicy:
  maxTags: 10                   # existing field
  maxDuration: 8h               # new global ceiling; see "Credential lifetime and leases"
```

`username` defaults to the full SPIFFE ID. For identities from an external
issuer, `Client.spec.username` and `Exporter.spec.username` hold the mapped
value, so the username comparison in `BasicAuthorizer` is unchanged. How the caller is resolved to an object *name*
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
| `/.well-known/est/csrattrs`                | bearer token or current client certificate                | the caller's SPIFFE ID    |
| `/.well-known/est/simpleenroll`            | bearer token (OIDC, internal, or one-time enrollment token) | first certificate      |
| `/.well-known/est/simplereenroll`          | current client certificate (mTLS)                         | renewed certificate       |

The flow:

1. The caller generates a key pair in its key store. The private key never
   leaves the caller, and on a machine with an OS keychain it never touches
   the disk in the clear (see [Key storage](#key-storage)).
2. The caller asks `csrattrs`, the EST endpoint through which a server tells
   a client what to put in its CSR, for its SPIFFE ID. Every EST request
   carries the caller's kind, namespace, and name as HTTP headers, the same
   `jumpstarter-kind`, `jumpstarter-namespace`, and `jumpstarter-name` values
   it sends as gRPC metadata. The handler copies those headers and
   `authorization` into the request context, so the existing `auth.Auth`
   resolves the caller to its `Client` or `Exporter` object with no new
   authorization code.
3. The caller builds a CSR whose only name is that URI SAN and sends it to
   `simpleenroll`. The controller resolves the caller again and computes the
   SPIFFE ID for the object itself:
   `spiffe://<trust-domain>/jumpstarter/<kind>/<namespace>/<name>/<uid>`,
   which is the object's internal subject
   (`exporter:<namespace>:<name>:<uid>`) written as a path. **The CSR is
   rejected unless it contains exactly that URI SAN and nothing else** (no
   DNS, IP, or email
   SANs, no other URIs). The controller cannot rewrite the request: a CSR is
   signed by the caller's key, and cert-manager issuers and approver-policy
   both act on its contents.
4. The controller creates a cert-manager `CertificateRequest` carrying the
   caller's CSR against the supplied or managed issuer, with the configured duration
   and client-auth usages, waits for issuance, and returns the chain.
5. Renewal uses `simplereenroll`, authenticated by the certificate being
   renewed. The controller re-checks that the `Client`/`Exporter` still exists
   before renewing.

The controller is a registration authority, not a CA. When
`enrollment.issuerRef` names an issuer the administrator supplies,
cert-manager's **approver-policy** constrains what the controller's service
account can request, so a compromised controller cannot mint serving
certificates, identities outside its trust domain, or long-lived credentials:

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
account when enrollment is enabled with a supplied issuer. With the
operator-managed issuer no policy is needed; see
[New users and small deployments](#new-users-and-small-deployments).
cert-manager is already an optional
operator integration (`spec.certManager`, used for serving certificates).
A supplied issuer additionally needs approver-policy, which in turn requires
cert-manager to run with its default approver disabled: left on, it approves
every request and the policy has no effect. That is a cluster-wide
cert-manager setting, so the operator checks for it and reports a condition
rather than changing it. Issuers outside the `cert-manager.io` group must
also be listed in approver-policy's `approveSignerNames`. Any cert-manager
issuer works as the backend: a CA issuer holding an intermediate signed by a corporate root, Vault,
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
    keyStore: auto            # best store the machine offers; see "Key storage"
    renewBefore: 33%          # renew when this share of the lifetime is left
```

`jmp login` on an enrollment-enabled controller runs the existing OIDC flow,
then immediately enrolls and stores the certificate instead of the bearer token:

```console
$ jmp login https://jumpstarter.lab.example.com --ca-sha256 3f9a…c1
Logged in as alice@example.com
Enrolled certificate spiffe://jumpstarter.lab.example.com/jumpstarter/client/lab-a/alice/6f1c… (expires in 8h)
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

### New users and small deployments

Most people meet Jumpstarter on one laptop and one board, or on a local
cluster created with `jmp admin create cluster`. Nothing above may make that
first hour harder. The rules this JEP holds itself to:

- **No new commands.** The local cluster needs no new flag either. Direct mode
  needs one, once: the pairing code on the first connection.
- **No new concepts.** A new user never has to read the words SPIFFE, trust
  domain, CEL, or approver-policy. Those appear only when an administrator
  chooses an external issuer.
- **Nothing to install.** The local cluster flow already installs
  cert-manager; nothing else is added to it.
- **Nothing to choose.** Where the private key is kept is decided by what the
  machine offers, not by the user.
- **A working setup keeps working.** No credential a new user holds expires in
  a way that needs a manual step, unless it has gone unused for longer than
  the grace period or its object was deleted.
- **Secure is the easy path.** Today the shortest documented command line is
  the insecure one in both setups. That is reversed.

#### Local cluster

What a new user types does not change:

```console
$ jmp admin create cluster
$ jmp admin create exporter demo --label board=demo --save
$ jmp run --exporter demo
$ jmp admin create client me --save --unsafe
$ jmp shell --client me --selector board=demo
```

What happens underneath does:

| | Today | With this JEP |
| --- | --- | --- |
| Cluster setup | Installs cert-manager and the operator; serving certificates come from an operator-managed self-signed CA | The same, plus enrollment enabled against a second operator-managed CA used only for identities. No approver-policy, trust-manager, OIDC provider, or login endpoint is needed |
| `jmp admin create ... --save` | Reads the token from the credential Secret and writes it into the config | Uses that token once to enroll, creates the key in the machine's key store (the OS keychain on a laptop), and writes a config with no secret in it |
| Config handed to another machine (`--out`) | Contains the one-year token | Contains a bootstrap credential; the first `jmp run` enrolls and keeps the key on that machine |
| Server trust | The config already carries the CA from the `jumpstarter-service-ca-cert` ConfigMap, but the setup guide still tells users to pass `--insecure-tls` | Unchanged mechanism; the guide drops `--insecure-tls` |
| Expiry | Token valid for a year, then `jmp admin rotate` by hand | Certificate renewed automatically in the background |
| A copied config file | Full access for up to a year | Nothing to use: no secret in it (`--save`), or a bootstrap credential that is void once the intended machine has enrolled (`--out`) |

Three parts of the design exist to make this work.

**A managed issuer.** `enrollment.issuerRef` is optional. When it is omitted,
the operator creates a CA `Issuer` in the Jumpstarter namespace, exactly as it
already does for serving certificates under `certManager.server.selfSigned`,
and publishes its certificate as the enrollment trust bundle. approver-policy
is not required in this mode. Its job is to stop a compromised controller
from obtaining certificates it should not have from a CA that other things
trust. A CA that exists only to sign Jumpstarter identities, in a namespace
that already holds the token signing key and the router key, gives a
compromised controller nothing it did not already have. approver-policy
remains required when `issuerRef` points at an issuer the administrator
supplies.

**Enrollment without the login service.** EST is served on the controller's
gRPC endpoint, which every config already contains, so a client or exporter
can enroll knowing only what it knows today. A local cluster needs no OIDC
provider and no login endpoint.

**Staying enrolled.** Short certificates are the wrong default for a laptop
that is closed over a weekend or an exporter that is unplugged for a month. A
bearer token never surprises a new user this way, so certificates must not
either:

- With the managed issuer, certificates default to 30 days and are renewed
  when two thirds of that is still left (`renewBefore: 67%`), so a machine
  that is used at least every few weeks never gets close to expiry. Deployments with an external identity
  provider choose shorter lifetimes deliberately.
- `enrollment.renewalGracePeriod` (default 90 days with the managed issuer,
  zero otherwise) lets `simplereenroll` accept a certificate that has expired,
  provided the `Client` or `Exporter` it names still exists. The caller still
  has to prove possession of the key, and deleting the object still ends
  access at once, so the grace period extends convenience, not what a stolen
  file can do.
- Past the grace period, the fix is the command the user already knows:
  `jmp admin create client me --save` again, or `jmp login` where there is an
  identity provider. The error says so.

`jmp describe client` already reports whether a client's token is still
valid; it reports the certificate's state and next renewal in the same place.

#### Direct mode

Direct mode has no controller, no cluster, and no CA. Today it offers three
choices, and the documented one is the first:

- `--tls-grpc-insecure`: no encryption and no authentication.
- `--passphrase`: a shared secret sent as request metadata on every call, in
  the clear unless TLS is also configured.
- `--tls-cert` and `--tls-key`: the user supplies a server certificate. The
  client has no option to trust a private CA, so a self-signed certificate
  cannot be verified.

This JEP adds **pairing**, which needs no flags once done and no certificate
handling from the user:

```console
# On the machine with the hardware
$ jmp run --exporter-config example-direct.yaml --tls-grpc-listener 0.0.0.0:19090
Listening on 0.0.0.0:19090
To connect from another machine, run this once:

    jmp shell --tls-grpc 192.168.1.20:19090 --pair K7QF-2MXD-9RTB-H4WN-6CPA

# On the laptop, the first time
$ jmp shell --tls-grpc 192.168.1.20:19090 --pair K7QF-2MXD-9RTB-H4WN-6CPA
Paired with example-direct

# On the laptop, every time after that
$ jmp shell --tls-grpc 192.168.1.20:19090
```

- On first start the exporter generates a key and a self-signed certificate
  and keeps them in its state directory. The user never sees them.
- The pairing code is short-lived and single-use, and is meant to be pasted,
  not typed. It carries a one-time secret and a fingerprint of the exporter's
  certificate, so the laptop verifies the exporter on the very first
  connection instead of trusting whatever answers.
- During pairing the laptop generates its own key and certificate and hands
  the certificate to the exporter, which records it as authorized. The laptop
  records the exporter's certificate against that address.
- Every later connection is mutual TLS between the two recorded
  certificates. There is no passphrase to retype, nothing secret in shell
  history, and nothing useful to someone who copies the command line.
- The exporter prints a code on start whenever nothing is paired yet.
  `jmp run ... --pair` prints a new one to add another laptop. Each paired
  laptop is recorded under its host name, or the name given with
  `jmp shell --pair <code> --pair-name <name>`, and `jmp run ... --unpair
  <name>` removes it.

This is the SSH model (a host key the client remembers, an authorized key the
server remembers) with a one-time code replacing the manual copying of keys.
It uses pinned certificates and no CA, so SPIFFE IDs and trust domains play no
part. `--tls-grpc-insecure`, `--passphrase`, `--tls-cert`, and `--tls-key`
keep working as they do today; the direct mode guide leads with pairing.

Local mode (`jmp shell --exporter`) uses a Unix socket on one machine and is
unaffected.

#### Growing out of the defaults

Each step up is one change, and none of them invalidates the step before:

1. **Local cluster with the managed issuer.** Nothing to configure.
2. **Add an identity provider.** Configure `authentication.jwt` as today;
   `jmp login` then enrolls users, and lifetimes can be shortened.
3. **Chain to a corporate root.** Set `enrollment.issuerRef` and install
   approver-policy.
4. **Accept an external issuer.** Add an `authentication.x509` entry and its
   mappings.

### Identity mapping

Mappings apply to trust domains with an external issuer. They are evaluated in
order; the first entry whose `match` is true wins, and a certificate that
matches no mapping is rejected. Certificates from the enrollment trust domain
are not mapped (see [Resolving the object](#resolving-the-object)). CEL
variables:

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

(resolving-the-object)=

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

[#1319](https://github.com/jumpstarter-dev/jumpstarter/pull/1319) and
[#1320](https://github.com/jumpstarter-dev/jumpstarter/pull/1320) establish
the pattern this JEP builds on. The authenticator that verified a credential
marks the user it returns with a Go type (`internalUser`, tested with
`authentication.IsInternal`), and the two later stages branch on that type
instead of on what the username looks like:

| Stage | Controller-signed token | External token |
| --- | --- | --- |
| `MetadataAttributesGetter` | Name taken from `jumpstarter-name` | Name derived from the username |
| `BasicAuthorizer.identifies` | Username must equal the object's internal subject | Username must equal `spec.username` |
| Provisioning | Never | A `Client`, when enabled |

Certificates slot into this without a third rule set in the authorizer.

**Certificates from the enrollment trust domain are internal credentials.**
The controller requested them, and their path is the object's internal
subject. The X.509 authenticator turns the path back into that subject, adds
the internal prefix, and returns an `internalUser`. From there the existing
internal-token rules apply unchanged: the caller names the object,
`identifies` compares the subject including the UID, and nothing is
provisioned. An object keeps working after it enrolls whether it was created
with an OIDC `spec.username` or with an internal token, and a certificate
issued to a deleted object does not authenticate as a re-created object of
the same name.

**Certificates from any other trust domain are external credentials**, with
one addition. The X.509 authenticator returns its own marked type carrying
the result of the mapping, and `MetadataAttributesGetter` gains one branch
for it:

- The object name is the mapping's `name` expression. It defaults to the last
  path segment of the SPIFFE ID and must evaluate to a valid object name. A
  caller-supplied `jumpstarter-name` must equal it.
- A mapping may also set `namespace`. When it does, a caller-supplied
  `jumpstarter-namespace` must equal it; when it does not, the caller's value
  is used, as for OIDC today.
- `jumpstarter-kind` must be one of the mapping's `principals`.

`BasicAuthorizer` needs no change for these: `identifies` already compares a
non-internal username with `spec.username`, and provisioning already creates
only `Client` objects and only for non-internal users.

Until #1319 and #1320 merge, `main` still recognises internal users by the
literal `internal:` username prefix and matches either kind of name through
`Usernames()`. This JEP should be implemented on top of them.

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
  A request or extension beyond it is rejected with `INVALID_ARGUMENT`. It is
  plumbed the way `maxTags` is: loaded into `LoadedConfig`, passed to
  `ClientService`, and checked in `CreateLease` and `UpdateLease` next to the
  tag validation, and in the legacy `ControllerService.RequestLease`. This
  differs from the per-policy `maximumDuration` in `ExporterAccessPolicy`,
  which is evaluated during exporter selection: a lease that asks for longer
  simply does not match that policy. Operators should set the ceiling at or
  below the session lifetime of their access platform.
- **Client preflight.** Credential sources report their expiry. Before creating
  or extending a lease, `jmp` compares the requested end time with the
  credential's renewable horizon and, depending on configuration, warns, clamps
  the duration, or triggers re-authentication first. This is a usability layer;
  the controller does not trust client-reported expiry.
- **Expiry events.** The client emits a "credential expires in N minutes"
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

Platforms that carry traffic fall into three groups. Platforms that only issue
identities ("Identity only" in the table) or only carry traffic ("Transport
only") combine with any of them:

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
          name: 'spiffe.segments[2]'
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

Checked against the Teleport documentation in November 2026: the
`tsh workload-identity issue-x509` flags and its three output files, which
match the `spiffe-dir` format; `user.name`, `user.bot_name`, and `user.traits`
as attributes available to SPIFFE ID templates; the `workload-identity-api`
and `application-tunnel` service types and the `selector.name` field; and
`bound_keypair` as a join method. Still open:

- The exact template syntax for reading one trait (`user.traits.team` as
  written above, or an indexed form).
- How the `bound_keypair` join options are spelled under `onboarding` in the
  `tbot` configuration file.
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
    socket: unix:///run/spire/agent/public/api.sock   # the agent's socket_path
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

Checked against the OpenZiti documentation in November 2026: the
`ziti edge create ca` flags for matching a `spiffe://` URI SAN and the
`--external-id` identity form, both as in OpenZiti's own SPIRE walkthrough;
and that a renewed certificate carrying the same claims keeps authenticating.
The CLI is being reorganised for OpenZiti 2.0, so the command spellings need
re-checking against the release in use. Still open:

- Whether `ziti-edge-tunnel` and Ziti Desktop Edge reload a rotated identity
  certificate without dropping established connections. The controller
  accepting the renewed certificate is confirmed; the tunnelers picking it up
  without a restart is not. Option A depends on this.
- External JWT signer (OIDC) login support on each tunneler platform,
  particularly Linux.
- Idle timeouts and edge router failover behavior for long-lived intercepted
  TCP connections.
- Whether OpenZiti's automatic enrollment for third-party CAs can create the
  identity from the SPIFFE ID on first connection, removing the per-exporter
  `create identity` step.
- The ConfigMap name and key for the SPIRE bundle. The `k8sbundle` notifier
  takes both from its configuration; `spire-bundle` and `bundle.crt` above
  are what the SPIRE Helm chart uses, not plugin defaults.
- The SPIRE agent socket path, which is set by the agent's `socket_path` and
  differs between the Helm chart and the quickstart manifests.

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

### Other integrations

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
| Login service | `/v1/auth/config` gains an `enrollment` block (EST URL and trust domain) | Additive |
| Operator `Jumpstarter` CRD | Fields mirroring the controller config above; a managed identity CA when enrollment is enabled without `issuerRef`; approver-policy and RBAC resources when it is enabled with one | Additive |
| `ClientConfig` / `ExporterConfig` | `credentials` (including `keyStore`, and the certificate and key paths of the `file` source), `endpointOverrides` | Additive; `token` still accepted |
| Client and exporter state | Keys are kept in a key store (OS keychain or a file in a state directory now; TPM and Secure Enclave with the Rust core), never in the config. Refresh tokens move to the same stores | New |
| Python dependencies | An OS secret storage library now; `grpcio>=1.80` for the signing callback when hardware stores arrive | The second raises the floor from 1.66.1 |
| Credential Secret | A bootstrap credential for enrollment (form open, see Unresolved Questions) | Additive |
| `RotateToken` | Returns `FAILED_PRECONDITION` to a caller that authenticated with a certificate | Callers with tokens unaffected |
| `jmp` CLI | `jmp login --ca-sha256`; `jmp admin create client` and `create exporter` enroll when the controller offers it; `jmp run --pair` / `--unpair` and `jmp shell --pair` for direct mode; `jmp describe client` reports certificate state | Additive; existing flags keep working |
| `Client` / `Exporter` / `Lease` CRDs | None required | n/a |
| gRPC `.proto` | None required | n/a |
| Router token | One token per side instead of one shared token; optional claim binding each to its holder's SPIFFE ID | Internal to controller and router |

### Hardware Considerations

- **Clock.** Short-lived certificates need correct time. Many SBCs (including
  Raspberry Pis) have no RTC and boot with a stale clock, which makes fresh
  certificates "not yet valid." Exporter hosts must have time synchronization
  before the exporter starts, and the exporter must report clock-related TLS
  failures explicitly.
- **Key storage.** An exporter's key is a file today. With the Rust core it is
  in a TPM when the host has one and the process may use it; Raspberry Pis
  need a TPM add-on board. See [Key storage](#key-storage).
- **Rotation during hardware operations.** A connection keeps the certificate
  it was opened with, and the controller checks it when each RPC starts.
  Streams that are already open are not re-checked, so rotation never
  interrupts an in-flight flash or power operation. Renewal happens well
  before expiry and new calls move to a connection with the new certificate,
  so a healthy client never starts an RPC with an expired one.
- **Offline exporters.** An exporter that is offline longer than its
  certificate lifetime plus the renewal grace period cannot renew with
  `simplereenroll` and needs a fresh bootstrap credential. With an external identity
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
   issuer, constrained by approver-policy when the issuer is one that other
   systems trust (see DD-9).
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

1. **Reuse the two existing kinds of credential** - Enrollment certificates
   are internal credentials whose path is the internal subject; certificates
   from other domains are external credentials whose mapping supplies an
   explicit `name` (and optional `namespace`).
2. **Reuse the OIDC rule for everything** - Derive the object name from the
   mapped username.
3. **A third rule set in the authorizer** for certificates.

**Decision:** Reuse the two existing kinds of credential.

**Rationale:** The OIDC rule strips everything before the first `:` and
sanitizes the rest, which turns a SPIFFE ID into an unusable name and rejects
the name in the caller's config; externally issued identities need the
administrator to say which part is the name. Enrollment certificates are
issued by the controller for an object it has already resolved, exactly like
an internal token, so giving them the internal subject (UID included) reuses
the rules from #1319 and #1320 as they are and leaves `BasicAuthorizer`
untouched. A third rule set would duplicate both.

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

### DD-9: Default issuer for built-in enrollment

**Alternatives considered:**

1. **An operator-managed CA when no issuer is given**, with approver-policy
   required only for a supplied issuer.
2. **Always require a supplied issuer and approver-policy.**
3. **Leave enrollment off by default** and keep bearer tokens for local
   clusters.

**Decision:** An operator-managed CA when no issuer is given.

**Rationale:** Requiring approver-policy means changing a cluster-wide
cert-manager setting before the first exporter can connect, which no newcomer
should face. The policy protects a CA that other systems trust; a CA that
signs nothing but Jumpstarter identities needs no such protection. Leaving
enrollment off would make certificates an advanced feature and keep new users
on the credential this JEP is trying to retire.

### DD-10: Authentication in direct mode

**Alternatives considered:**

1. **Pairing with pinned certificates** - A one-time code, then mutual TLS
   between remembered certificates.
2. **Keep the passphrase, add server pinning** - `--passphrase` plus a
   fingerprint flag on the client.
3. **A local CA** - `jmp` creates a CA and issues certificates to both sides.
4. **Leave direct mode as it is.**

**Decision:** Pairing with pinned certificates.

**Rationale:** Direct mode is the first thing many users run and today its
easy path has no encryption. A passphrase must be retyped or stored, ends up
in shell history, and is a bearer secret. A local CA is a new thing to create,
protect, and copy between machines, for a setup with two parties. Pairing
costs the user one pasted command, once, and after that the secure command
line is the shortest one.

### DD-11: Where private keys are kept

**Alternatives considered:**

1. **A key store interface now, with file and OS stores in Python and
   hardware stores added by the Rust core.**
2. **Key files only**, relying on file permissions and disk encryption.
3. **Hardware stores in Python**, through per-platform bindings and a signed
   macOS helper.
4. **Wait for the Rust core** for anything beyond files.
5. **Two keys** - A hardware identity key that only signs renewal requests,
   and a short-lived software key for TLS.

**Decision:** A key store interface now, with file and OS stores in Python and
hardware stores added by the Rust core.

**Rationale:** Files leave the key readable by anyone holding the disk, which
is the leak this JEP exists to close, and the OS keychain closes it today with
no native code. Waiting for the Rust core for that much would leave every
laptop exposed in the meantime. Hardware stores are different: they need
native signing APIs, platform packaging, and on macOS a signed binary, all of
which the Rust core provides once and Python would have to assemble per
platform. Fixing the interface now means the later step changes where a key
lives and nothing else. gRPC's signing callback is what lets the core's keys
serve the Python client's connections, and it removes the need for a two-key
scheme, which would have added a second enrollment protocol.

### DD-12: Migration

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
  C -- invalid --> D
  B -- no --> D{authorization\nheader?}
  D -- yes --> E[Existing JWT authenticators\ninternal + OIDC]
  D -- no --> F[Unauthenticated]
  C -- valid --> G{Bearer token\nalso present?}
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

Several places read the bearer token directly, past the authenticator, and
fail today for a caller that has only a certificate:

- `Register` calls `recordExporterCredential`, which hashes the bearer token
  onto the `Exporter` and returns an error when there is none. A
  certificate-only exporter cannot register until this records the
  certificate instead, or is skipped for certificate callers.
- `GetServiceEndpoints` calls `telemetryToken`, which reads the bearer token
  to cap the telemetry token's lifetime and fails without one. With
  telemetry enabled, a certificate-only client or exporter gets an error
  from endpoint discovery. The cap has to come from the certificate's
  `notAfter`.
- `RotateToken` reads the token to rotate it. A certificate-only exporter has
  no token to rotate, so its credential source must not call `RotateToken`,
  and the controller should answer `FAILED_PRECONDITION` rather than
  "missing authorization header" if it does.
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
short renewal intervals an exporter will usually have rotated by then, and would
present a different certificate to the router. Binding to the identity
survives rotation and still makes a leaked router token useless to anyone who
cannot authenticate as that identity.

The controller needs the exporter's SPIFFE ID when a client dials. It is
already in the right place: `Dial` hands the token to the exporter through an
in-memory queue owned by that exporter's `Listen` call, on the same replica,
so the identity that opened the `Listen` stream is recorded on that queue
entry and read when the token is minted.

The router reads the same ConfigMap as the controller but today uses only its
`grpc` section; it gains the `authentication.x509` trust domains (not the
mappings, which it does not need). Per-side router tokens are also what the
proposed driver access rules need, so the two proposals share this change.

### Credential sources

Credential sources are implemented in the Python client and exporter on
`main`, which is what ships today. The work is contained:

- Client, exporter, and router connections all build their TLS credentials in
  one function, `ssl_channel_credentials(target, tls_config)`. A credential
  source hands it a key and chain; nothing else in the connection code needs
  to know which source is in use.
- `call_credentials` always attaches a bearer token next to the
  `jumpstarter-kind`, `jumpstarter-namespace`, and `jumpstarter-name`
  metadata. It gains a variant that sends the metadata alone.
- `cryptography`, already a dependency of the core package, covers key
  generation and CSRs for `enrollment`.
- `spiffe` needs a Workload API client. py-spiffe (the `spiffe` package on
  PyPI) provides one, including a source that follows rotations; the
  alternative is to call the Workload API directly, since it is a small gRPC
  service over a Unix socket and `grpcio` is already in use.

The Rust core on the `rust-core-rewrite` branch describes itself as a
wire-compatible peer of the Go controller and router, with the Python and Go
sources as the source of truth. It implements the same sources when it lands;
nothing in this JEP waits for it.

| Source | Obtains | Renews | Notes |
| --- | --- | --- | --- |
| `token` | bearer token | controller rotation (`RotateToken`, clients and exporters) | Today's behavior |
| `oidc` | bearer token | refresh token | Today's `jmp login` |
| `enrollment` | certificate via EST | `simplereenroll` | Bootstraps with OIDC or a one-time token |
| `exec` | certificate from a command | re-runs the command before expiry | Any issuer CLI (for example `tsh`, `step`, Vault) |
| `spiffe` | X.509-SVID from the Workload API | streamed by the Workload API | Any Workload API agent (for example SPIRE agent, `tbot`) |
| `file` | certificate and key from disk | re-reads on change | For externally managed files |

Rotation semantics: Python gRPC fixes the client certificate when a channel is
created, so the client rebuilds its channel after rotation; existing streams
continue. The exporter already builds its channel through a factory that is
called again after a token rotation, which is the hook for this. The Rust core
can use a dynamic certificate resolver and skip the rebuild.

The Python client loads the key from its key store when it builds a channel.
Once the Rust core provides hardware stores, the channel is built with a
signing callback and the key stays where it is (see
[Key storage](#key-storage)).

The same credentials are used for the controller and for routers: both the
client and the exporter dial the router with the TLS configuration of their
controller connection.

### Key storage

A certificate is only as hard to steal as its private key. A key in a plain
file can be read by anyone who gets at the disk: a lost laptop without disk
encryption, a backup, a synced dotfiles folder, another account on a shared
bench machine. The goal is that holding the device is not enough; an attacker
must also be able to unlock the store the key lives in.

#### What Python gRPC allows

This was the open constraint in earlier revisions of this JEP, which assumed
the Python client had to hand gRPC the key as PEM bytes and so could only use
keys it could read. That is no longer true. Since grpcio 1.80, a channel can
be built with a **signing callback** in place of the key
(`grpc.experimental.ssl_channel_credentials_with_custom_signer`): gRPC passes
the callback the bytes to sign during the handshake, and never sees the key.
Checked against the version the project currently resolves (1.83), this
completes a mutual TLS handshake on both the blocking and the `grpc.aio`
channel, with the callback answering directly or from another thread.

So the transport is not the obstacle: a key that cannot be exported can be
the TLS key itself. Two caveats: the API is marked experimental, to be folded
into `grpc.ssl_channel_credentials`, and it requires raising the project's
floor from `grpcio>=1.66.1` to `>=1.80`.

What Python lacks is a dependable way to reach the signing facilities
themselves. The Secure Enclave, the TPM, and CNG are native APIs with
platform rules attached (code signing on macOS, group membership and the TPM2
stack on Linux), and driving them from an interpreter means a different
binding and packaging problem on every platform. The Rust core is the right
home for that: it calls those APIs directly, ships as a binary that can be
signed, and already has bindings to Python. The work is therefore split:

- **Now, in Python:** the key store interface, the `file` store, and the OS
  stores used as encrypted storage. The key is kept in the keychain, unlocked
  with the user's login, and loaded into the process when a connection is
  made. This is at-rest protection, which is what a lost or copied disk
  needs, and it uses nothing but the operating system's secret storage.
- **With the Rust core:** the hardware stores, and OS stores holding keys
  that are never loaded into the process. The core signs and the client
  reaches it through the signing callback, or the core makes the connection
  itself. Nothing in the config or the enrollment protocol changes when this
  arrives; a client enrolls again and its new key is created in the better
  store.

#### One interface, three levels of protection

Every private key Jumpstarter creates on a client or exporter is created
inside a **key store** and used through three operations: generate, return
the public key, sign. Nothing above the key store ever asks for the key
itself. Keys are ECDSA P-256, the one algorithm every store below supports.

| Level | Store | Where the key is | Survives | Available |
| --- | --- | --- | --- | --- |
| Hardware | `tpm` (Linux and Windows), `secure-enclave` (macOS), `pkcs11` (security keys, HSMs) | Generated in the device and never leaves it | Theft of the device or disk; malware running as the user cannot copy the key, only use it while it runs | With the Rust core |
| OS store | `keychain` (macOS login keychain), `windows` (Credential Manager), `secret-service` (GNOME Keyring, KWallet) | Encrypted by the operating system, unlocked when the user logs in; in memory while a connection is made | Theft of a locked or powered-off device, disk copies, backups, other local users | Now |
| File | `file` | A `0600` file in the state directory | Other local users only; rely on disk encryption for the rest | Now |

```yaml
credentials:
  type: enrollment
  enrollment:
    keyStore: auto      # auto | tpm | secure-enclave | pkcs11 | keychain | windows | secret-service | file
```

`auto` is the default and picks the highest level that works on the machine
without asking the user anything:

| Platform | `auto` picks now | `auto` picks with the Rust core |
| --- | --- | --- |
| macOS | `keychain` | `secure-enclave` |
| Windows | `windows` | `tpm` when the machine has one |
| Linux desktop | `secret-service` | `tpm` when the user may use it |
| Linux headless (exporters, CI) | `file` | `tpm` when the process may use it |

The rules that keep this predictable:

- A new user never chooses. The getting-started commands produce an
  OS-protected key on a laptop with no extra step.
- A store is chosen when the key is created and never changed silently. If
  the store later becomes unavailable, the client says so and offers to enroll
  again; it does not fall back to a file.
- `file` is never chosen on a machine that has something better, and when it
  is chosen `jmp` says so once. `jmp describe client` shows which store holds
  the key.
- No store asks for a fingerprint or PIN on each use by default. A key is
  used once per connection, and a prompt on every `jmp` command would push
  people back to tokens. Presence checks are an opt-in.

The same key stores hold the other secrets a client keeps today in its config
file in plain text: the OIDC refresh token and, until it is used, the
bootstrap credential. The OS stores take these as they are; on a machine with
only hardware and file stores they stay in the file store.

#### Per platform

- **macOS.** The login keychain works from any process and is the baseline.
  The Secure Enclave is harder: Apple only lets a program create
  enclave-protected keys if it is signed with an application identifier and a
  provisioning profile, which a Python interpreter installed with `pip` or
  `uv` is not. The Rust core, shipped as a signed binary, can be.
- **Windows.** The Credential Manager is the baseline. CNG offers a
  TPM-backed provider for the Rust core to use.
- **Linux.** The TPM is reached through the TPM2 software stack or its PKCS#11
  module and usually needs the user to be in a group. Desktop sessions have
  the Secret Service. Headless machines have neither a login session nor,
  often, a TPM: a Raspberry Pi needs an add-on board. For those, the file
  store remains, and the exporter's state directory can be supplied as a
  systemd encrypted credential, which ties the file to that machine.
- **PKCS#11** covers security keys and HSMs on every platform, and is how the
  hardware level will be tested in CI, against a software token.

#### What this does not do

- It does not stop someone who can unlock the store. A stolen laptop that is
  logged in and unlocked can use the key for as long as the certificate
  renews. Deleting the `Client` ends that.
- The controller cannot tell where a key lives, so it cannot require a
  hardware key. That needs attestation at enrollment, listed under Future
  Possibilities.
- It does not cover keys that an external issuer hands over. A SPIFFE Workload
  API agent returns the private key to the workload, and an `exec` command
  writes it to the credential directory; both are held in memory or in that
  private directory for their short lifetime. Protecting those is the
  issuing platform's job (for example an agent that itself joined with a
  TPM).
- A direct mode exporter serves TLS with a key Python must load, so its own
  key is in the OS or file store. The laptop's pairing key follows the same
  stores as any other client key.

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

### Direct mode pairing

Pairing has to work within what Python gRPC offers, since direct mode is
served by the Python exporter:

- A gRPC server in Python either requires and verifies a client certificate
  or does not ask for one (`require_client_auth`). There is no mode that
  accepts a certificate and leaves the decision to the application, so one
  listener cannot serve paired clients over mutual TLS and also accept a
  client that is not paired yet without a certificate.
- The set of trusted client roots can be replaced while the server runs
  (`grpc.dynamic_ssl_server_credentials`, consulted on every new connection).

The listener therefore always requires a client certificate, and the pairing
code is what makes the first connection possible:

1. When it prints a code, the exporter derives a temporary CA key from the
   code's one-time secret and adds that CA to its trusted roots for the
   lifetime of the code.
2. The laptop derives the same CA key from the code, generates its own
   long-term key, and issues itself a certificate signed by the temporary CA.
   It checks the exporter's certificate against the fingerprint in the code
   before sending anything.
3. Over that connection the laptop calls a pairing RPC that hands over its
   long-term self-signed certificate. The exporter adds it to its trusted
   roots and its authorized list, removes the temporary CA, and voids the
   code.
4. The laptop stores the exporter's certificate for that address. Later
   connections present the long-term certificate and need no code.

The client side reuses code that exists: `ssl_channel_credentials` already
fetches a server's chain before building credentials. In pairing mode it
compares that chain with the stored fingerprint and then uses it as the only
trusted root, matching the certificate by fingerprint, not by host name, so
the same exporter can be reached by IP address or by any name.

The length of the code, the key derivation, and the lifetime of a code decide
how hard it is to guess a code or forge a matching fingerprint while the code
is valid. They are to be fixed, with a security review, before this part
leaves Experimental (see Unresolved Questions).

### Interface coverage

Every connection Jumpstarter makes, what protects it today, and where this
JEP stands on it. "Blocker" means the design as written cannot be implemented
or does not work until the item is resolved; each is expanded below.

| Connection | Today | With this JEP | Status |
| --- | --- | --- | --- |
| Client to controller (`ClientService`, `ControllerService`) | Server TLS, bearer token | Mutual TLS | Covered |
| Exporter to controller | Server TLS, bearer token | Mutual TLS | **Blocker B1, B2**: `Register` and endpoint discovery require a bearer token |
| Client or exporter to router | Server TLS, shared router token | Mutual TLS, per-side token bound to the SPIFFE ID | Covered; router must load trust domains |
| Client to exporter session, inside the router stream | No authentication of its own; the router sees the bytes | Unchanged; only the holder of a bound token can join | Covered as far as this JEP goes; end-to-end protection is future work |
| Client or exporter to telemetry | Server TLS, controller-minted telemetry token | Unchanged | Depends on B2 |
| Login service (`/v1/auth/config`) | Edge TLS, no authentication | Unchanged | Covered |
| Enrollment (EST on `:8082`) | n/a | Bearer token or current certificate | New; see B8 |
| Direct mode, client to exporter | None, a passphrase, or a user-supplied server certificate | Pairing, pinned certificates | New; code format needs review |
| Exporter to exporter, direct (JEP-0015 peer listener) | n/a | Mutual TLS between exporter identities | **Blocker B3 to B6** |
| Exporter to exporter, through the router (JEP-0015 `DialPeer`) | n/a | Mutual TLS to the router, bound tokens | **Blocker B7** |
| Virtual exporter Pods (`ExporterSet`) to controller | Token written into a config Secret by the reconciler | Enroll at start from that Secret | Gap B9 |
| `jmp admin` to the Kubernetes API | kubeconfig | Unchanged | Not in scope |
| Local mode and hooks (Unix socket) | Local only | Unchanged | Not in scope |

#### Blockers in the code on `main`

- **B1. `Register` needs a bearer token.** See
  [Authentication chain](#authentication-chain). Small change, but nothing
  works for a certificate-only exporter until it is made.
- **B2. Telemetry token issuance needs a bearer token.** Same section. It
  affects clients too, and it fails closed: the caller gets an error from
  `GetServiceEndpoints`, not a response without telemetry.

Both are contained fixes. They are listed as blockers because they would
only be found when the first certificate-only exporter is started.

#### Blockers shared with JEP-0015

JEP-0015 (multi-exporter leases) adds the first connections in which neither
end is the controller: exporters dialing each other to carry a lease's
forwards. Its DD-25 gives exporters SPIFFE certificates for that path and
says a later JEP on mTLS should reuse them. This is that JEP, and the two
do not yet fit together.

- **B3. Two ways to issue the same identity.** JEP-0015 has the controller
  hold an identity CA and sign through a new `SignExporterCertificate` RPC,
  with IDs of the form `spiffe://<td>/ns/<namespace>/exporter/<name>`. This
  JEP has the controller request certificates from cert-manager through EST,
  rejects a controller-held CA (DD-5) and a gRPC enrollment service (DD-6),
  and uses `spiffe://<td>/jumpstarter/<kind>/<namespace>/<name>/<uid>`. An
  exporter must have one identity, issued one way. JEP-0015 treats peer IDs
  as opaque, so either format works for it; what must be settled is which
  JEP owns issuance. Proposed: this one, with JEP-0015 dropping
  `SignExporterCertificate` and depending on this JEP for the direct path,
  while its router path keeps working with bearer tokens.
- **B4. Python cannot verify a server by SPIFFE ID.** JEP-0015 has the dialing
  exporter check the URI SAN and not a DNS name, so that a peer can be
  reached by Pod IP. Python gRPC always checks the server certificate
  against a host name and offers no hook to replace that check. Tested
  against the gRPC the project resolves: a dial to a server whose
  certificate has only a URI SAN fails whatever name override is used, and
  succeeds once the certificate also carries a DNS name that the dialer
  supplies as its override. An exporter certificate that is used to serve
  therefore needs a DNS SAN that stands for its identity (for example a name
  derived from the SPIFFE ID under the trust domain), and the dialer sets
  that name. This changes three things in this JEP: the enrollment rule that
  a CSR holds "exactly that URI SAN and nothing else", the approver-policy
  example, and requirement I1 of the integration contract for issuers whose
  exporters take part in direct forwards. The serving side has no such
  problem: it sees the dialer's SPIFFE ID.
- **B5. Exporters have no identity trust bundle.** To verify a peer, an
  exporter needs the CA bundle of the peer's trust domain. In this JEP only
  the controller and routers hold those; clients and exporters receive the
  bundle for *serving* certificates. JEP-0015 returns bundles from its
  signing RPC. A replacement channel is needed: EST `cacerts` for the
  enrollment domain, and the controller's setup message for a forward for
  any other domain.
- **B6. Exporter certificates must be usable for serving, with a loadable
  key.** The peer listener needs the `serverAuth` usage, which this JEP's
  policy restricts to `clientAuth`. A Python gRPC server also takes its key
  as bytes, with no signing callback on the server side, so an exporter
  that serves peers (or direct mode clients) cannot keep its key in a TPM
  until the Rust core serves the connection. On a Python listener the
  certificate is checked against one pool of roots, so the per-domain
  verification of [Server-side TLS](#server-side-tls) is not available
  there; the direct path should be limited to peers in one trust domain
  until it is.
- **B7. Router tokens.** JEP-0015's DD-5 relies on one token handed to both
  exporters and on the router staying unchanged. This JEP issues one token
  per side and has the router check the binding. `DialPeer` can mint two
  bound tokens exactly as `Dial` does, but JEP-0015's "no router change"
  no longer holds once exporters use certificates.

#### Other gaps

- **B8. EST is used beyond what stock clients do.** `csrattrs` is defined to
  return attribute types, not a concrete SAN value for the caller, and the
  bootstrap token is a Jumpstarter credential. The Jumpstarter client is the
  only client that will enroll. "An existing standard with existing clients"
  is therefore a weaker argument for EST than DD-6 makes it, though the
  endpoint shapes and encodings are still worth reusing.
- **B9. Virtual exporters.** The `ExporterSet` reconciler writes the
  exporter's token into the config Secret mounted into each Pod. Those Pods
  have no state to keep a key in. They can enroll at start with that token
  and hold the key in memory, which works but makes the Secret the real
  credential; a Pod-bound identity (as JEP-0015 sketches with Pod
  certificates) would be better and is not designed here.
- **B10. One controller replica per exporter pair.** `Dial` reaches an
  exporter only through the replica holding its `Listen` stream, and token
  binding reads the exporter's identity from that stream. `DialPeer`
  involves two exporters whose `Listen` streams may be on different
  replicas. This is JEP-0015's to solve, but the binding must be solved
  with it.
- **B11. Marked users.** #1319 states that the internal-token authenticator
  is the only place that creates an internal user. Treating enrollment
  certificates as internal needs that rule widened, with its authors'
  agreement.
- **B12. Ingress in re-encrypt mode.** The setup guide suggests re-encrypt
  with a user-supplied certificate as the alternative to `--insecure-tls`.
  An ingress that terminates TLS drops the client certificate, so those
  deployments stay on bearer tokens until they move to passthrough.
- **B13. Certificate request objects.** Every enrollment and renewal creates a
  cert-manager `CertificateRequest`. Nothing removes them, so the controller
  must, and CI jobs that enroll a fresh client per run should use a
  workload identity instead.

### Implementation map

Where each change lands in the code on `main`. Paths are under `controller/`
unless they start with `python/`.

| Change | Where | Kind of change |
| --- | --- | --- |
| Config keys | `internal/config/types.go` (`Authentication`, `Grpc`, `LeasePolicy`, new `Enrollment`); operator `Jumpstarter` API and the code that renders the ConfigMap | New fields |
| Request and read client certificates | `internal/service/controller_service.go` (`Start`: the `tls.Config` for `:8082`); `internal/service/tls_credentials.go` (`LoadTLSCredentials`, used by the router) | Two `tls.Config` fields, plus reload |
| X.509 authenticator and the chain | New files in `internal/authentication/`, beside `bearer.go`; implements `ContextAuthenticator` | New code |
| Marked user types | `internal/authentication/internal.go` (from #1319) | One more type |
| Object resolution for external certificates | `internal/authorization/metadata.go` (`ContextAttributes`) | One branch |
| Username check and provisioning | `internal/authorization/basic.go` | None |
| Wiring | `cmd/main.go` and `internal/config/config.go` (`LoadConfiguration`), where the bearer authenticator is built today | Wrap in the chain |
| Callers without a bearer token | `internal/service/controller_service.go` (`recordExporterCredential` in `Register`, `telemetryToken` in `GetServiceEndpoints`, `rotateExporterToken`) | Three fixes |
| Virtual exporters | `internal/exporterset/exporterconfig.go`, which writes the token into the Pod's config Secret | Enroll at Pod start |
| EST endpoints | `internal/service/controller_service.go` (`Start`): requests that are not gRPC go to a gateway mux that has no routes registered today | New handlers |
| Per-side, bound router tokens | `internal/service/controller_service.go` (`Dial`, `listenQueue`); `internal/service/router_service.go` (`authenticate`) | Second token, one claim, one check |
| Router trust domains | `internal/config/config.go` (`LoadRouterConfiguration`), `cmd/router/main.go` | Read one more section |
| Lease ceiling | `internal/service/client/v1/client_service.go` (`CreateLease`, `UpdateLease`), beside the `MaxTags` check; `internal/service/controller_service.go` (`RequestLease`) | One check |
| Advertising enrollment | `internal/service/login/service.go` (`AuthConfig`) | One block |
| Client certificate on every connection | `python/packages/jumpstarter/jumpstarter/common/grpc.py` (`ssl_channel_credentials`) | Two arguments |
| Metadata without a token | `python/packages/jumpstarter/jumpstarter/config/grpc.py` (`call_credentials`) | One variant |
| `credentials` and `endpointOverrides` config | `python/packages/jumpstarter/jumpstarter/config/` (`client.py`, `exporter.py`, `tls.py`) | New fields |
| Credential sources (`enrollment`, `exec`, `spiffe`, `file`), expiry events and lease preflight | New module under `python/packages/jumpstarter/jumpstarter/`; preflight in `client/lease.py` | New code |
| Key stores | New module beside the credential sources, with file and OS stores | New code; one dependency |
| Hardware key stores (later) | Rust core, exposed through its Python bindings; `common/grpc.py` builds the channel from a signer; `grpcio` floor in `python/packages/jumpstarter-protocol/pyproject.toml` | With the Rust core |
| Refresh token out of the config file | `python/packages/jumpstarter/jumpstarter/config/client.py` (`refresh_token`), `python/packages/jumpstarter-cli/jumpstarter_cli/login.py` | Moved to the key store |
| Enrollment after login | `python/packages/jumpstarter-cli/jumpstarter_cli/login.py` | New step |
| Enrollment when creating clients and exporters | `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/create.py`; `python/packages/jumpstarter-kubernetes/jumpstarter_kubernetes/clients.py` and `exporters.py`, where the token is read from the Secret today | New step |
| Enrollment in local clusters | `python/packages/jumpstarter-kubernetes/jumpstarter_kubernetes/cluster/operator.py` (the `Jumpstarter` resource it applies); operator certificate reconciliation | One block; a second managed CA |
| Direct mode pairing | `python/packages/jumpstarter-cli/jumpstarter_cli/run.py` and `shell.py`; `python/packages/jumpstarter/jumpstarter/exporter/auth.py`, beside the passphrase interceptor | New code |
| Guides | `docs/source/getting-started/guides/setup/direct-mode.md` and `distributed-mode.md` | Rewritten around the secure path |

Nothing changes in the `.proto` files, in the `Client`, `Exporter`, or `Lease`
types, or in the lease controller.

### Failure modes

| Failure | Behavior |
| --- | --- |
| Certificate expired, not yet valid, or not issued by its trust domain | The handshake succeeds. If the call also carries a bearer token that is allowed for its principal kind, the certificate is ignored and logged. Otherwise the authenticator returns `UNAUTHENTICATED` with a message naming the cause and the validity window, and the client triggers renewal |
| No certificate under `clientAuth: Required` | TLS handshake fails; the caller sees a connection error, not a gRPC status |
| SPIFFE ID from an external trust domain matches no mapping | `UNAUTHENTICATED`, logged with the SPIFFE ID |
| Mapping allowed, but no matching `Client`/`Exporter` | Unchanged: authorization denies, or provisioning creates a `Client` where enabled and allowed. Exporters are never provisioned |
| Caller-supplied name or namespace differs from the mapped one | `INVALID_ARGUMENT`, as for OIDC name mismatches today |
| `clientAuth: Required` with enrollment enabled | Controller refuses to start |
| Trust bundle ConfigMap missing | That trust domain is disabled and logged; other domains keep working |
| cert-manager unavailable | `simpleenroll` and `simplereenroll` return `503`; existing certificates keep working until expiry |
| Both certificate and bearer token present with different identities | Rejected |

### Security analysis

| Threat | Today | With this JEP |
| --- | --- | --- |
| Exporter config copied off a device | Full impersonation until token expiry | No credential in config; the key is a separate file, and in a TPM once the Rust core supports it |
| Laptop lost or disk copied, without the owner's login | Token, and OIDC refresh token, readable from the config file | Key and refresh token in the OS keychain; key in the Secure Enclave or TPM once the Rust core supports it |
| Client token leaked (logs, shell history, CI) | Replayable from anywhere | Certificates require the private key; bound router tokens are useless to any other identity |
| Offboarding or decommissioning | Find and revoke tokens | Issuer stops issuing; access ends within TTL; deleting the `Client`/`Exporter` still denies every new RPC |
| Compromised controller | Can mint internal tokens | Same. With a supplied issuer, approver-policy limits enrollment to its trust domain, client auth, and a capped TTL; a managed issuer signs nothing but Jumpstarter identities |
| Issuer for domain A mints domain B identities | n/a | Per-domain chain verification |
| First connection to a LAN controller | `tls.insecure`: unverified on every connection | `--ca-sha256` pin, or bundle delivered by host management |
| Downgrade to bearer during migration | n/a | `bearer.allowedPrincipals` closes it per principal kind; `clientAuth: Required` closes it entirely where enrollment is not used |

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
  caller-supplied metadata, a `jumpstarter-kind` outside `principals`, and
  that an X.509 username never takes the OIDC name-derivation path.
- Enrollment identities: the path round-trips to the internal subject, the
  user is internal, and a certificate for a deleted object is refused by a
  re-created object of the same name.
- Enrollment: CSRs with a wrong, missing, or extra name rejected, SPIFFE ID
  derived from the principal, `CertificateRequest` construction,
  re-enrollment for deleted principals.
- Configuration validation: `clientAuth: Required` with enrollment enabled is
  rejected.
- Renewal grace: an expired certificate renews inside the grace period, is
  refused outside it, and is refused inside it once the object is deleted.
- Direct mode pairing: code expiry and single use, fingerprint mismatch,
  connection from an unpaired certificate, and `--unpair`.
- Router tokens: one per side, SPIFFE ID binding, mismatch rejection, and a
  bound token still accepted after the holder rotates its certificate.
- `endpointOverrides` resolution with preserved TLS server name.
- Credential sources: exec output parsing, file reload, renewal scheduling.
- Key stores: the three operations against each backend, `auto` selection per
  platform, and refusal to fall back to a file when a store disappears. When
  the hardware level arrives it runs against a software PKCS#11 token and a
  software TPM.
- `leasePolicy.maxDuration` on create, extend, and the legacy request path;
  client preflight against the credential's expiry.
- `RotateToken` from a certificate-authenticated caller returns
  `FAILED_PRECONDITION`.
- An invalid certificate with a permitted bearer token authenticates as the
  token; with a bearer token that is not permitted, it is rejected.
- A certificate-only exporter registers, and a certificate-only client and
  exporter receive a telemetry token whose lifetime is capped by the
  certificate.

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
- **Getting-started flows:** the local cluster guide and the direct mode guide
  run as written, with no insecure flag, as e2e jobs.

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
- [ ] On a cluster made with `jmp admin create cluster`, the five commands of
      the local cluster guide lead to a working shell with no insecure flag,
      no token in any config file, and no extra component installed.
- [ ] A client left unused for longer than its certificate lifetime, but
      within the grace period, works on its next use with no user action.
- [ ] In direct mode, one pairing command on the laptop is enough for every
      later `jmp shell --tls-grpc` to connect with mutual TLS and no other
      flag, and an unpaired machine is refused.
- [ ] On macOS, Windows, and a Linux desktop, a client created with the
      getting-started commands has no private key and no refresh token in
      any plain file, and connects with mutual TLS.
- [ ] `jmp login` against an enrollment-enabled controller stores a
      certificate, and renews it with `simplereenroll` before expiry.
- [ ] A CSR whose names differ from the caller's computed SPIFFE ID is
      rejected, and with a supplied issuer approver-policy denies a `CertificateRequest` outside the
      enrollment trust domain.
- [ ] A certificate enrolled for an object stops working when that object is
      deleted and re-created under the same name.
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
- The key store interface, with the file and OS stores.
  Bearer tokens remain the default everywhere.
- Integration contract published, with SPIRE conformance tests passing.
- Feedback wanted: mapping ergonomics, the integration contract, the Teleport
  reference integration, and router endpoint overrides.

### Beta

- Enrollment endpoint and `enrollment` credential source available.
- New installations created by the operator enable enrollment with the managed
  issuer, `jmp admin create cluster` included, and `jmp login` and
  `jmp admin create client|exporter` enroll by default when the controller
  offers it.
- Direct mode pairing available, and the getting-started guides rewritten
  around the secure paths.
- Hardware key stores are not a condition for Beta or Stable. They follow the
  Rust core, with their own acceptance test: with a TPM, Secure Enclave, or
  PKCS#11 token the private key cannot be exported, and the client connects
  and renews with it.
- Configs written for another machine carry a one-time bootstrap credential
  instead of the long-lived token.
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
- **Direct mode.** `--tls-grpc-insecure`, `--passphrase`, `--tls-cert`, and
  `--tls-key` behave as today. Pairing is used only when neither side passes
  one of them.
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
  serving certificates. The operator creates approver-policy resources only
  when enrollment uses a supplied issuer, and reports a condition rather than
  failing when the CRDs are missing. trust-manager is never required; it is
  one way to produce the bundle ConfigMaps.

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
- The getting-started paths become the secure ones, with the same commands
  and fewer flags than today.

### Negative

- More moving parts: trust bundles, an issuer, and, when chaining to a
  corporate root, approver-policy with the default approver disabled.
- A client's key lives in a key store, not in its config file, so a setup
  cannot be moved to another machine by copying a file. That is the point,
  but it is a change: moving means enrolling again on the new machine.
- Key stores differ by platform, which adds platform-specific code and a test
  matrix.
- Until the Rust core lands, the key is protected at rest but is loaded into
  the client process, and headless exporters keep it in a file.
- The renewal grace period trades some of the "access ends within the
  lifetime" property for convenience in the default profile.
- Certificate expiry becomes an operational concern: clocks on SBCs, mid-lease
  expiry for users, and offline exporters that miss renewal.
- TLS must terminate in the controller and router pods. Deployments that
  terminate gRPC TLS at an ingress cannot use certificate authentication.
- Two authentication paths have to be maintained and tested for as long as
  bearer tokens are supported.
- Object resolution gains a branch for certificates from external issuers,
  alongside the rules for internal tokens and OIDC.

### Risks

- Hardware stores depend on the Rust core, which has no date, and on a gRPC
  Python API that is marked experimental for as long as Python makes the
  connections.
- #1319 and #1320 are not merged. If they change shape, the object resolution
  design has to follow.
- JEP-0015 defines exporter certificates of its own. If both proposals are
  implemented as written, exporters end up with two identities and two
  issuers.
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
  standard with existing client implementations and fits the controller's
  existing listener (DD-6).

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
- **EST (RFC 7030)** and **certificate-bound tokens (RFC 8705).** The latter
  is the model for binding router tokens; DD-8 binds to the identity instead
  of the thumbprint it specifies.
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

1. **SPIFFE path layout for external issuers.** Enrollment identities use the
   internal subject as their path. Should Jumpstarter also recommend a layout
   for external issuers (for example `/jumpstarter/exporters/<name>`), so
   that a default mapping works without configuration?
2. **Lifetime and grace defaults.** The JEP proposes 30-day certificates and a
   90-day renewal grace period with the managed issuer, and no grace
   otherwise. Are these the right numbers, and should a deployment with an
   external identity provider be allowed a grace period for exporters only?
3. **Bootstrap token lifecycle.** Should one-time enrollment tokens be a new
   field on `Exporter` status, a separate Secret, or reuse the existing exporter
   credential Secret? The option that fits the code best is a second key in
   the existing credential Secret, which `jmp admin create` already reads,
   holding a short-lived token from the same signer with an enrollment-only
   audience. The gRPC authenticator only accepts the signer's normal
   audience, so such a token is useless anywhere but the EST endpoint. Until
   this is settled, the bootstrap credential is today's token.
4. **Key storage.** Is `auto` the right default order on each platform? Is the
   OS keychain, with the key loaded into the process, acceptable until the
   Rust core provides hardware stores? When it does, should existing clients
   be moved to the better store at their next renewal or only when they
   enroll again? Should presence checks (Touch ID, a TPM PIN) be offered at
   all?
5. **Contract publication.** Should the integration contract be versioned and
   published as a standalone document, so platforms and third parties can
   claim conformance? Where should `exec` presets live: in the Jumpstarter
   repository, or with each platform?
6. **Flight Control specifics to verify:** native FDO support, and whether
   Flight Control-issued device certificates could serve directly as an
   identity issuer.
7. **JEP-0015.** Which JEP issues exporter identities, and in which SPIFFE ID
   format? Should this JEP require JEP-0015's direct path to wait for it, and
   should exporter certificates carry a DNS name for their identity so that
   Python can verify a peer (see [Interface coverage](#interface-coverage))?
8. **Direct mode pairing.** Pairing shares this JEP's goal but none of its
   machinery. Should it stay here, or move to its own JEP so it can ship
   independently? Either way, the code format (length, key derivation,
   lifetime) needs a security review before it is fixed.
9. **Multiple controller replicas.** `Dial` reaches an exporter only through
   the replica holding its `Listen` stream. That is true today and this JEP
   relies on it for router token binding; if dials are ever routed across
   replicas, the exporter's identity has to travel with them.
10. **Default `leasePolicy.maxDuration`:** unset (today's behavior) or a
   conservative default for new installations?
11. **Relationship to host management proposals.** JEP-0017
   ([#1253](https://github.com/jumpstarter-dev/jumpstarter/pull/1253)) and
   [#1242](https://github.com/jumpstarter-dev/jumpstarter/issues/1242) cover
   Flight Control management of exporter hosts. Which of them owns delivery of
   the trust bundle and bootstrap credentials should be settled there.
12. **Reference integration verification.** Items specific to each reference
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
  hardware-bound keys. Until then the key store is the client's choice and
  the controller cannot verify it.
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
- 2026-11-12: Aligned with the current implementation: enrollment identities
  reuse the internal subject and the credential-kind pattern of #1319 and
  #1320, credential sources target the Python client on `main`, and an
  implementation map was added
- 2026-11-12: Added the new-user experience: operator-managed issuer for local
  clusters, renewal grace period, and pairing for direct mode
- 2026-11-12: Checked the reference integration snippets against vendor
  documentation, specified how pairing works within Python gRPC, and removed
  inconsistencies left by earlier revisions
- 2026-11-12: Added key storage: a key store interface with file and OS
  keychain stores in the Python client, and hardware stores (TPM, Secure
  Enclave, PKCS#11) deferred to the Rust core
- 2026-11-13: Added interface coverage and known blockers, including the
  overlap with JEP-0015's exporter certificates

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
- `python/packages/jumpstarter/jumpstarter/config/grpc.py` (`call_credentials`)
- `python/packages/jumpstarter/jumpstarter/common/streams.py` (`connect_router_stream`)
- `controller/internal/service/client/v1/client_service.go` (`CreateLease`, `UpdateLease`, `RotateToken`)
- [py-spiffe](https://pypi.org/project/spiffe/)
- [grpcio 1.80.0 release notes](https://newreleases.io/project/pypi/grpcio/release/1.80.0) (TLS private key signer in Python)
- [Apple Developer Forums: -34018 when using Secure Enclave](https://developer.apple.com/forums/thread/728150)
- [Teleport: Workload Identity attributes](https://goteleport.com/docs/reference/machine-workload-identity/workload-identity/attributes/)
- [OpenZiti: third-party CAs](https://openziti.io/docs/learn/core-concepts/security/authentication/third-party-cas)
- `python/packages/jumpstarter-cli/jumpstarter_cli/run.py` and `shell.py` (direct mode: `--tls-grpc-listener`, `--tls-grpc`, `--passphrase`)
- `python/packages/jumpstarter-kubernetes/jumpstarter_kubernetes/cluster/operator.py` (what `jmp admin create cluster` installs)
- [cert-manager: installing approver-policy](https://cert-manager.io/docs/policy/approval/approver-policy/installation/)
- JEP-0015 ([#1069](https://github.com/jumpstarter-dev/jumpstarter/pull/1069)): Multi-Exporter Leases and Inter-Exporter Port Forwarding (DD-4, DD-5, DD-25)
- `controller/internal/exporterset/exporterconfig.go` (virtual exporter credentials)
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
