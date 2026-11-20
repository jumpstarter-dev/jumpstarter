*** Settings ***
Documentation       Local iOS tools against the simulator, through the Jumpstarter tunnel.
...
...                 ``Simulator Ios Connect`` serves the simulator's idb companion on a
...                 local port; ``Run iOS Tool`` and the ``iOS ...`` keywords run ``idb``
...                 against it.
...                 These tests need the ``idb`` client on PATH (``pip install fb-idb``)
...                 and skip without it.

Resource            ../../resources/ios.resource

Suite Setup         Run Keywords    Prepare Device    AND    Connect Over idb
Suite Teardown      Run Keywords    Close iOS Device    AND    Release Device


*** Test Cases ***
Describe The Simulator
    ${info}=    iOS Device Info
    Log    ${info}
    ${result}=    Run iOS Tool    idb    describe
    Should Not Be Empty    ${result.stdout}

Launch And Terminate A Built-In App
    [Documentation]    ``iOS Screenshot`` (``idb screenshot``) needs the simulator's display,
    ...    so a headless simulator is checked through ``idb list-apps`` instead.
    iOS Launch App    com.apple.Preferences
    ${apps}=    Run iOS Tool    idb    list-apps    --json
    Should Contain    ${apps.stdout}    com.apple.Preferences
    iOS Terminate App    com.apple.Preferences

Install The Sample App
    Skip Unless Sample App Present
    ${app}=    Unzip Sample App
    iOS Install App    ${app}
    iOS Launch App    com.example.apple-samplecode.UICatalog
    [Teardown]    Run Keyword And Ignore Error    iOS Terminate App    com.example.apple-samplecode.UICatalog

System Log
    ${log}=    iOS Syslog    duration=3s
    Log    ${log}


*** Keywords ***
Connect Over idb
    ${idb}=    Skip Unless Executable    idb    install the idb client with `pip install fb-idb`
    Simulator Ios Connect    protocol=idb    alias=simulator

Unzip Sample App
    [Documentation]    ``idb install`` takes an ``.app`` bundle, so unpack the downloaded zip.
    ${target}=    Set Variable    ${OUTPUT DIR}/UIKitCatalog
    Evaluate    shutil.unpack_archive($SAMPLE_APP, $target)    modules=shutil
    ${apps}=    List Directories In Directory    ${target}    *.app    absolute=True
    Should Not Be Empty    ${apps}
    RETURN    ${apps}[0]
