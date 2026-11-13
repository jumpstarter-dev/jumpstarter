*** Settings ***
Documentation       Upload Appium's ApiDemos sample app and drive it.
...
...                 ``Appium Upload App`` uploads the APK to the exporter and returns a
...                 ``jumpstarter-app:`` token; ``Open Application`` installs it from
...                 ``appium:app``.

Resource            ../../resources/android.resource

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
    Wait Until Page Contains Element    accessibility_id=Views    timeout=30s
    Click Element    accessibility_id=Views
    Wait Until Page Contains Element    accessibility_id=Animation    timeout=20s
    Capture Page Screenshot
    [Teardown]    Run Keyword And Ignore Error    Close Application
