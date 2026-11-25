*** Settings ***
Documentation       Power keywords, which come from the client class of the power driver.

Resource            ../client.resource


*** Test Cases ***
Power Cycle The Board
    Power On
    Power Cycle    wait=0
    Power Off

Power Readings Are Sane
    ${readings}=    Power Read    max_items=2
    Length Should Be    ${readings}    2
    Should Be True    $readings[0].voltage >= 0

Call Any Driver Method
    [Documentation]    ``Call Driver`` reaches any client method by driver path.
    Call Driver    power    cycle    wait=0
    ${keywords}=    List Driver Keywords    power
    Should Contain    ${keywords}    Power Cycle
