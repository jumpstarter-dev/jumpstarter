*** Settings ***
Documentation       Power the mock board and talk to it over its serial console.
...
...                 ``Dut Power ...`` keywords come from the board's power driver, at
...                 ``dut.power``, through the generated ``client.resource``.
...                 ``Open Console`` and the ``Console ...`` keywords work on any driver
...                 byte stream.

Resource            ../client.resource
Test Teardown       Close All Consoles


*** Test Cases ***
Board Boots To Login Prompt
    Dut Power Off
    Open Console    dut.console    prompt=root@mockdut:~#${SPACE}
    Dut Power On
    ${boot}=    Console Login    root    not-a-secret    timeout=30s
    Should Contain    ${boot}    Starting kernel
    ${kernel}=    Console Execute Command    uname -r
    Should Be Equal    ${kernel}    6.6.0-mock

Power Status Follows The Switch
    Dut Power Off
    Dut Power Should Be Off
    Dut Power On
    Dut Power Should Be On
