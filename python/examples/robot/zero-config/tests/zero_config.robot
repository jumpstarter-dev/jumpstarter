*** Settings ***
Documentation       No client resource: the library connects and imports a keyword library
...                 for every driver it finds. Handy for exploring; generate a client
...                 resource to get the same keywords in the editor.

Library             jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/../../basic/exporter.yaml


*** Test Cases ***
Discover And Drive
    ${drivers}=    List Drivers
    Log Many    @{drivers}
    ${paths}=    Evaluate    [driver.path for driver in $drivers]
    Should Contain    ${paths}    dut.power
    # Each keyword carries its driver's path: dut.power gives Dut Power On. These
    # keywords only exist once the run has connected, so the editor cannot see
    # them; the robotcode comments keep it from flagging them.
    Dut Power On    # robotcode: ignore[KeywordNotFound]
    Dut Power Should Be On    # robotcode: ignore[KeywordNotFound]
    Dut Should Be Powered    # robotcode: ignore[KeywordNotFound]
    Storage Host    # robotcode: ignore[KeywordNotFound]
    Storage Dut    # robotcode: ignore[KeywordNotFound]
    # The library is named after the path, so a qualified name works too.
    dut.power.Dut Power Off    # robotcode: ignore[KeywordNotFound]

Keywords Of One Driver
    [Documentation]    ``List Driver Keywords`` names them as the auto-imported library does.
    ${keywords}=    List Driver Keywords    dut.power
    Should Contain    ${keywords}    Dut Power On
