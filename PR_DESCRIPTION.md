# Upgrade to Python 3.14

## Summary
Upgrades the project from Python 3.11/3.12 to Python 3.14, the latest stable release.

## Changes
- Updated `.py-version` from 3.12 to 3.14
- Updated all `requires-python` fields from `>=3.11` to `>=3.14` (65 packages)
- Updated ruff `target-version` to `py314`
- Updated GitHub Actions CI to test with Python 3.14
- Updated `uv.lock` with Python 3.14 compatible dependencies

## Dependencies
This required upgrading to newer versions of packages with Python 3.14 support:
- `pydantic-core`: 2.33.2 → 2.46.4 (has Python 3.14 wheels)
- `pydantic`: 2.11.7 → 2.13.4

## Build Requirements
Building with Python 3.14 requires:
- **Environment variable**: `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` for Rust-based Python extensions
- **macOS**: 
  - `brew install gcc` (provides gfortran for scipy)
  - `brew install openblas` (required for scipy)
  - `export PKG_CONFIG_PATH="/opt/homebrew/opt/openblas/lib/pkgconfig:$PKG_CONFIG_PATH"`
- **Linux**: Install equivalent packages via system package manager

## Testing
- ✅ Successfully synced all packages with `uv sync --all-packages --all-extras`
- ✅ Verified `jmp` CLI works correctly
- ✅ Python 3.14.5 confirmed running

## Notes
Python 3.14 was released recently, so some packages in the ecosystem are still catching up. The forward compatibility flag allows PyO3-based packages to build using the stable ABI.

## Checklist
- [x] All `pyproject.toml` files updated
- [x] CI configuration updated
- [x] Dependencies resolved and lockfile updated
- [x] Local testing completed
- [ ] CI passes
