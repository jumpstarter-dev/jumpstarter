# TMT Driver

`jumpstarter-driver-tmt` provides functionality for running TMT (Test Management Tool) commands locally while connecting to remote devices via SSH network connections. This driver allows you to execute TMT test plans and commands that provision and test remote hardware through SSH connections.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-tmt
```

## Configuration

Example configuration:

```yaml
export:
  tmt:
    type: jumpstarter_driver_tmt.driver.TMT
    config:
      reboot_cmd: "j power cycle"
      default_username: "root"
      default_password: "somePassword"
    children:
      ssh:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          host: "192.168.1.100"
          port: 22
          enable_address: true
```

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| reboot_cmd | Command to reboot the target device | str | no | "j power cycle" |
| default_username | Default username for SSH connections | str | no | "" |
| default_password | Default password for SSH connections | str | no | "" |

### Required Children

| Child | Description | Required |
|-------|-------------|----------|
| ssh | Network TCP driver instance for SSH connection | yes |

## Usage

### CLI Commands

The TMT driver provides a CLI command `tmt` that allows you to run TMT commands locally while connecting to remote devices:

```bash
# assuming that your DUT has a power and storage driver
j power on
j storage flash ....

# Running part of your plan with tmt
j tmt --root . -c tracing=off -c arch=aarch64 -c distro=rhel-9 -c hw_target=rcar_s4 run --workdir-root /tmp/ -a -vv provision .. some other provisioning... plan -vv --name ^/podman/plans/fusa/tests$

# Use SSH port forwarding (if no direct connection to the DUT is possible)
j tmt --forward-ssh ....

# Specify custom username and password
j tmt --tmt-username root --tmt-password mypassword ...

# Raise log level of the tmt wrapper driver
j --log-level DEBUG tmt --root . -c tracing=off -c arch=aarch64 -c distro=rhel-9 -c hw_target=r
car_s4 run --workdir-root /tmp/ -a -vv provision .. some other provisioning... plan -vv --name ^/podman/plans/fusa/te
sts$
[09/22/25 13:27:18] DEBUG    Using direct SSH connection for tmt - host: 127.0.0.1, port: 2222           client.py:64
                    DEBUG    Provision to be replaced: ('provision', '..', 'some', 'other',             client.py:117
                             'provisioning...')                                                                      
                    DEBUG    Will be replaced with: ['provision', '-h', 'connect', '-g', '127.0.0.1',   client.py:118
                             '-P', '2222', '-u', 'root', '-p', '******']                                           
                    DEBUG    Running TMT command: ['tmt', '--root', '.', '-c', 'tracing=off', '-c',      client.py:74
                             'arch=aarch64', '-c', 'distro=rhel-9', '-c', 'hw_target=rcar_s4', 'run',                
                             '--workdir-root', '/tmp/', '-a', '-vv', 'provision', '-h', 'connect', '-g',             
                             '127.0.0.1', '-P', '2222', '-u', 'root', '-p', '******', 'plan', '-vv',               
                             '--name', '^/podman/plans/fusa/tests$']                                                 
/tmp/run-018
...
```

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--forward-ssh` | Use SSH port forwarding for connection | false |
| `--tmt-username` | Username for SSH connections | from config |
| `--tmt-password` | Password for SSH connections | from config |
| `--tmt-cmd` | TMT command to execute | "tmt" |
| `--tmt-on-exporter` | Run TMT on the exporter (not implemented) | false |

### Provision Arguments Handling

The driver automatically handles TMT provision arguments by:

1. **Detecting provision sections**: Looks for `provision` or `run` commands in the TMT arguments
2. **Replacing connection details**: Automatically replaces or adds SSH connection parameters (`-h connect -g <host> -P <port> -u <username> -p <password>`)
3. **Preserving other arguments**: Keeps all other TMT arguments intact

Example of how arguments are transformed:
```bash
# Input command
j tmt run --name /my/test/plan provision -h connect -g 192.168.1.100 -P 22

# Automatically transformed to use SSH connection
# TMT receives: run --name /my/test/plan provision -h connect -g <forwarded_host> -P <forwarded_port> -u root -p password
```
