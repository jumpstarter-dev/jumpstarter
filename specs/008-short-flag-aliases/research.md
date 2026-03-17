# Research: Short Flag Aliases Audit

**Branch**: `008-short-flag-aliases` | **Date**: 2026-03-17

## Audit Methodology

Searched all `click.option` declarations across the four CLI packages
(`jumpstarter-cli`, `jumpstarter-cli-admin`, `jumpstarter-cli-common`,
`jumpstarter-cli-driver`) and catalogued every flag, its current short
alias (if any), and its containing command.

## Current Flag Inventory

### jumpstarter-cli-common (shared options)

| Long Flag              | Short | Variable            | Used By                          |
|------------------------|-------|---------------------|----------------------------------|
| `--log-level`          | --    | `log_level`         | All commands (top-level)         |
| `--kubeconfig`         | --    | `kubeconfig`        | Admin k8s commands               |
| `--context`            | --    | `context`           | Admin k8s commands               |
| `--namespace`          | `-n`  | `namespace`         | Admin k8s commands               |
| `--label`              | `-l`  | `labels`            | Admin create commands            |
| `--insecure-tls-config`| --    | `insecure_tls_config`| login                           |
| `--output`             | `-o`  | `output`            | Many commands                    |
| `--nointeractive`      | --    | `nointeractive`     | login                            |

### jumpstarter-cli (main CLI)

| Command          | Long Flag              | Short | Variable             | Conflict? |
|------------------|------------------------|-------|----------------------|-----------|
| `get leases`     | `--all`                | --    | `show_all`           | No        |
| `get exporters`  | `--with`               | --    | `with_options`       | No        |
| `delete leases`  | `--all`                | --    | `all`                | No        |
| `shell`          | `--lease`              | --    | `lease_name`         | No        |
| `shell`          | `--exporter-logs`      | --    | `exporter_logs`      | No        |
| `shell`          | `--duration`           | --    | `duration`           | No        |
| `shell`          | `--acquisition-timeout`| --    | `acquisition_timeout`| No        |
| `create lease`   | `--lease-id`           | --    | `lease_id`           | No        |
| `create lease`   | `--duration`           | --    | `duration`           | No        |
| `create lease`   | `--begin-time`         | --    | `begin_time`         | No        |
| `update lease`   | `--duration`           | --    | `duration`           | No        |
| `update lease`   | `--begin-time`         | --    | `begin_time`         | No        |
| `auth status`    | `--verbose`            | --    | `verbose`            | No        |
| `login`          | `--endpoint`           | `-e`  | `endpoint`           | --        |
| `login`          | `--allow`              | --    | `allow`              | No        |
| `login`          | `--unsafe`             | --    | `unsafe`             | No        |
| `login`          | `--insecure-login-tls` | --    | `insecure_login_tls` | No        |
| `login`          | `--insecure-login-http`| --    | `insecure_login_http`| No        |
| `config client create` | `--endpoint`      | `-e`  | `endpoint`           | --        |
| `config client create` | `--token`          | `-t`  | `token`              | --        |
| `config client create` | `--allow`          | `-a`  | `allow`              | --        |
| `config client create` | `--unsafe`         | --    | `unsafe`             | No        |
| `common`         | `--selector`           | `-l`  | `selector`           | --        |
| `common`         | `--name`               | `-n`  | `exporter_name`      | --        |

### jumpstarter-cli-admin

| Command          | Long Flag              | Short | Variable             | Conflict? |
|------------------|------------------------|-------|----------------------|-----------|
| `get exporter`   | `--devices`            | `-d`  | `devices`            | --        |
| `get cluster(s)` | `--type`               | --    | `type`               | No        |
| `get cluster(s)` | `--kubectl`            | --    | `kubectl`            | No        |
| `get cluster(s)` | `--helm`               | --    | `helm`               | No        |
| `get cluster(s)` | `--kind`               | --    | `kind`               | No        |
| `get cluster(s)` | `--minikube`           | --    | `minikube`           | No        |
| `install`        | `--chart`              | `-c`  | `chart`              | --        |
| `install`        | `--namespace`          | `-n`  | `namespace`          | --        |
| `install`        | `--ip`                 | `-i`  | `ip`                 | --        |
| `install`        | `--basedomain`         | `-b`  | `basedomain`         | --        |
| `install`        | `--grpc-endpoint`      | `-g`  | `grpc_endpoint`      | --        |
| `install`        | `--router-endpoint`    | `-r`  | `router_endpoint`    | --        |
| `install`        | `--version`            | `-v`  | `version`            | --        |
| `install`        | `--values`             | `-f`  | `values`             | --        |
| `create cluster` | `--namespace`          | `-n`  | `namespace`          | --        |
| `create cluster` | `--ip`                 | `-i`  | `ip`                 | --        |
| `create cluster` | `--basedomain`         | `-b`  | `basedomain`         | --        |
| `create cluster` | `--grpc-endpoint`      | `-g`  | `grpc_endpoint`      | --        |
| `create cluster` | `--router-endpoint`    | `-r`  | `router_endpoint`    | --        |
| `create cluster` | `--version`            | `-v`  | `version`            | --        |
| `create cluster` | `--values`             | `-f`  | `values`             | --        |
| `create client`  | `--allow`              | `-a`  | `allow`              | --        |
| `delete cluster` | `--force`              | --    | `force`              | No        |
| `import`         | `--allow`              | `-a`  | `allow`              | --        |

## Recommendations

### High confidence -- add these short aliases

| Command          | Long Flag   | Proposed Short | Rationale                        |
|------------------|-------------|---------------|----------------------------------|
| `get leases`     | `--all`     | `-a`          | Explicitly requested; no conflict|
| `delete leases`  | `--all`     | `-a`          | Consistency with `get leases`    |
| `auth status`    | `--verbose` | `-v`          | Universal CLI convention         |

### Considered but rejected

| Command          | Long Flag              | Candidate | Rejection Reason                             |
|------------------|------------------------|-----------|----------------------------------------------|
| `shell`          | `--exporter-logs`      | `-e`      | Conflicts: `-e` is `--endpoint` on login     |
| `shell`          | `--duration`           | `-d`      | Might confuse with `-d` (devices) in admin   |
| `login`          | `--allow`              | `-a`      | Not adding to login to avoid confusion       |
| `get cluster(s)` | `--type`               | `-t`      | `-t` used for `--token` in other commands    |
| `delete cluster` | `--force`              | `-f`      | `-f` used for `--values` in install; risky   |

### Conflict Analysis: `-a` across commands

The letter `-a` is already used for `--allow` in:
- `config client create` (jumpstarter-cli)
- `create client` (jumpstarter-cli-admin)
- `import` (jumpstarter-cli-admin)

These are different Click command groups, so there is no technical
conflict. Each Click command has its own option namespace. Using `-a` for
`--all` on `get leases` and `delete leases` is safe because neither of
those commands has an `--allow` option.
