# Authentication

Jumpstarter uses internally issued JWT tokens to authenticate clients and
{term}`exporter`s by default. You can also configure Jumpstarter to use external OpenID
Connect (OIDC) providers.

When installing with the {term}`operator`, authentication is configured directly on the
`Jumpstarter` Custom Resource, under `spec.authentication`. 

For {term}`operator` installation context, see
[Production](../installation/service/production.md).

To use OIDC with your Jumpstarter installation:

1. Set `spec.authentication.jwt` on your `Jumpstarter` resource
2. Configure your OIDC provider to work with Jumpstarter
3. Create users with appropriate OIDC usernames

## Username Collisions

For OIDC users, except Kubernetes service accounts, Jumpstarter derives resource names directly from
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
   prefix usernames with `keycloak:` as configured in the claim mappings. Name each
   resource after the username without its prefix, as described in
   [Username Collisions](#username-collisions): except for Kubernetes service
   accounts, the {term}`controller` rejects requests from an OIDC user whose client
   or {term}`exporter` name differs.

```console
$ jmp admin create client developer-1 --insecure-tls --oidc-username keycloak:developer-1
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

### Dex with GitHub

Follow these steps to set up Dex with GitHub as the identity provider,
allowing members of specific GitHub organizations to authenticate with
Jumpstarter using their GitHub accounts.

#### 1. Create a GitHub OAuth App

1. Navigate to your GitHub organization's settings:
   `https://github.com/organizations/<your-org>/settings/applications/new`

   :::{note}
   You need admin access to the organization. The OAuth App only needs to be
   created in one organization, even if you want to allow users from multiple
   organizations.
   :::

2. Fill in the application details:

   | Field | Value |
   |-------|-------|
   | **Application name** | `Jumpstarter` (or any descriptive name) |
   | **Homepage URL** | `https://<dex-domain>` |
   | **Authorization callback URL** | `https://<dex-domain>/callback` |

   Replace `<dex-domain>` with the domain where Dex will be accessible
   (e.g., `dex.apps.example.org`).

3. Click **Register application**.

4. On the application page:
   - Copy the **Client ID** (displayed at the top)
   - Click **Generate a new client secret** and copy the secret immediately
     (it is only shown once)

5. If you are restricting access to multiple GitHub organizations, each
   organization may need to grant access to the OAuth App. Organization admins
   can approve access at:
   `https://github.com/organizations/<org-name>/settings/oauth_application_policy`

#### 2. Create the GitHub credentials secret

Create a namespace for Dex and a secret containing the OAuth App credentials:

```console
$ kubectl create namespace dex
$ kubectl -n dex create secret generic dex-github \
    --from-literal=client-id='<GitHub Client ID>' \
    --from-literal=client-secret='<GitHub Client Secret>'
```

:::{important}
Do not store the GitHub client secret in version control. Create the secret
directly on the cluster.
:::

:::{note}
The command above passes the secret on the command line, which an
interactive shell may retain in history. If you prefer to avoid that,
write the secret to a permission-restricted file (`chmod 600`) and use
`--from-file=client-secret=/path/to/restricted/github-client-secret`
instead.
:::

#### 3. Deploy Dex

Deploy Dex with the GitHub connector. The example below uses Kubernetes
manifests. Adjust the `<dex-domain>` placeholder and the list of GitHub
organizations to match your environment:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dex-config
  namespace: dex
data:
  config.yaml: |
    issuer: https://<dex-domain>
    storage:
      type: kubernetes
      config:
        inCluster: true
    web:
      http: 0.0.0.0:5556
    connectors:
      - type: github
        id: github
        name: GitHub
        config:
          clientID: $GITHUB_CLIENT_ID
          clientSecret: $GITHUB_CLIENT_SECRET
          redirectURI: https://<dex-domain>/callback
          orgs:
            - name: <your-github-org>
            - name: <another-github-org>  # optional
    staticClients:
      - id: jumpstarter-cli
        name: Jumpstarter CLI
        public: true
    oauth2:
      skipApprovalScreen: true
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dex
  namespace: dex
spec:
  replicas: 1
  selector:
    matchLabels:
      app: dex
  template:
    metadata:
      labels:
        app: dex
    spec:
      serviceAccountName: dex
      containers:
        - name: dex
          image: ghcr.io/dexidp/dex:v2.41.1
          command: ["dex", "serve", "/etc/dex/config.yaml"]
          ports:
            - name: http
              containerPort: 5556
          env:
            - name: GITHUB_CLIENT_ID
              valueFrom:
                secretKeyRef:
                  name: dex-github
                  key: client-id
            - name: GITHUB_CLIENT_SECRET
              valueFrom:
                secretKeyRef:
                  name: dex-github
                  key: client-secret
          volumeMounts:
            - name: config
              mountPath: /etc/dex
              readOnly: true
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
      volumes:
        - name: config
          configMap:
            name: dex-config
```

Dex also needs RBAC permissions for its Kubernetes storage backend:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dex
  namespace: dex
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: dex
rules:
  - apiGroups: ["dex.coreos.com"]
    resources: ["*"]
    verbs: ["*"]
  - apiGroups: ["apiextensions.k8s.io"]
    resources: ["customresourcedefinitions"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: dex
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: dex
subjects:
  - kind: ServiceAccount
    name: dex
    namespace: dex
```

Expose Dex with a Service, then externally:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: dex
  namespace: dex
spec:
  selector:
    app: dex
  ports:
    - name: http
      port: 5556
      targetPort: http
```

On OpenShift, use a Route with edge TLS termination (the cluster's
wildcard certificate handles HTTPS automatically):

```yaml
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: dex
  namespace: dex
spec:
  host: <dex-domain>
  port:
    targetPort: http
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
  to:
    kind: Service
    name: dex
```

On vanilla Kubernetes, use an Ingress with cert-manager or your preferred TLS
solution instead.

:::{note}
The `staticClients` entry for `jumpstarter-cli` must not include
`redirectURIs`. When a public client has no `redirectURIs` configured, Dex
automatically accepts any `http://localhost:<port>/...` callback per
[RFC 8252](https://datatracker.ietf.org/doc/html/rfc8252), which is required
for CLI-based OAuth flows that listen on random ports.
:::

#### 4. Configure Jumpstarter to trust Dex

Add Dex as a JWT issuer on your `Jumpstarter` resource. Enable
`autoProvisioning` so that a client is created automatically for each user on
their first request to the {term}`controller`:

```yaml
spec:
  authentication:
    autoProvisioning:
      enabled: true
    jwt:
    - issuer:
        url: https://<dex-domain>
        audiences:
        - jumpstarter-cli
      claimMappings:
        username:
          claim: "preferred_username"
          prefix: "github:"
```

#### 5. Verify the deployment

```console
$ curl -s https://<dex-domain>/.well-known/openid-configuration | jq .issuer
"https://<dex-domain>"
```

#### 6. Log in

Users from the configured GitHub organizations can log in using the simplified
login format:

```console
$ jmp login <client-name>@<login-endpoint>
```

For example:

```console
$ jmp login myuser@jumpstarter-login.apps.example.org:443
```

Use the GitHub username in lowercase as `<client-name>`: the {term}`controller`
derives the client name from the username, see
[Username Collisions](#username-collisions).

This prints a URL to open in a browser for GitHub OAuth authorization. After
approval, the user is authenticated as `github:<github-username>` in Jumpstarter.

With `autoProvisioning` enabled, no prior `jmp admin create client` step is
needed: the client resource is created automatically on the first request to the
{term}`controller`.

See the `jmp login` [man page](../../reference/man-pages/jmp.md) for the full
list of options.

### Dex with Kubernetes Service Accounts

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

3. Configure Jumpstarter to trust Dex. Add this configuration to your
   `Jumpstarter` resource:

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

## Managing Tokens

`jmp login` requests the `offline_access` scope by default and stores the
refresh token in the client config when the provider returns one; pass
`--no-offline-access` to skip it. When the config already holds a refresh token
and none of `--token`, `--username` or `--password` is given, `jmp login` first
renews the token with it and keeps requesting `offline_access`, so
`--no-offline-access` and `--device-flow` only take effect when no refresh token
is stored or that renewal fails. Inspect and renew the stored credentials with
`jmp auth`:

```console
$ jmp auth status          # Expiry, subject and issuer of the client token
$ jmp auth status -o json  # The same for scripts, also as -o yaml
$ jmp auth refresh         # Get a new access token with the stored refresh token
$ jmp auth rotate          # Replace an internal token with a freshly signed one
```

With `-o json` or `-o yaml`, `jmp auth status` reports a `status` of `valid`,
`expiring-soon` (less than five minutes left), `expired`, `no-expiry`,
`no-token` or `invalid-token`, together with `expiresAt`, `remainingSeconds`,
`subject`, `issuer` and `refreshTokenStored`.

When `jmp get`, `jmp describe`, `jmp create`, `jmp update`, `jmp delete`,
`jmp share` or `jmp shell` fails because the token has expired, the CLI logs in
again with the token's OIDC issuer and retries the command once. It uses the
stored refresh token when it can, and otherwise starts a new login. These
messages go to stderr, so JSON output on stdout stays parseable, and the new
tokens are saved to the client config file that was loaded, including one
passed with `--client-config`.

The {term}`controller` records the expiry of a client's internal token in the
`status.tokenExpiresAt` field of its `Client` resource, and sets the
`TokenExpiring` condition to `True` once fewer than 30 days remain.

### Headless and Container Environments

By default, `jmp login` prints a URL to open in a browser and waits for the
OIDC provider to redirect to a callback server on `localhost`. Where that
redirect cannot reach the CLI, for example in a container or a cloud IDE, use
the OAuth 2.0 Device Authorization Grant
([RFC 8628](https://datatracker.ietf.org/doc/html/rfc8628)) instead:

```console
$ jmp login --device-flow <client-name>@<login-endpoint>
```

The CLI prints a verification URL, and a code to enter there unless the URL
already contains it, then waits until you approve the login in any browser. Set
`JMP_OIDC_DEVICE_FLOW=1` to use the device flow without the flag, including when
the CLI logs in again after a token expires. The OIDC provider must list a
`device_authorization_endpoint` in its OIDC discovery document, otherwise
`jmp login` stops with an error. `--token`, and `--username` with `--password`,
take precedence over the device flow.

## Reference

For the full `spec.authentication` field reference, see the
[Jumpstarter {term}`CRD`](../../reference/crds/jumpstarter.md).
