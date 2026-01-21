# Loading Order

Jumpstarter uses a hierarchical approach to loading configuration, allowing you
to override settings at different levels.

## Configuration Sources

Jumpstarter loads configuration from the following sources, in order of
precedence (highest to lowest):

1. **Command-line arguments** - Highest priority, override all other settings
2. **Environment variables** - Override file-based configurations
3. **User configuration files** - Located in `${HOME}/.config/jumpstarter/`
4. **System configuration files** - Located in `/etc/jumpstarter/`

## Client Configuration Hierarchy

For client operations, Jumpstarter processes configurations in this order:

1. **Command-line options** such as `--endpoint` or `--client-config`
2. **Environment variables** such as `JMP_ENDPOINT`, `JMP_TOKEN`, or
   `JMP_CLIENT_CONFIG`
3. **Current client** defined in `${HOME}/.config/jumpstarter/config.yaml`
4. **Specific client file** in `${HOME}/.config/jumpstarter/clients/<n>.yaml`

## Exporter Configuration Hierarchy

For exporter operations, Jumpstarter processes configurations in this order:

1. **Command-line options** such as `--exporter` or `--exporter-config`
2. **Environment variables** such as `JMP_ENDPOINT`, `JMP_TOKEN`, or
   `JMP_NAMESPACE`
3. **Specific exporter file** in `/etc/jumpstarter/exporters/<n>.yaml`

## Example

Here's a practical example of how configuration overrides work:

1. You create a client configuration file at
   `${HOME}/.config/jumpstarter/clients/default.yaml`:

   ```yaml
   endpoint: "jumpstarter1.my-lab.com:1443"
   ```

2. You set an environment variable in your terminal:

   ```console
   $ export JMP_ENDPOINT="jumpstarter2.my-lab.com:1443"
   ```

3. You run a command with an explicit endpoint argument:

   ```console
   $ jmp --endpoint jumpstarter3.my-lab.com:1443 info
   ```

Jumpstarter connects to `jumpstarter3.my-lab.com:1443` because the command-line
argument has the highest priority.

## Use Cases

Choose the appropriate configuration method based on your needs:

- **Development**: Use user config files for personal settings
- **CI/CD Pipelines**: Use environment variables for automation
- **One-off Tasks**: Use command-line arguments for temporary changes
- **System Defaults**: Use system config files for shared settings across users

This hierarchical approach allows Jumpstarter to be flexible across different
usage scenarios while maintaining consistent behavior.
