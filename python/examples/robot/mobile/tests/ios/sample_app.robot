*** Settings ***
Documentation       Drive Appium's UIKitCatalog sample app.
...
...                 ``Appium Upload App`` uploads the zipped app to the exporter and
...                 returns a ``jumpstarter-app:`` token; ``Open Application``
...                 installs it from ``appium:app``.

Resource            ../../resources/ios.resource

Suite Setup         Prepare Device
Suite Teardown      Release Device


*** Test Cases ***
Install And Drive Sample App
    Skip Unless Sample App Present
    ${token}=    Appium Upload App    ${SAMPLE_APP}
    Should Start With    ${token}    jumpstarter-app:
    Open Application    ${APPIUM.url}    alias=sample
    ...    &{APPIUM.capabilities}    &{APPIUM_CAPS}    appium:app=${token}
    Dismiss System Dialogs
    Wait Until Page Contains Element    accessibility_id=Buttons    timeout=60s
    Click Element    accessibility_id=Buttons
    Wait Until Page Contains Element    xpath=//XCUIElementTypeNavigationBar[@name="Buttons"]    timeout=30s
    Capture Page Screenshot
    [Teardown]    Run Keyword And Ignore Error    Close Application
