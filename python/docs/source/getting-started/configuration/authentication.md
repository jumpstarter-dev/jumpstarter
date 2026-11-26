# Authentication

Jumpstarter uses internally issued JWT tokens to authenticate clients and
{term}`exporter`s by default. You can also configure Jumpstarter to use external OpenID
Connect (OIDC) providers.

When installing with the {term}`operator`, authentication is configured directly on the
`Jumpstarter` custom resource, under `spec.authentication`. 

For {term}`operator` installation context, see
[Production](../installation/service/production.md).

To use OIDC with your Jumpstarter installation:

1. Set `spec.authentication.jwt` on your `Jumpstarter` resource
2. Configure your OIDC provider to work with Jumpstarter
3. Create users with appropriate OIDC usernames

## Migrating credentials after a signing-key change

The controller normally verifies tokens using the current signing key. During
migration, it can also accept an **exact, unexpired credential already stored in
Kubernetes**. It does not trust signatures made with the previous signing key.

This fallback is enabled by default. It runs only after normal authentication
fails. Valid current-key and external OIDC tokens do not cause an additional
Secret lookup. The fallback checks the token's issuer, audience, expiry and
internal subject, then requires an exact token or SHA-256 hash match in the
cached Secret before re-reading the Secret and its owner directly from the API
server. Normal resource authorization, including the identity's UID, still applies.

When reconciliation replaces an old-key token in a `<name>-client` or
`<name>-exporter` Secret, it retains the old token's hash in the Secret's
`jumpstarter.dev/legacy-token-hash` annotation. Only the credential actually in
the Secret is retained; tokens supplied in requests are never enrolled. Install
this migration support together with the signing-key fix: a credential already
overwritten without retaining its hash cannot be recovered by this mechanism.

Old tokens keep working until their own expiry; the fallback never extends it.
Release 0.9 does not provide automatic exporter token refresh. Before the old
token expires, update each exporter's local `token` field from its replacement
Secret, preserving driver and TLS settings, and restart the exporter. Clients
must be re-imported with `jmp admin import` to pick up their replacement
credentials. Each fallback authentication logs a warning naming the credential
Secret, without logging the token.

Keep the fallback enabled until the old tokens have expired. Turning it off
earlier cuts off holders that have not refreshed. The switch only controls
authentication: reconciliation records and retains the hash even while fallback
is off. Turning it off deletes nothing, and turning it back on restores access
for unexpired retained tokens. To permanently revoke a retained credential,
delete the Secret's `jumpstarter.dev/legacy-token-hash` annotation or the Secret
itself. These changes affect new authentication; they do not retroactively
reauthenticate existing streams.

The same migration path covers a `CONTROLLER_KEY` rotation. Only one previous
generation is retained, so update holders' local credentials before rotating
again. The retained hash stays until the next credential regeneration, even
after expiry; an expired token is rejected by the authenticator.

After a suspected signing-key leak, turn the fallback off and re-issue
credentials. Otherwise, genuine tokens obtained earlier would be carried over
to the new signing key through the stored-token fallback.

For manually managed deployments, disable the fallback in the controller
ConfigMap's `config` section and restart the controller:

```yaml
authentication:
  internal:
    allowStoredTokenFallback: false
```

When omitted, `allowStoredTokenFallback` uses the controller's current default
(`true`). Explicit `true` and `false` values are preserved.

The release 0.9 operator does not expose this setting and leaves it omitted,
so operator-managed installations use the enabled default. No operator or CRD
update is required for this backport.

## Username Collisions

When using OIDC auto provisioning, Jumpstarter derives resource names directly from
the OIDC username by stripping the provider prefix (e.g., "dex:", "keycloak:")
and sanitizing the result to meet Kubernetes naming requirements.

**This means that if you configure multiple OIDC providers with users that have
the same username, those users will map to the same Jumpstarter resource name,
potentially causing conflicts.**

For example:
- User `dex:developer` maps to resource name `developer`
- User `keycloak:developer` also maps to resource name `developer`

This is an **accepted limitation** to keep resource names clean and readable.
To avoid collisions:

1. Use a single OIDC provider per Jumpstarter installation, or
2. Ensure usernames are unique across all configured OIDC providers, or
3. Use different username claim mappings that include provider-specific prefixes, or
4. Pre-create the resource (Client/{term}`Exporter`) with explicit username mappings when conflicts exist

## Examples

### Keycloak

Set up Keycloak for Jumpstarter authentication:

1. Create a new Keycloak client with these settings:
   - `Client ID`: `jumpstarter-cli`
   - `Valid redirect URIs`: `http://localhost/callback`
   - Leave remaining fields as default

2. Configure `spec.authentication.jwt` on your `Jumpstarter` resource:

```yaml
spec:
  authentication:
    jwt:
    - issuer:
        url: https://<keycloak domain>/realms/<realm name>
        certificateAuthority: <PEM encoded CA certificates>
        audiences:
        - jumpstarter-cli
      claimMappings:
        username:
          claim: preferred_username
          prefix: "keycloak:"
```

Note, the HTTPS URL is mandatory, and you only need to include
certificateAuthority when using a self-signed certificate. The username will be
prefixed with "keycloak:" (e.g., keycloak:example-user).

3. Create clients and {term}`exporter`s with the `jmp admin create` commands. Be sure to
   prefix usernames with `keycloak:` as configured in the claim mappings:

```console
$ jmp admin create client test-client --insecure-tls --oidc-username keycloak:developer-1
```

4. Instruct users to log in with:

```console
$ jmp login --client <client alias> \
    --insecure-tls \
    --endpoint <jumpstarter controller endpoint> \
    --namespace <namespace> --name <client name> \
    --issuer https://<keycloak domain>/realms/<realm name>
```

For non-interactive login, add username and password:

```console
$ jmp login --client <client alias> [other parameters] \
    --insecure-tls \
    --username <username> \
    --password <password>
```

For machine-to-machine authentication (useful in CI environments), use a token:

```console
$ jmp login --client <client alias> [other parameters] --token <token>
```

For {term}`exporter`s, use similar login command but with the `--exporter` flag:

```console
$ jmp login --exporter <exporter alias> \
    --insecure-tls \
    --endpoint <jumpstarter controller endpoint> \
    --namespace <namespace> --name <exporter name> \
    --issuer https://<keycloak domain>/realms/<realm name>
```

### Dex

Follow these steps to set up Dex for service account authentication:

1. Initialize a self-signed CA and sign certificate for Dex:

```console
$ easyrsa init-pki
$ easyrsa --no-pass build-ca
$ easyrsa --no-pass build-server-full dex.dex.svc.cluster.local
```

Then import the certificate into a Kubernetes secret:

```console
$ kubectl create namespace dex
$ kubectl -n dex create secret tls dex-tls \
    --cert=pki/issued/dex.dex.svc.cluster.local.crt \
    --key=pki/private/dex.dex.svc.cluster.local.key
```

2. Install Dex with Helm using the following `values.yaml`:

```yaml
https:
  enabled: true
config:
  issuer: https://dex.dex.svc.cluster.local:5556
  web:
    tlsCert: /etc/dex/tls/tls.crt
    tlsKey: /etc/dex/tls/tls.key
  storage:
    type: kubernetes
    config:
      inCluster: true
  staticClients:
    - id: jumpstarter-cli
      name: Jumpstarter CLI
      public: true
  connectors:
    - name: kubernetes
      type: oidc
      id: kubernetes
      config:
        # kubectl get --raw /.well-known/openid-configuration | jq -r '.issuer'
        issuer: "https://kubernetes.default.svc.cluster.local"
        rootCAs:
          - /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        userNameKey: sub
        scopes:
          - profile
volumes:
  - name: tls
    secret:
      secretName: dex-tls
volumeMounts:
  - name: tls
    mountPath: /etc/dex/tls
service:
  type: ClusterIP
  ports:
    http:
      port: 5554
    https:
      port: 5556
```

Ensure OIDC discovery URLs do not require authentication:

```console
$ kubectl create clusterrolebinding oidc-reviewer  \
    --clusterrole=system:service-account-issuer-discovery \
    --group=system:unauthenticated
```

Then install Dex:

```console
$ helm repo add dex https://charts.dexidp.io
$ helm install --namespace dex --wait -f values.yaml dex dex/dex
```

3. Configure Jumpstarter to trust Dex. Use this configuration for
   `jumpstarter-controller.authenticationConfiguration` during installation:

```yaml
spec:
  authentication:
    jwt:
    - issuer:
        url: https://dex.dex.svc.cluster.local:5556
        audiences:
        - jumpstarter-cli
        audienceMatchPolicy: MatchAny
        certificateAuthority: |
          <content of pki/ca.crt>
      claimMappings:
        username:
          claim: "name"
          prefix: "dex:"
```

4. Create clients and {term}`exporter`s with appropriate OIDC usernames. Prefix the full
   service account name with "dex:" as configured in the claim mappings.:

```console
$ jmp admin create exporter test-exporter --label foo=bar \
    --insecure-tls \
    --oidc-username dex:system:serviceaccount:default:test-service-account
```

5. Configure pods with proper service accounts to log in using:

For clients:

```console
$ jmp login --client <client alias> \
    --insecure-tls \
    --endpoint <jumpstarter controller endpoint> \
    --namespace <namespace> --name <client name> \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
```

For {term}`exporter`s:

```console
$ jmp login --exporter <exporter alias> \
    --insecure-tls \
    --endpoint <jumpstarter controller endpoint> \
    --namespace <namespace> --name <exporter name> \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
```

## Reference

For the full `spec.authentication` field reference, see the
[Jumpstarter {term}`CRD`](../../reference/crds/jumpstarter.md).
