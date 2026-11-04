*** Settings ***
Documentation       Drive Appium's UIKitCatalog sample app.
...
...                 Shows the implicit upload: a local ``app=`` path is uploaded by
...                 ``Appium Open Application`` itself.

Resource            ../../resources/ios.resource

Suite Setup         Prepare Device
Suite Teardown      Release Device


*** Test Cases ***
Install And Drive Sample App
    Skip Unless Sample App Present
    Appium Open Application    alias=sample    app=${SAMPLE_APP}    &{APPIUM_CAPS}
    Dismiss System Dialogs
    Wait Until Page Contains Element    accessibility_id=Buttons    timeout=60s
    Click Element    accessibility_id=Buttons
    Wait Until Page Contains Element    xpath=//XCUIElementTypeNavigationBar[@name="Buttons"]    timeout=30s
    Capture Page Screenshot
    [Teardown]    Run Keyword And Ignore Error    Close Application
