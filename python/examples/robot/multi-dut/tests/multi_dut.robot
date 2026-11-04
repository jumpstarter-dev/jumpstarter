*** Settings ***
Documentation       Two identical boards on one exporter, driven by the same keywords.
...
...                 ``client.resource`` was generated with ``roles = true``: the
...                 boards' power and console drivers become *role* libraries, ``Power``
...                 and ``Console``, and ``@{MULTI_DEVICES}`` lists the boards (the list
...                 is named after the session, ``multi``). ``Use Device`` points every
...                 role library at one board; the binding resets after each test.

Resource            ../client.resource

Test Teardown       Close All Consoles


*** Test Cases ***
Every DUT Boots
    FOR    ${dut}    IN    @{MULTI_DEVICES}
        Use Device    ${dut}
        Power Off
        Open Console    ${dut}.console    alias=${dut}
        Power On
        Console Read Until    ${dut} login:    timeout=30s
        Close Console
    END

Boards Are Switched Independently
    Use Device    dut1
    Power Off
    Use Device    dut2
    Power On
    Power Should Be On
    Dut1 Reboot    wait=0.1
    Dut2 Should Be Powered
    Use Device    dut1
    Power Should Be On

Switch Off The Second Board
    Use Device    dut2
    Power Off

Role Libraries Reset After Each Test
    [Documentation]    The previous test's ``Use Device`` does not leak: the roles are
    ...    bound to the first board again.
    Power On
    Dut1 Should Be Powered
    Run Keyword And Expect Error    *    Dut2 Should Be Powered

Bind One Role Explicitly
    [Documentation]    ``Bind Driver`` rebinds a single role library, by its ``AS`` name.
    Bind Driver    multi.power    dut2.power
    Power On
    Dut2 Should Be Powered
