*** Settings ***
Documentation       The board boots and accepts a login. ``${PASSWORD}`` comes from the testbed.

Resource            ../client.resource
Test Teardown       Close All Consoles


*** Variables ***
${PASSWORD}     not-a-secret    # a testbed overrides this


*** Test Cases ***
Board Boots To Login Prompt
    Dut Power Off
    Open Console    dut.console    prompt=root@mockdut:~#${SPACE}
    Dut Power On
    Console Login    root    ${PASSWORD}    timeout=30s
    ${kernel}=    Console Execute Command    uname -r
    Should Not Be Empty    ${kernel}
