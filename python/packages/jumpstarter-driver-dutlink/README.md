# DUT Link Driver

`jumpstarter-driver-dutlink` provides functionality for interacting with DUT
Link devices.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-dutlink
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-dutlink/examples/config.yaml
:language: yaml
```


### CLI Script

```{literalinclude} ../../../../../packages/jumpstarter-driver-dutlink/examples/dutlink.py
:language: python
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_dutlink.driver.Dutlink()
```
