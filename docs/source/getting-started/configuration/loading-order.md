# Loading Order

Jumpstarter uses a hierarchical approach to loading configuration, allowing you
to override settings at different levels.

## Configuration Sources

Jumpstarter loads configuration from the following sources, in order of
precedence (highest to lowest):

1. **Command-line arguments** - Highest priority, override all other settings
2. **Config files** - Client and {term}`exporter` configs in
   `${HOME}/.config/jumpstarter/`, and {term}`exporter` configs in
   `/etc/jumpstarter/exporters/`
3. **Environment variables** - Supply settings that the config file
   leaves out

`JMP_RETRY_TIMEOUT`, `JMP_DIAL_TIMEOUT` and `JMP_GRPC_INSECURE` are exceptions:
they take precedence over the config file. Environment variables can
also replace the current client entirely, as described below.

## Client Config Hierarchy

For client operations, Jumpstarter selects the client config in this
order:

1. **Command-line options** `--client <alias>` or `--client-config <path>`
2. **Environment variables**, when both `JMP_NAME` and `JMP_NAMESPACE` are set:
   the configuration is built from `JMP_*` variables such as `JMP_ENDPOINT` and
   `JMP_TOKEN`
3. **Current client** defined in `${HOME}/.config/jumpstarter/config.yaml`,
   loaded from `${HOME}/.config/jumpstarter/clients/<alias>.yaml`

The `jmp shell` options `--retry-timeout` and `--dial-timeout` override the
`JMP_RETRY_TIMEOUT` and `JMP_DIAL_TIMEOUT` variables, which override the
`leases` section of the config file.

## Exporter Config Hierarchy

For {term}`exporter` operations, `jmp run` loads the configuration named by one
of these command-line options:

1. `--exporter <alias>` loads `${HOME}/.config/jumpstarter/exporters/<alias>.yaml`,
   or `/etc/jumpstarter/exporters/<alias>.yaml` when the user file does not exist
2. `--exporter-config <path>` loads that file

The {term}`exporter` reads its endpoint and token only from the file.
`JMP_NAMESPACE` and `JMP_NAME` are used only when `metadata` in the file leaves
them out.

## Example

Here's a practical example of how configuration overrides work:

1. You create a client config file at
   `${HOME}/.config/jumpstarter/clients/default.yaml`:

   ```yaml
   leases:
     dial_timeout: 60
   ```

2. You set an environment variable in your terminal:

   ```console
   $ export JMP_DIAL_TIMEOUT=120
   ```

3. You run a command with an explicit option:

   ```console
   $ jmp shell --selector example.com/board=rpi4 --dial-timeout 3m
   ```

Jumpstarter waits up to three minutes for the {term}`exporter` to become ready,
because the command-line option has the highest priority. Without
`--dial-timeout` it waits 120 seconds, the value from the environment.

## Use Cases

Choose the appropriate configuration method based on your needs:

- **Development**: Use user config files for personal settings
- **CI/CD Pipelines**: Use environment variables for automation
- **One-off Tasks**: Use command-line arguments for temporary changes
- **System Defaults**: Use `/etc/jumpstarter/exporters/` for {term}`exporter`
  configs shared across users

This hierarchical approach allows Jumpstarter to be flexible across different
usage scenarios while maintaining consistent behavior.
