### Authentication

#### Preface
Authentication in jumpstarter is implemented through k8s service account tokens.

#### Clients
Clients (either used by human users or CI jobs) are authorized to access the k8s api, likely through the jumpstarter CLI. All clients have permission to issue service account tokens for themselves under a shared service account, which can be used to authenticate against the jumpstarter controller.

For clients with permission to create/modify/delete exporters, additional RBAC rules are in place to allow them to modify jumpstarter CRDs.

#### Exporters
Exporters are not allowed to talk to the k8s api, thus clients issue long-lived service account tokens for them under a shared service account at exporter creation time.
