# Contract: CLI Flag Short Aliases

**Branch**: `008-short-flag-aliases` | **Date**: 2026-03-17

## Contract Definition

Each entry below specifies a Click option that MUST be modified to include
a short alias. The contract is verified by unit tests that invoke the
command with the short flag and assert identical behavior to the long flag.

### C-001: `get leases --all` gains `-a`

**File**: `python/packages/jumpstarter-cli/jumpstarter_cli/get.py`
**Line**: `@click.option("--all", ...)`
**Change**: Add `"-a"` as first positional argument to `click.option`.

Before:
```python
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Show all leases including expired ones"
)
```

After:
```python
@click.option(
    "-a",
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Show all leases including expired ones"
)
```

**Test**: Invoke `get_leases` with `["-a"]` via Click test runner and
verify `show_all` is `True`.

---

### C-002: `delete leases --all` gains `-a`

**File**: `python/packages/jumpstarter-cli/jumpstarter_cli/delete.py`
**Line**: `@click.option("--all", "all", is_flag=True)`
**Change**: Add `"-a"` as first positional argument.

Before:
```python
@click.option("--all", "all", is_flag=True)
```

After:
```python
@click.option("-a", "--all", "all", is_flag=True)
```

**Test**: Invoke `delete_leases` with `["-a"]` via Click test runner and
verify `all` parameter is `True`.

---

### C-003: `auth status --verbose` gains `-v`

**File**: `python/packages/jumpstarter-cli/jumpstarter_cli/auth.py`
**Line**: `@click.option("--verbose", is_flag=True, ...)`
**Change**: Add `"-v"` as first positional argument.

Before:
```python
@click.option("--verbose", is_flag=True, help="Show additional token details")
```

After:
```python
@click.option("-v", "--verbose", is_flag=True, help="Show additional token details")
```

**Test**: Invoke `token_status` with `["-v"]` via Click test runner and
verify `verbose` is `True`.
