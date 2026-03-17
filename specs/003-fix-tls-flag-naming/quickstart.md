# Quickstart: Fix TLS Flag Naming

**Feature**: 003-fix-tls-flag-naming
**Date**: 2026-03-17

## What Changed

The `--insecure-tls-config` flag has been renamed to `--insecure-tls` across
all commands. The old name still works but prints a deprecation warning.

## Before and After

### Before

```bash
jmp login my-client@login.example.com --insecure-tls-config
jmp admin create client my-client --insecure-tls-config
jmp admin create exporter my-exporter --insecure-tls-config
```

### After

```bash
jmp login my-client@login.example.com --insecure-tls
jmp admin create client my-client --insecure-tls
jmp admin create exporter my-exporter --insecure-tls
```

## Migration Guide

1. Search your scripts for `--insecure-tls-config` and replace with
   `--insecure-tls`.
2. No other flags changed. `--insecure-login-tls` and `--insecure-login-http`
   remain the same.
3. Old flag will continue to work during the deprecation period but will emit:
   ```
   Warning: '--insecure-tls-config' is deprecated. Use '--insecure-tls' instead.
   ```

## Verification

```bash
# Verify new flag appears in help
jmp login --help | grep insecure-tls

# Verify old flag still works (with deprecation warning)
jmp login my-client@login.example.com --insecure-tls-config 2>&1 | grep "deprecated"
```

## Unchanged Flags

The following flags were already well-named and remain unchanged:

- `--insecure-login-tls` -- skip TLS verification for login endpoint
- `--insecure-login-http` -- use HTTP instead of HTTPS for login endpoint
