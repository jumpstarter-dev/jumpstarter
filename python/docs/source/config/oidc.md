# OIDC Authentication

Jumpstarter authenticates clients and exporters with internally issued JWT tokens by default, it can also be configured to use external OpenID Connect (OIDC) providers.

To use OIDC with you Jumpstarter installation, set the helm value `jumpstarter-controller.authenticationConfiguration` to a valid `AuthenticationConfiguration` yaml configuration.

## Examples

### Keycloak

Create a new keycloak client for the jumpstarter cli, set `Client ID` to `jumpstarter-cli`, `Valid redirect URIs` to `http://localhost/callback` and leave the remaining fields as default. Use the following snippet as `jumpstarter-controller.authenticationConfiguration` during Jumpstarter installation.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: AuthenticationConfiguration
jwt:
- issuer:
    url: https://<keycloak domain>/realms/<realm name> # https is mandatory
    certificateAuthority: <PEM encoded CA certificates> # if using self-signed certificate
    audiences:
    - jumpstarter-cli
  claimMappings:
    # use user ID prefixed with "keycloak:" as username
    # e.g. keycloak:example-user
    username:
      claim: preferred_username
      prefix: "keycloak:"
```

Then proceed to create clients and exporters with the `jmp admin create` commands, set their corresponding OIDC username with the `--oidc-username` flag, e.g. `jmp admin create client test-client --oidc-username keycloak:developer-1`. Be sure to prefix usernames with "keycloak:", as previously configured.

Finally, instruct the users to login with the following commands

```
# for clients
jmp client login <client alias> --endpoint <jumpstarter controller endpoint> \
  --namespace <namespace> --name <client name> \
  --issuer https://<keycloak domain>/realms/<realm name>
# without additional options, the users would be directed to login with the web browser
# or the username and password can be directly specified for non-interactive login
  --username <username> --password <password>
# or a token for machine to machine authentication, useful in CI environments
  --token <token>

# for exporters
jmp exporter login <exporter alias> --endpoint <jumpstarter controller endpoint> \
  --namespace <namespace> --name <exporter name> \
  --issuer https://<keycloak domain>/realms/<realm name>
# --username, --password and --token are also accepted by jmp exporter login
```

### Dex (for authenticating with kubernetes Service Accounts)

Initialize a self-signed CA and sign certificate for dex

```shell
easyrsa init-pki
easyrsa --no-pass build-ca
easyrsa --no-pass build-server-full dex.dex.svc.cluster.local

# import certificate into secret
kubectl create namespace dex
kubectl -n dex create secret tls dex-tls \
  --cert=pki/issued/dex.dex.svc.cluster.local.crt \
  --key=pki/private/dex.dex.svc.cluster.local.key
```

Install dex with helm

```yaml
# dex.values.yaml
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

```shell
# Ensure OIDC discovery URLs do not require authentication
kubectl create clusterrolebinding oidc-reviewer  \
 --clusterrole=system:service-account-issuer-discovery \
 --group=system:unauthenticated

helm repo add dex https://charts.dexidp.io
helm install --namespace dex --wait -f dex.values.yaml dex dex/dex
```

Configure Jumpstarter to trust dex by using the following snippet as `jumpstarter-controller.authenticationConfiguration` during Jumpstarter installation.

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

Then proceed to create clients and exporters with the `jmp admin create` commands, set their corresponding OIDC username with the `--oidc-username` flag, e.g. `jmp admin create exporter test-exporter --oidc-username dex:system:serviceaccount:default:test-service-account`. Just prefix the full service account name with "dex:", as previously configured.

Finally, instruct the users to login with the following commands in pods configured with proper service accounts.

```
# for clients
jmp client login <client alias> --endpoint <jumpstarter controller endpoint> \
  --namespace <namespace> --name <client name> \
  --issuer https://dex.dex.svc.cluster.local:5556 \
  --connector-id kubernetes \
  --token $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)

# for exporters
jmp exporter login <exporter alias> --endpoint <jumpstarter controller endpoint> \
  --namespace <namespace> --name <exporter name> \
  --issuer https://dex.dex.svc.cluster.local:5556 \
  --connector-id kubernetes \
  --token $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
```

## Reference
```yaml
#
# CAUTION: this is an example configuration.
#          Do not use this for your own cluster!
#
apiVersion: jumpstarter.dev/v1alpha1
kind: AuthenticationConfiguration
# list of authenticators to authenticate Jumpstarter users using OIDC issued JWT tokens.
jwt:
- issuer:
    # URL of the provider that allows the API server to discover public signing keys.
    # Only URLs that use the https:// scheme are accepted.
    url: https://example.com
    # discoveryURL, if specified, overrides the URL used to fetch discovery
    # information instead of using "{url}/.well-known/openid-configuration".
    # The exact value specified is used, so "/.well-known/openid-configuration"
    # must be included in discoveryURL if needed.
    #
    # The "issuer" field in the fetched discovery information must match the "issuer.url" field
    # in the AuthenticationConfiguration and will be used to validate the "iss" claim in the presented JWT.
    # This is for scenarios where the well-known and jwks endpoints are hosted at a different
    # location than the issuer (such as locally in the cluster).
    # discoveryURL must be different from url if specified and must be unique across all authenticators.
    discoveryURL: https://discovery.example.com/.well-known/openid-configuration
    # PEM encoded CA certificates used to validate the connection when fetching
    # discovery information. If not set, the system verifier will be used.
    certificateAuthority: <PEM encoded CA certificates>
    # audiences is the set of acceptable audiences the JWT must be issued to.
    # At least one of the entries must match the "aud" claim in presented JWTs.
    audiences:
    - my-app
    - my-other-app
    # this is required to be set to "MatchAny" when multiple audiences are specified.
    audienceMatchPolicy: MatchAny
  # rules applied to validate token claims to authenticate users.
  claimValidationRules:
    # A key=value pair that describes a required claim in the JWT Token.
    # If set, the claim is verified to be present in the JWT Token with a matching value.
  - claim: hd
    requiredValue: example.com
    # Instead of claim and requiredValue, you can use expression to validate the claim.
    # expression is a CEL expression that evaluates to a boolean.
    # all the expressions must evaluate to true for validation to succeed.
  - expression: 'claims.hd == "example.com"'
    # Message customizes the error message seen in the API server logs when the validation fails.
    message: the hd claim must be set to example.com
  - expression: 'claims.exp - claims.nbf <= 86400'
    message: total token lifetime must not exceed 24 hours
  claimMappings:
    # username represents an option for the username attribute.
    # This is the only required attribute.
    username:
      # JWT claim to use as the user name.
      # By default sub, which is expected to be a unique identifier of the end user.
      # Admins can choose other claims, such as email or name, depending on their provider.
      # However, claims other than email should be prefixed to prevent naming clashes with other authenticators.
      # Mutually exclusive with username.expression.
      claim: "sub"
      # Prefix prepended to username claims to prevent clashes with existing names (such as internal:users).
      # For example, the value oidc: will create usernames like oidc:jane.doe.
      # if username.claim is set, username.prefix is required.
      # Explicitly set it to "" if no prefix is desired.
      # Mutually exclusive with username.expression.
      prefix: ""
      # expression is a CEL expression that evaluates to a string.
      #
      # 1.  If username.expression uses 'claims.email', then 'claims.email_verified' must be used in
      #     username.expression or extra[*].valueExpression or claimValidationRules[*].expression.
      #     An example claim validation rule expression that matches the validation automatically
      #     applied when username.claim is set to 'email' is 'claims.?email_verified.orValue(true)'.
      # 2.  If the username asserted based on username.expression is the empty string, the authentication
      #     request will fail.
      # Mutually exclusive with username.claim and username.prefix.
      expression: 'claims.username + ":external-user"'
    # groups represents an option for the groups attribute.
    groups:
      # JWT claim to use as the user's group. If the claim is present it must be an array of strings.
      # Mutually exclusive with groups.expression.
      claim: "sub"
      # Prefix prepended to group claims to prevent clashes with existing names (such as system:groups).
      # For example, the value oidc: will create group names like oidc:engineering and oidc:infra.
      # if groups.claim is set, groups.prefix is required.
      # Explicitly set it to "" if no prefix is desired.
      # Mutually exclusive with groups.expression.
      prefix: ""
      # expression is a CEL expression that evaluates to a string or a list of strings.
      # Mutually exclusive with groups.claim and groups.prefix.
      expression: 'claims.roles.split(",")'
    # uid represents an option for the uid attribute.
    uid:
      # Mutually exclusive with uid.expression.
      claim: 'sub'
      # Mutually exclusive with uid.claim
      # expression is a CEL expression that evaluates to a string.
      expression: 'claims.sub'
    # extra attributes to be added to the UserInfo object. Keys must be domain-prefix path and must be unique.
    extra:
      # key is a string to use as the extra attribute key.
      # key must be a domain-prefix path (e.g. example.org/foo). All characters before the first "/" must be a valid
      # subdomain as defined by RFC 1123. All characters trailing the first "/" must
      # be valid HTTP Path characters as defined by RFC 3986.
      # k8s.io, kubernetes.io and their subdomains are reserved for Kubernetes use and cannot be used.
      # key must be lowercase and unique across all extra attributes.
    - key: 'example.com/tenant'
      # valueExpression is a CEL expression that evaluates to a string or a list of strings.
      valueExpression: 'claims.tenant'
  # validation rules applied to the final user object.
  userValidationRules:
    # expression is a CEL expression that evaluates to a boolean.
    # all the expressions must evaluate to true for the user to be valid.
  - expression: "!user.username.startsWith('system:')"
    # Message customizes the error message seen in the API server logs when the validation fails.
    message: 'username cannot used reserved system: prefix'
  - expression: "user.groups.all(group, !group.startsWith('system:'))"
    message: 'groups cannot used reserved system: prefix'
```
