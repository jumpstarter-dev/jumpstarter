# Authentication

Jumpstarter uses internally issued JWT tokens to authenticate clients and
exporters by default. You can also configure Jumpstarter to use external OpenID
Connect (OIDC) providers.

To use OIDC with your Jumpstarter installation:

1. Set the helm value `jumpstarter-controller.authenticationConfiguration` to a
   valid `AuthenticationConfiguration` yaml configuration
2. Configure your OIDC provider to work with Jumpstarter
3. Create users with appropriate OIDC usernames

## Examples

### Keycloak

Set up Keycloak for Jumpstarter authentication:

1. Create a new Keycloak client with these settings:
   - `Client ID`: `jumpstarter-cli`
   - `Valid redirect URIs`: `http://localhost/callback`
   - Leave remaining fields as default

2. Use this configuration for
   `jumpstarter-controller.authenticationConfiguration` during installation:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: AuthenticationConfiguration
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

3. Create clients and exporters with the `jmp admin create` commands. Be sure to
   prefix usernames with `keycloak:` as configured in the claim mappings:

```console
$ jmp admin create client test-client --insecure-tls-config --oidc-username keycloak:developer-1
```

4. Instruct users to log in with:

```console
$ jmp login --client <client alias> \
    --insecure-tls-config \
    --endpoint <jumpstarter controller endpoint> \
    --namespace <namespace> --name <client name> \
    --issuer https://<keycloak domain>/realms/<realm name>
```

For non-interactive login, add username and password:

```console
$ jmp login --client <client alias> [other parameters] \
    --insecure-tls-config \
    --username <username> \
    --password <password>
```

For machine-to-machine authentication (useful in CI environments), use a token:

```console
$ jmp login --client <client alias> [other parameters] --token <token>
```

For exporters, use similar login command but with the `--exporter` flag:

```console
$ jmp login --exporter <exporter alias> \
    --insecure-tls-config \
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
apiVersion: jumpstarter.dev/v1alpha1
kind: AuthenticationConfiguration
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

4. Create clients and exporters with appropriate OIDC usernames. Prefix the full
   service account name with "dex:" as configured in the claim mappings.:

```console
$ jmp admin create exporter test-exporter --label foo=bar \
    --insecure-tls-config \
    --oidc-username dex:system:serviceaccount:default:test-service-account
```

5. Configure pods with proper service accounts to log in using:

For clients:

```console
$ jmp login --client <client alias> \
    --insecure-tls-config \
    --endpoint <jumpstarter controller endpoint> \
    --namespace <namespace> --name <client name> \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
```

For exporters:

```console
$ jmp login --exporter <exporter alias> \
    --insecure-tls-config \
    --endpoint <jumpstarter controller endpoint> \
    --namespace <namespace> --name <exporter name> \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
```

## Reference

The reference section provides a complete example of an
`AuthenticationConfiguration` resource with detailed comments. Use this as a
template for creating your own configuration.

Key components include:

- JWT issuer configuration
- Claim validation rules
- Claim mappings for username and groups
- User validation rules

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: AuthenticationConfiguration
# JWT authenticators for OIDC-issued tokens
jwt:
- issuer:
    # URL of the OIDC provider (must use https://)
    url: https://example.com
    # Optional: override URL for discovery information
    discoveryURL: https://discovery.example.com/.well-known/openid-configuration
    # Optional: PEM encoded CA certificates for validation
    certificateAuthority: <PEM encoded CA certificates>
    # List of acceptable token audiences
    audiences:
    - my-app
    - my-other-app
    # Required when multiple audiences are specified
    audienceMatchPolicy: MatchAny
  # rules applied to validate token claims to authenticate users.
  claimValidationRules:
    # Validate specific claim values
  - claim: hd
    requiredValue: example.com
    # Alternative: use CEL expressions for complex validation
  - expression: 'claims.hd == "example.com"'
    message: the hd claim must be set to example.com
  - expression: 'claims.exp - claims.nbf <= 86400'
    message: total token lifetime must not exceed 24 hours
  # Map OIDC claims to Jumpstarter user properties
  claimMappings:
    # Required: configure username mapping
    username:
      # JWT claim to use as username
      claim: "sub"
      # Prefix for username (required when claim is set)
      prefix: ""
      # Alternative: use CEL expression (mutually exclusive with claim+prefix)
      # expression: 'claims.username + ":external-user"'
    # Optional: configure groups mapping
    groups:
      claim: "sub"
      prefix: ""
      # Alternative: use CEL expression
      # expression: 'claims.roles.split(",")'
    # Optional: configure UID mapping
    uid:
      claim: 'sub'
      # Alternative: use CEL expression
      # expression: 'claims.sub'
    # Optional: add extra attributes to UserInfo
    extra:
    - key: 'example.com/tenant'
      valueExpression: 'claims.tenant'
  # validation rules applied to the final user object.
  userValidationRules:
  - expression: "!user.username.startsWith('system:')"
    message: 'username cannot used reserved system: prefix'
  - expression: "user.groups.all(group, !group.startsWith('system:'))"
    message: 'groups cannot used reserved system: prefix'
```
