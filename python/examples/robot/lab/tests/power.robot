*** Settings ***
Documentation       Power control and measurements.

Resource            ../client.resource


*** Test Cases ***
Power Status Follows The Switch
    Dut Power Off
    Dut Power Should Be Off
    Dut Power On
    Dut Power Should Be On

Power Readings Are Sane
    Dut Power On
    ${readings}=    Dut Power Read    max_items=3
    Length Should Be    ${readings}    3
    Should Be True    ${readings[0].voltage} > 0
