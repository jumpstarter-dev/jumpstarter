*** Settings ***
Documentation       Reach drivers by path: methods, context managers, streams and properties.
...
...                 These keywords take a driver path, so they work with or without a
...                 client resource, on any client method, including ones without a
...                 keyword of their own.

Library             jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/../../basic/exporter.yaml


*** Test Cases ***
Call Any Client Method
    [Documentation]    Text arguments are converted to the method's annotated types.
    Call Driver    dut.power    cycle    wait=0
    ${status}=    Call Driver    dut.power    status
    Should Be Equal    ${status}    on

Context Manager Stays Open Until Closed
    [Documentation]    ``Open Driver Context`` enters a client context manager, here the
    ...    power client's ``log_stream``, and keeps it open until ``Close Driver Context``
    ...    or the end of its scope.
    Open Driver Context    dut.power    log_stream    alias=logs
    Call Driver    dut.power    on
    Close Driver Context    logs

Collect A Few Items From A Stream
    Call Driver    dut.power    on
    ${items}=    Collect Driver Stream    dut.power    read    count=2
    Length Should Be    ${items}    2
    Should Be True    ${items[0].voltage} > 0

Read A Client Property
    ${description}=    Get Driver Property    dut.power    description
    Log    ${description}
