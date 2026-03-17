# Data Model: Flag Mapping

**Feature**: 003-fix-tls-flag-naming
**Date**: 2026-03-17

## Flag Rename Mapping

| Old CLI Flag             | New CLI Flag             | Python Parameter (old)   | Python Parameter (new) | Scope       | Action        |
|--------------------------|--------------------------|--------------------------|------------------------|-------------|---------------|
| `--insecure-tls-config`  | `--insecure-tls`         | `insecure_tls_config`    | `insecure_tls`         | All commands | Rename + deprecate old |
| `--insecure-login-tls`   | `--insecure-login-tls`   | `insecure_login_tls`     | `insecure_login_tls`   | `jmp login`  | Keep as-is    |
| `--insecure-login-http`  | `--insecure-login-http`  | `insecure_login_http`    | `insecure_login_http`  | `jmp login`  | Keep as-is    |

## Shared Option Decorators

| Decorator Variable (old)       | Decorator Variable (new) | Defined In         |
|--------------------------------|--------------------------|--------------------|
| `opt_insecure_tls_config`      | `opt_insecure_tls`       | `opt.py`           |
| (new, hidden)                  | `opt_insecure_tls_config`| `opt.py` (deprecated alias) |

## Commands Affected

| Command               | File                                | Uses Flag            |
|-----------------------|-------------------------------------|----------------------|
| `jmp login`           | `jumpstarter_cli/login.py`          | `--insecure-tls`     |
| `jmp admin create client` | `jumpstarter_cli_admin/create.py` | `--insecure-tls` |
| `jmp admin create exporter` | `jumpstarter_cli_admin/create.py` | `--insecure-tls` |
| `jmp admin import client` | `jumpstarter_cli_admin/import_res.py` | `--insecure-tls` |
| `jmp admin import exporter` | `jumpstarter_cli_admin/import_res.py` | `--insecure-tls` |

## Helper Function Impact

| Function               | File     | Parameter Change                           |
|------------------------|----------|--------------------------------------------|
| `confirm_insecure_tls` | `opt.py` | `insecure_tls_config` -> `insecure_tls`    |

## Deprecation Behavior

When `--insecure-tls-config` is used:

1. The flag is accepted (hidden from `--help`).
2. A warning is printed to stderr:
   `Warning: '--insecure-tls-config' is deprecated. Use '--insecure-tls' instead.`
3. The value is written to the same destination parameter `insecure_tls`.
4. Command execution proceeds normally.
