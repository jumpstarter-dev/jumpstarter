# Jumpstarter Python

The Python implementation of [Jumpstarter](https://jumpstarter.dev): client
libraries, the `jmp` CLI, hardware drivers, and the testing framework. This
directory is managed as a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/).

## Development

Run these from the repository root:

```sh
make -C python build
make -C python test
make -C python lint-fix

uv run --directory python ruff check .
uv run --directory python ruff format .
```
