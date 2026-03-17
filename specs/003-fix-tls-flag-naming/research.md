# Research: Click Flag Deprecation and Aliasing Patterns

**Feature**: 003-fix-tls-flag-naming
**Date**: 2026-03-17

## Click Option Deprecation Strategies

### Strategy 1: Custom Click Option Class with Deprecation Warning

Click does not have a built-in deprecation mechanism for options. The standard
approach is to create a custom `click.Option` subclass that intercepts the
option processing and emits a warning when the deprecated name is used.

```python
import warnings
import click

class DeprecatedOption(click.Option):
    def __init__(self, *args, deprecated_name=None, **kwargs):
        self.deprecated_name = deprecated_name
        super().__init__(*args, **kwargs)

    def type_cast_value(self, ctx, value):
        if self.deprecated_name and self.deprecated_name in (ctx.params or {}):
            warnings.warn(
                f"'{self.deprecated_name}' is deprecated, use '{self.name}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return super().type_cast_value(ctx, value)
```

**Limitation**: This approach requires tracking which CLI token was actually
used, which Click does not expose directly.

### Strategy 2: Dual Options with Callback Merging (Recommended)

Define both the old and new flag as separate Click options, where the old one
triggers a deprecation warning via a callback and maps its value into the new
parameter name.

```python
def _deprecated_flag_callback(ctx, param, value, *, new_name, old_name):
    if value:
        click.echo(
            f"Warning: '--{old_name}' is deprecated. Use '--{new_name}' instead.",
            err=True,
        )
        ctx.params[new_name.replace("-", "_")] = value
    return value

opt_insecure_tls = click.option(
    "--insecure-tls",
    "insecure_tls",
    is_flag=True,
    default=False,
    help="Disable endpoint TLS verification.",
)

opt_insecure_tls_config_deprecated = click.option(
    "--insecure-tls-config",
    "insecure_tls",          # same dest as new option
    is_flag=True,
    default=False,
    hidden=True,             # hide from --help
    help="Deprecated: use --insecure-tls instead.",
)
```

**Key insight**: Click allows multiple options to write to the same parameter
name. By using `hidden=True` the deprecated flag disappears from `--help` but
remains functional. No callback is needed for basic aliasing since both options
write to the same destination parameter.

For the deprecation warning, a callback on the hidden option can emit the
warning.

### Strategy 3: Click Parameter Secondary Names

Click options support multiple names out of the box:

```python
click.option("--insecure-tls", "--insecure-tls-config", "insecure_tls", ...)
```

However, this shows both names in `--help` output and provides no mechanism to
warn about the deprecated name. Not suitable for our use case.

## Recommended Approach

**Strategy 2** (dual options with shared destination) is the best fit because:

1. It hides the deprecated flag from `--help` via `hidden=True`.
2. It allows a deprecation warning via a callback on the hidden option.
3. It requires no custom Click subclasses, keeping the code simple.
4. It is forward-compatible: removing the deprecated option later is a one-line
   deletion.

## Existing Patterns in Jumpstarter

The codebase uses `click.option` decorators defined as module-level variables
in `opt.py` and applied as decorators on command functions. The pattern of
shared option decorators (e.g., `opt_insecure_tls_config`, `opt_nointeractive`)
is well-established.

The deprecation approach should follow this same pattern: define
`opt_insecure_tls` as the new decorator and `opt_insecure_tls_config` as a
hidden deprecated alias, both in `opt.py`.

## Files Requiring Changes

1. `python/packages/jumpstarter-cli-common/jumpstarter_cli_common/opt.py` -
   Rename option, add deprecated alias
2. `python/packages/jumpstarter-cli/jumpstarter_cli/login.py` -
   Update import and usage
3. `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/create.py` -
   Update import and usage
4. `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/import_res.py` -
   Update import and usage
5. `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/create_test.py` -
   Add tests for new name, keep tests for old name
6. `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/import_res_test.py` -
   Add tests for new name, keep tests for old name
7. `python/docs/source/getting-started/guides/setup-distributed-mode.md` -
   Update flag references
8. `python/docs/source/getting-started/configuration/authentication.md` -
   Update flag references
9. `e2e/tests.bats` - Update flag references
