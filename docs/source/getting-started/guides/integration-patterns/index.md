# Integration Patterns

Common patterns for incorporating Jumpstarter into your development and testing
workflows.

- [CI/CD](cicd.md): Pipeline configs for GitHub Actions, GitLab CI, and other
  CI/CD systems
- [Development](development.md): Local and cloud-native development setups
- [Agentic](agentic.md): Give AI assistants that support local {term}`MCP`
  servers access to hardware, under your identity and {term}`access policies <access policy>`
- [Observability](observability.md): Metrics, logs, and the optional telemetry
  service
- [UI Automation](ui-automation.md): Drive iOS and Android targets through one
  Appium interface
- [Virtual Testing](virtual-testing.md): Create VMs on a remote KubeVirt cluster
  using the `kubevirt.jumpstarter.dev` {term}`provisioner`
- [Best Practices](practices.md): Labeling, security, and resource management

```{toctree}
:maxdepth: 1
:hidden:
cicd.md
development.md
agentic.md
observability.md
ui-automation.md
virtual-testing.md
practices.md
```
