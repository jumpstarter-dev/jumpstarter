*** Settings ***
Documentation       Upload Appium's ApiDemos sample app and drive it.
...
...                 Shows the explicit upload: ``Appium Upload App`` returns a
...                 ``jumpstarter-app:`` token that the session installs.

Resource            ../../resources/android.resource

Suite Setup         Prepare Device
Suite Teardown      Release Device


*** Test Cases ***
Install And Drive Sample App
    Skip Unless Sample App Present
    ${token}=    Appium Upload App    ${SAMPLE_APP}
    Should Start With    ${token}    jumpstarter-app:
    Appium Open Application    alias=sample    app=${token}    &{APPIUM_CAPS}
    Dismiss System Dialogs
    Wait Until Page Contains Element    accessibility_id=Views    timeout=30s
    Click Element    accessibility_id=Views
    Wait Until Page Contains Element    accessibility_id=Animation    timeout=20s
    Capture Page Screenshot
    [Teardown]    Run Keyword And Ignore Error    Close Application
