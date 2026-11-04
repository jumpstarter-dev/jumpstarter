*** Settings ***
Documentation       Device control and UI automation in one test: power cycle the emulator
...                 through its power driver, then check the UI again.

Resource            ../../resources/android.resource

Suite Setup         Prepare Device
Suite Teardown      Release Device


*** Test Cases ***
App Survives Device Reboot
    Open Settings App
    Close Application
    Stop Appium
    Device Power Cycle    wait=1
    Start Device Appium
    Open Settings App
    Wait Until Page Contains Element    ${SETTINGS_SEARCH}    timeout=30s
    [Teardown]    Run Keyword And Ignore Error    Close Application

Collect Device Logs On Failure
    Open Settings App
    Search Settings For    Bluetooth
    [Teardown]    Run Keywords
    ...    Run Keyword If Test Failed    Log Device Diagnostics
    ...    AND    Run Keyword And Ignore Error    Close Application
