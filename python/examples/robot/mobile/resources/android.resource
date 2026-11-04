*** Settings ***
Documentation       Android binding of the shared mobile flows: the emulator's client,
...                 locators, capabilities and tooling checks.

Library             OperatingSystem
Library             Process
Library             AppiumLibrary
Resource            ../client-android.resource
Resource            common.resource


*** Variables ***
${SESSION_NAME}             android
${EXPORTER_TEMPLATE}        ${CURDIR}/../exporter-android.yaml
${APPIUM_PATH}              emulator.appium
${SAMPLE_APP}               ${CURDIR}/../apps/ApiDemos-debug.apk
# Placeholders of exporter-android.yaml, from the environment or `robot -v`.
${ANDROID_SDK}              %{ANDROID_HOME=%{ANDROID_SDK_ROOT=}}
${ANDROID_EMULATOR}         ${ANDROID_SDK}/emulator/emulator
${AVD_NAME}                 %{AVD_NAME=jumpstarter_test}
${APPIUM_EXECUTABLE}        %{APPIUM_EXECUTABLE=appium}
${APPIUM_HOME}              %{APPIUM_HOME=~/.appium}
# Added to every Appium session of this platform. UiAutomator2's "Appium
# Settings" helper app (8.4.x) crashes on Android 15 images when it starts its
# location foreground service, so the sessions skip installing and starting
# it; `Prepare Platform For Appium` removes a copy left by earlier sessions.
&{APPIUM_CAPS}              appium:skipDeviceInitialization=${True}
# The built-in Settings app: no download needed.
&{SETTINGS_CAPS}            appium:appPackage=com.android.settings    appium:appActivity=.Settings
${SETTINGS_SEARCH}          xpath=//*[@resource-id='com.android.settings:id/search_action_bar' or @text='Search settings' or @content-desc='Search settings']
${SETTINGS_SEARCH_FIELD}    class=android.widget.EditText


*** Keywords ***
Skip Unless Tooling Present
    [Documentation]    Skip the Android suites unless the SDK, the AVD and Appium are installed.
    Skip If    not $ANDROID_SDK
    ...    ANDROID_HOME is not set: install the Android SDK (emulator, platform-tools) and export ANDROID_HOME
    ${emulator}=    Normalize Path    ${ANDROID_EMULATOR}
    Skip If    not os.path.isfile($emulator)
    ...    No Android emulator at ${emulator}: install it with `sdkmanager emulator`
    ${avds}=    Run Process    ${emulator}    -list-avds
    Skip If    $AVD_NAME not in $avds.stdout.split()
    ...    No AVD named ${AVD_NAME} (see `emulator -list-avds`): create it, or set AVD_NAME
    ${appium}=    Skip Unless Executable    ${APPIUM_EXECUTABLE}
    ...    install Appium 2 or 3 and its uiautomator2 driver, or set APPIUM_EXECUTABLE
    ${home}=    Normalize Path    ${APPIUM_HOME}
    # The emulator's ADB server child runs `adb` from PATH.
    ${adb}=    Evaluate    shutil.which("adb")    modules=shutil
    IF    $adb is None
        ${tools}=    Normalize Path    ${ANDROID_SDK}/platform-tools
        Set Environment Variable    PATH    ${tools}${:}%{PATH}
    END
    Skip Unless Executable    adb    install the Android SDK platform-tools
    Set Suite Variable    ${ANDROID_EMULATOR}    ${emulator}
    Set Suite Variable    ${APPIUM_EXECUTABLE}    ${appium}
    Set Suite Variable    ${APPIUM_HOME}    ${home}

Prepare Platform For Appium
    [Documentation]    Remove UiAutomator2's crashing "Appium Settings" helper; see ``&{APPIUM_CAPS}``.
    Emulator Connect    alias=prepare
    Run Keyword And Ignore Error    ADB Uninstall    io.appium.settings
    Close ADB Device    prepare

Dismiss System Dialogs
    [Documentation]    Close "<app> keeps stopping" and "isn't responding" dialogs.
    ...
    ...                Emulator images sometimes crash a system service (Settings
    ...                Services, the Appium Settings helper) while booting; its dialog
    ...                covers the app under test.
    FOR    ${attempt}    IN RANGE    3
        ${shown}=    Run Keyword And Return Status
        ...    Page Should Contain Element    xpath=//*[@resource-id='android:id/aerr_close' or @resource-id='android:id/aerr_wait']
        IF    not ${shown}    BREAK
        Run Keyword And Ignore Error    Click Element    xpath=//*[@resource-id='android:id/aerr_close' or @resource-id='android:id/aerr_wait']
        Sleep    1s
    END

Log Device Diagnostics
    [Documentation]    Attach the emulator's recent log and a screenshot to the Robot log.
    Emulator Connect    alias=diagnostics
    ADB Logcat    lines=200
    ADB Screenshot
