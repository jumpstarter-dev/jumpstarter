*** Settings ***
Documentation       ADB through the Jumpstarter tunnel, without an ``adb`` binary for the keywords.
...
...                 ``Emulator Connect`` opens the emulator's ADB server endpoint and waits
...                 for boot; the ``ADB ...`` keywords then act on that device.
...                 ``Get ADB Environment`` hands the same server to your own tools.

Library             Process
Resource            ../../resources/android.resource

Suite Setup         Run Keywords    Prepare Device    AND    Emulator Connect    alias=emulator
Suite Teardown      Run Keywords    Close All ADB Devices    AND    Release Device


*** Test Cases ***
Shell And Properties
    ${sdk}=    ADB Shell    getprop ro.build.version.sdk
    Should Match Regexp    ${sdk}    ^\\d+$
    ${model}=    ADB Get Property    ro.product.model
    Should Not Be Empty    ${model}
    Run Keyword And Expect Error    *    ADB Shell    exit 3

Install The Sample App
    Skip Unless Sample App Present
    ADB Install    ${SAMPLE_APP}
    ${packages}=    ADB Shell    pm list packages io.appium.android.apis
    Should Contain    ${packages}    io.appium.android.apis
    [Teardown]    Run Keyword And Ignore Error    ADB Uninstall    io.appium.android.apis

Logcat And Screenshot
    ${log}=    ADB Logcat    lines=50
    Should Not Be Empty    ${log}
    ${png}=    ADB Screenshot    adb.png
    File Should Exist    ${png}

Real adb Against The Leased Device
    &{env}=    Get ADB Environment    emulator.adb
    ${result}=    Run Process    adb    devices
    ...    env:ANDROID_ADB_SERVER_ADDRESS=${env.ANDROID_ADB_SERVER_ADDRESS}
    ...    env:ANDROID_ADB_SERVER_PORT=${env.ANDROID_ADB_SERVER_PORT}
    Should Be Equal As Integers    ${result.rc}    0
    Should Contain    ${result.stdout}    emulator-
