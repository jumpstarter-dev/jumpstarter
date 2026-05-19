# Testing Frameworks

## pytest Integration

Jumpstarter integrates with pytest through the `jumpstarter-testing` package:

```python
from jumpstarter_testing.pytest import JumpstarterTest

class TestMyDevice(JumpstarterTest):
    # Optional: specify which exporter to use based on labels
    exporter_selector = "vendor=acme,model=widget-v2"

    def test_power_cycle(self):
        # Access the device driver through the provided client
        self.client.power.on()
        assert self.client.serial.read_until("boot complete") is not None
        self.client.power.off()
```

## Robot Framework Integration

For teams using Robot Framework, Jumpstarter drivers can be exposed as keywords:

```robotframework
*** Settings ***
Library    JumpstarterLibrary

*** Test Cases ***
Device Boot Test
    Connect To Exporter    selector=vendor=acme,model=widget-v2
    Power On
    ${output}=    Read Serial Until    boot complete
    Should Not Be Empty    ${output}
    Power Off
```
