# Contract: CLI Flag Names and Deprecation Behavior

**Feature**: 003-fix-tls-flag-naming
**Date**: 2026-03-17

## New Flag Definition

### `--insecure-tls` (replaces `--insecure-tls-config`)

- **CLI name**: `--insecure-tls`
- **Parameter name**: `insecure_tls`
- **Type**: Boolean flag (`is_flag=True`)
- **Default**: `False`
- **Help text**: "Disable endpoint TLS verification. This is insecure and should only be used for testing purposes."
- **Available on**: `jmp login`, `jmp admin create client`, `jmp admin create exporter`, `jmp admin import client`, `jmp admin import exporter`

### `--insecure-tls-config` (deprecated alias)

- **CLI name**: `--insecure-tls-config`
- **Parameter name**: `insecure_tls` (shared with `--insecure-tls`)
- **Type**: Boolean flag (`is_flag=True`)
- **Default**: `False`
- **Hidden**: `True` (not shown in `--help`)
- **Help text**: "Deprecated: use --insecure-tls instead."
- **Behavior**: When used, emits deprecation warning to stderr before proceeding.

### `--insecure-login-tls` (unchanged)

- **CLI name**: `--insecure-login-tls`
- **Parameter name**: `insecure_login_tls`
- **Type**: Boolean flag
- **Default**: `False`
- **Help text**: "Skip TLS certificate verification when fetching config from login endpoint."
- **Available on**: `jmp login` only

### `--insecure-login-http` (unchanged)

- **CLI name**: `--insecure-login-http`
- **Parameter name**: `insecure_login_http`
- **Type**: Boolean flag
- **Default**: `False`
- **Help text**: "Use HTTP instead of HTTPS when fetching config from login endpoint (for local testing)."
- **Available on**: `jmp login` only

## Deprecation Warning Format

```
Warning: '--insecure-tls-config' is deprecated. Use '--insecure-tls' instead.
```

Output destination: stderr (via `click.echo(..., err=True)`)

## Deprecation Timeline

- **Current release**: Both `--insecure-tls` and `--insecure-tls-config` work.
  The old name emits a warning.
- **Next minor release**: Old name continues to work with warning (minimum one
  release cycle).
- **Future major release**: Old name may be removed.

## Click Implementation Pattern

```python
def _deprecated_insecure_tls_callback(ctx, param, value):
    if value:
        click.echo(
            "Warning: '--insecure-tls-config' is deprecated. "
            "Use '--insecure-tls' instead.",
            err=True,
        )
    return value

opt_insecure_tls = click.option(
    "--insecure-tls",
    "insecure_tls",
    is_flag=True,
    default=False,
    help="Disable endpoint TLS verification. This is insecure and "
         "should only be used for testing purposes.",
)

opt_insecure_tls_config = click.option(
    "--insecure-tls-config",
    "insecure_tls",
    is_flag=True,
    default=False,
    hidden=True,
    callback=_deprecated_insecure_tls_callback,
    expose_value=False,
    help="Deprecated: use --insecure-tls instead.",
)
```

Both decorators MUST be applied to every command that supports this flag, with
`opt_insecure_tls_config` (deprecated) applied before `opt_insecure_tls` (new)
in the decorator stack to ensure proper parameter resolution.
