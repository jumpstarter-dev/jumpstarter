*** Settings ***
Documentation       Use a Robot Framework remote library that runs on the device.
...
...                 The board runs a remote library server; Jumpstarter forwards a local
...                 port to it with `Open Port Forward`, and Robot Framework's own
...                 ``Remote`` library imports its keywords from there. The device needs
...                 no route to the test runner: the exporter reaches the server, and the
...                 lease decides who may.
...
...                 Locally, ``board_remote_server.py`` plays the board on port 8271 (8270, the remote library default, is pabot's own PabotLib port).

Library             Process
Library             jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/../exporter.yaml
...                 session=remote    auto_import=False

Suite Setup         Connect To The Board
Suite Teardown      Terminate All Processes
Test Tags           remote


*** Test Cases ***
# The Board keywords come from the server at run time, so the editor cannot
# know them beforehand; the robotcode comments keep it from flagging them.
Board Keywords Run On The Board
    ${temperature}=    Board.Get Temperature    # robotcode: ignore[KeywordNotFound]
    Should Be True    ${temperature} < 85
    Board.Set Led    on    # robotcode: ignore[KeywordNotFound]
    Board.Led Should Be    on    # robotcode: ignore[KeywordNotFound]

Board Failures Fail The Test
    Run Keyword And Expect Error    *LED state must be on or off*
    ...    Board.Set Led    blinking    # robotcode: ignore[KeywordNotFound]


*** Keywords ***
Connect To The Board
    ${python}=    Evaluate    sys.executable    modules=sys
    Start Process    ${python}    ${CURDIR}/../board_remote_server.py    8271    alias=board
    Wait Until Keyword Succeeds    10s    0.2s    Port Should Be Open    127.0.0.1    8271
    ${address}=    Open Port Forward    remote    session=remote
    Import Library    Remote    http://${address}    AS    Board

Port Should Be Open
    [Arguments]    ${host}    ${port}
    Evaluate    socket.create_connection(($host, int($port)), timeout=1).close()    modules=socket
