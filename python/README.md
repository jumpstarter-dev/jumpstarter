# Jumpstarter Python

The Python implementation of [Jumpstarter](https://jumpstarter.dev): client
libraries, the `jmp` CLI, hardware drivers, and the testing framework. This
directory is managed as a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/).

## Development

```sh
make build-python
make test
make lint-fix

uv run ruff check .
uv run ruff format .
```
