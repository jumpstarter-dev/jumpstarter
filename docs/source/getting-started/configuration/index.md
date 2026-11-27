# Configuration

This section explains how to configure Jumpstarter for your environment.

- [Files](files.md): Understanding the structure and content of configuration
  files
- [Loading Order](loading-order.md): Understanding how configuration files are
  prioritized from different sources (environment variables, command line,
  system and user config files)
- [Authentication](authentication.md): Setting up OIDC and managing tokens
- [Observability](observability.md): Prometheus autodiscovery, starter alerts,
  and cluster log-shipper integration for Controller and Router
- [Dashboards and Alerting](dashboards-alerting.md): Perses dashboards,
  datasource prerequisites, and dashboard validation

For a list of supported configuration options including an explanation please
refer to the [MAN pages](../../reference/man-pages/index.md).

```{toctree}
:maxdepth: 1
:hidden:
files.md
loading-order.md
authentication.md
observability.md
dashboards-alerting.md
```