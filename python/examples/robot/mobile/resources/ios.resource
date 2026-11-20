*** Settings ***
Documentation       iOS binding of the shared mobile flows: the simulator's client,
...                 locators, capabilities and tooling checks (macOS only).

Library             OperatingSystem
Library             Process
Library             AppiumLibrary
Resource            ../client-ios.resource
Resource            common.resource


*** Variables ***
${SESSION_NAME}             ios
${EXPORTER_TEMPLATE}        ${CURDIR}/../exporter-ios.yaml
${APPIUM_PATH}              simulator.appium
${SAMPLE_APP}               ${CURDIR}/../apps/UIKitCatalog-iphonesimulator.zip
# Placeholders of exporter-ios.yaml, from the environment or `robot -v`.
# IOS_RUNTIME defaults to the newest installed runtime that supports IOS_DEVICE_TYPE.
${IOS_DEVICE_TYPE}          %{IOS_DEVICE_TYPE=com.apple.CoreSimulator.SimDeviceType.iPhone-16}
${IOS_RUNTIME}              %{IOS_RUNTIME=}
${IDB_COMPANION}            %{IDB_COMPANION=idb_companion}
${APPIUM_EXECUTABLE}        %{APPIUM_EXECUTABLE=appium}
${APPIUM_HOME}              %{APPIUM_HOME=~/.appium}
${WDA_APP}                  %{WDA_APP=}
# Added to every Appium session of this platform.
&{APPIUM_CAPS}
# The built-in Settings app: no download needed.
&{SETTINGS_CAPS}            appium:bundleId=com.apple.Preferences
${SETTINGS_SEARCH}          class=XCUIElementTypeSearchField
${SETTINGS_SEARCH_FIELD}    class=XCUIElementTypeSearchField


*** Keywords ***
Prepare Platform For Appium
    [Documentation]    Nothing to prepare on the simulator before Appium starts.
    No Operation

Dismiss System Dialogs
    [Documentation]    The simulator shows no system crash dialogs to close.
    No Operation

Skip Unless Tooling Present
    [Documentation]    Skip the iOS suites unless this is a Mac with Xcode, idb_companion, Appium and a WDA.
    Skip If    sys.platform != "darwin"    The iOS simulator needs macOS
    Skip Unless Executable    xcrun    install Xcode
    ${simctl}=    Run Process    xcrun    simctl    list    -j    runtimes
    Skip If    ${simctl.rc} != 0
    ...    `xcrun simctl` failed: select the full Xcode with `sudo xcode-select -s /Applications/Xcode.app`
    IF    not $IOS_RUNTIME
        # The newest runtime that can run IOS_DEVICE_TYPE: a freshly installed
        # runtime may support only a few device types.
        ${runtime}=    Evaluate
        ...    (lambda runtimes, device: max((r for r in runtimes if r.get("platform") == "iOS" and r.get("isAvailable") and any(t.get("identifier") == device for t in r.get("supportedDeviceTypes", []))), key=lambda r: [int(p) for p in r["version"].split(".")], default={}).get("identifier", ""))(json.loads($simctl.stdout)["runtimes"], $IOS_DEVICE_TYPE)
        ...    modules=json
        Skip If    not $runtime
        ...    No installed iOS simulator runtime supports ${IOS_DEVICE_TYPE}: set IOS_DEVICE_TYPE or IOS_RUNTIME, or add a runtime in Xcode > Settings > Components
        Set Suite Variable    ${IOS_RUNTIME}    ${runtime}
    END
    ${companion}=    Skip Unless Executable    ${IDB_COMPANION}
    ...    install idb_companion (https://github.com/facebook/idb), or set IDB_COMPANION
    ${appium}=    Skip Unless Executable    ${APPIUM_EXECUTABLE}
    ...    install Appium 2 or 3 and its xcuitest driver, or set APPIUM_EXECUTABLE
    Skip If    not $WDA_APP
    ...    WDA_APP is not set: point it at a prebuilt simulator WebDriverAgentRunner-Runner.app
    ${wda}=    Normalize Path    ${WDA_APP}
    Skip If    not os.path.isdir($wda)    No WebDriverAgent app at ${wda}
    ${home}=    Normalize Path    ${APPIUM_HOME}
    Set Suite Variable    ${IDB_COMPANION}    ${companion}
    Set Suite Variable    ${APPIUM_EXECUTABLE}    ${appium}
    Set Suite Variable    ${APPIUM_HOME}    ${home}
    Set Suite Variable    ${WDA_APP}    ${wda}

Log Device Diagnostics
    [Documentation]    Attach the simulator's recent system log (needs ``idb``) and a screenshot.
    ${idb}=    Evaluate    shutil.which("idb")    modules=shutil
    IF    $idb is not None
        Simulator Ios Connect    alias=diagnostics
        iOS Syslog    duration=5s
    END
    Capture Page Screenshot
