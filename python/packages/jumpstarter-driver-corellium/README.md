# Corellium Driver

`jumpstarter-driver-corellium` provides functionality for interacting with
[Corellium](https://corellium.com) virtualization platform.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-corellium
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-corellium/examples/config.yaml
:language: yaml
```

### ExporterConfig Example

You can run an exporter by running: `jmp exporter shell -c $file`:

```{literalinclude} ../../../../../packages/jumpstarter-driver-corellium/examples/config_exporterconfig_example.yaml
:language: yaml
```

```{literalinclude} ../../../../../packages/jumpstarter-driver-corellium/examples/config_exporterconfig_example_1.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_corellium.driver.Corellium()
```
