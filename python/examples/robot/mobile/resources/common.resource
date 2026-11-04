*** Settings ***
Documentation       Platform-neutral mobile flows, shared by the Android and iOS suites.
...
...                 Nothing here names a platform. The platform resource
...                 (``android.resource`` or ``ios.resource``) imports this file and
...                 defines what it uses: the variables ``${SESSION_NAME}``,
...                 ``${EXPORTER_TEMPLATE}``, ``${APPIUM_PATH}``, ``&{APPIUM_CAPS}``,
...                 ``&{SETTINGS_CAPS}``, ``${SETTINGS_SEARCH}`` and
...                 ``${SETTINGS_SEARCH_FIELD}``, and the keywords
...                 ``Skip Unless Tooling Present`` and ``Log Device Diagnostics``.
...                 Both client resources alias their power and Appium drivers to
...                 ``Device Power`` and ``Appium``, so ``Device Power On`` and
...                 ``Appium Upload App`` mean the same on both platforms.
...
...                 `Start Device Appium` keeps the running server in ``${APPIUM}``:
...                 AppiumLibrary's ``Open Application`` takes its ``url`` and
...                 ``capabilities``.

Library             OperatingSystem
Library             AppiumLibrary


*** Variables ***
# The running Appium server, set by `Start Device Appium`: ``url`` and ``capabilities``.
${APPIUM}       ${None}


*** Keywords ***
Prepare Device
    [Documentation]    Serve the exporter, boot the device and start Appium, once per run.
    ...
    ...    Used as the suite setup of the platform folder's ``__init__.robot`` and
    ...    of every suite file, so a single file runs on its own too. Only the
    ...    suite that prepared the device shuts it down (`Release Device`).
    ...    With a selector or lease name for the session, such as
    ...    ``-v JMP_ANDROID_SELECTOR:...`` or ``-v JMP_IOS_LEASE_NAME:...``, or
    ...    inside ``jmp shell``, the device is remote and no local tooling is needed.
    ${ready}=    Get Variable Value    ${DEVICE_READY_${SESSION_NAME}}    ${False}
    IF    ${ready}    RETURN
    ${remote}=    Device Is Remote
    IF    not ${remote}
        Skip Unless Tooling Present
        Serve Exporter Template
    END
    Boot Device
    Set Global Variable    ${DEVICE_READY_${SESSION_NAME}}    ${True}
    Set Global Variable    ${DEVICE_OWNER_${SESSION_NAME}}    ${SUITE NAME}

Device Is Remote
    [Documentation]    Whether the session uses a lease or ``jmp shell`` for its device, rather than a local exporter.
    ...
    ...    The generated client resource names the session's variables after it,
    ...    such as ``${JMP_ANDROID_SELECTOR}``; a generic ``${JMP_SELECTOR}`` or
    ...    ``${JMP_LEASE_NAME}`` given on the command line counts too.
    ${selector}=    Get Variable Value    ${JMP_${SESSION_NAME.upper()}_SELECTOR}    ${EMPTY}
    ${lease}=    Get Variable Value    ${JMP_${SESSION_NAME.upper()}_LEASE_NAME}    ${EMPTY}
    ${any_selector}=    Get Variable Value    ${JMP_SELECTOR}    ${EMPTY}
    ${any_lease}=    Get Variable Value    ${JMP_LEASE_NAME}    ${EMPTY}
    ${remote}=    Evaluate
    ...    bool($selector or $lease or $any_selector or $any_lease or os.environ.get("JUMPSTARTER_HOST"))
    ...    modules=os
    RETURN    ${remote}

Release Device
    [Documentation]    Shut the device down and stop its exporter, if this suite prepared it.
    ${owner}=    Get Variable Value    ${DEVICE_OWNER_${SESSION_NAME}}    ${None}
    IF    $owner != $SUITE_NAME    RETURN
    Run Keyword And Ignore Error    Shut Down Device
    Stop Local Exporter    session=${SESSION_NAME}
    Set Global Variable    ${DEVICE_READY_${SESSION_NAME}}    ${False}
    Set Global Variable    ${DEVICE_OWNER_${SESSION_NAME}}    ${None}

Serve Exporter Template
    [Documentation]    Fill in the exporter config template's placeholders and serve it in-process.
    ${template}=    Get File    ${EXPORTER_TEMPLATE}
    ${config}=    Replace Variables    ${template}
    ${path}=    Set Variable    ${OUTPUT DIR}/exporter-${SESSION_NAME}.yaml
    Create File    ${path}    ${config}
    Start Local Exporter    ${path}    session=${SESSION_NAME}

Boot Device
    Device Power On
    Prepare Platform For Appium
    Start Device Appium

Start Device Appium
    [Documentation]    Serve the device's Appium and keep it in ``${APPIUM}`` for ``Open Application``.
    ${appium}=    Start Appium    ${APPIUM_PATH}
    Set Global Variable    ${APPIUM}    ${appium}

Shut Down Device
    Run Keyword And Ignore Error    Close All Applications
    Run Keyword And Ignore Error    Stop Appium
    Device Power Off

Skip Unless Executable
    [Documentation]    Skip unless ``command`` runs from PATH (or is a path); returns its full path.
    [Arguments]    ${command}    ${hint}
    ${found}=    Evaluate    shutil.which(os.path.expanduser($command))    modules=shutil,os
    Skip If    $found is None    '${command}' was not found: ${hint}
    RETURN    ${found}

Open Settings App
    Open Application    ${APPIUM.url}    alias=settings
    ...    &{APPIUM.capabilities}    &{APPIUM_CAPS}    &{SETTINGS_CAPS}
    Dismiss System Dialogs

Search Settings For
    [Arguments]    ${text}
    Wait Until Page Contains Element    ${SETTINGS_SEARCH}    timeout=20s
    Click Element    ${SETTINGS_SEARCH}
    Wait Until Page Contains Element    ${SETTINGS_SEARCH_FIELD}    timeout=20s
    Input Text    ${SETTINGS_SEARCH_FIELD}    ${text}
    Wait Until Page Contains    ${text}    timeout=20s

Settings Search Finds
    [Arguments]    ${text}
    Open Settings App
    Search Settings For    ${text}
    Capture Page Screenshot
    [Teardown]    Run Keyword And Ignore Error    Close Application

Skip Unless Sample App Present
    Skip If    not os.path.exists($SAMPLE_APP)
    ...    ${SAMPLE_APP} is missing: run mobile/scripts/fetch-sample-apps.sh first
