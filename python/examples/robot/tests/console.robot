*** Settings ***
Documentation       A byte stream needs no Jumpstarter keywords of its own.
...                 ``Open Port Forward`` serves the driver's stream on a local port,
...                 and Robot Framework's Telnet library talks to it. The mock
...                 console echoes what it receives; on a real board, ``Telnet.Login``
...                 and ``Telnet.Execute Command`` work the same way.

Library             String
Library             Telnet    timeout=10s
Resource            ../client.resource
Test Teardown       Close All Connections


*** Test Cases ***
Console Echoes What It Receives
    ${address}=    Open Port Forward    console
    ${host}    ${port}=    Split String    ${address}    :
    Telnet.Open Connection    ${host}    port=${port}
    Telnet.Write Bare    hello board
    ${output}=    Telnet.Read Until    hello board
    Should Contain    ${output}    hello board
