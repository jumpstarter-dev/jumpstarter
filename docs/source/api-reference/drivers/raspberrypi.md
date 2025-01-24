# Raspberry Pi drivers

Raspberry Pi drivers are a set of drivers for the various peripherals on Pi and similar single board computers.

## Driver configuration
```yaml
export:
  my_serial:
    type: "jumpstarter_driver_raspberrypi.driver.DigitalIO"
    config:
      pin: "D3"
```

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| pin | Name of the GPIO pin to connect to, in [Adafruit Blinka format](https://docs.circuitpython.org/projects/blinka/en/latest/index.html#usage-example) | str | yes | |

## DigitalIOClient API
```{eval-rst}
.. autoclass:: jumpstarter_driver_raspberrypi.client.DigitalIOClient
    :members:
```

## Examples
Switch pin to push pull output and set output to high
```{testcode}
digitalioclient.switch_to_output(value=False, drive_mode=digitalio.DriveMode.PUSH_PULL) # default to low
digitalioclient.value = True
```

Switch pin to input with pull up and read value
```{testcode}
digitalioclient.switch_to_input(pull=digitalio.Pull.UP)
print(digitalioclient.value)
```
