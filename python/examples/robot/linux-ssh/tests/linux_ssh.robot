*** Settings ***
Documentation       A Linux machine over SSH: SSHLibrary keywords through a Jumpstarter tunnel.
...
...                 ``Open SSH Connection`` forwards a local port to the exporter's SSH
...                 driver, hands SSHLibrary the exporter's key and logs in; after that
...                 every SSHLibrary keyword works on the device.
...
...                 The suite serves ``exporter.yaml`` locally, pointed at
...                 ``SSH_HOST:SSH_PORT`` (default 127.0.0.1:22), and logs in as
...                 ``SSH_USER`` with the key in ``SSH_IDENTITY_FILE`` or with
...                 ``SSH_PASSWORD``. Set them as environment variables or with
...                 ``robot -v``. It skips when nothing listens there or no credential
...                 is given.
...
...                 | SSH_IDENTITY_FILE=~/.ssh/id_ed25519 robot -d results tests/

Library             OperatingSystem
Library             SSHLibrary
Library             jumpstarter_robot.Jumpstarter    auto_import=False

Suite Setup         Serve The SSH Exporter
Suite Teardown      Run Keywords    Close All Connections    AND    Stop Local Exporter
Test Tags           ssh


*** Variables ***
${SSH_HOST}             %{SSH_HOST=127.0.0.1}
${SSH_PORT}             %{SSH_PORT=22}
${SSH_USER}             %{SSH_USER=%{USER=root}}
${SSH_IDENTITY_FILE}    %{SSH_IDENTITY_FILE=}
${SSH_PASSWORD}         %{SSH_PASSWORD=}


*** Test Cases ***
Run Commands On The Device
    ${kernel}=    Execute Command    uname -s
    Should Not Be Empty    ${kernel}
    ${output}    ${rc}=    Execute Command    echo jumpstarter; exit 3    return_rc=True
    Should Be Equal    ${output}    jumpstarter
    Should Be Equal As Integers    ${rc}    3

Copy A File To The Device And Back
    [Documentation]    Needs SFTP on the device; ``--exclude sftp`` skips it.
    [Tags]    sftp
    ${name}=    Evaluate    f"jumpstarter-robot-{uuid.uuid4().hex[:8]}.txt"    modules=uuid
    Create File    ${OUTPUT DIR}/${name}    sent over a Jumpstarter tunnel
    Put File    ${OUTPUT DIR}/${name}    /tmp/${name}
    SSHLibrary.File Should Exist    /tmp/${name}
    SSHLibrary.Get File    /tmp/${name}    ${OUTPUT DIR}/${name}.back
    ${content}=    OperatingSystem.Get File    ${OUTPUT DIR}/${name}.back
    Should Be Equal    ${content}    sent over a Jumpstarter tunnel
    [Teardown]    Execute Command    rm -f /tmp/${name}

Endpoint For Other Tools
    [Documentation]    ``Get SSH Endpoint`` gives host, port, user and key file, for
    ...    ``scp``, ``rsync`` or anything else run with the Process library.
    ${endpoint}=    Get SSH Endpoint
    Should Be Equal    ${endpoint.username}    ${SSH_USER}
    Should Be True    ${endpoint.port} > 0


*** Keywords ***
Serve The SSH Exporter
    ${listening}=    Run Keyword And Return Status    Evaluate
    ...    socket.create_connection(($SSH_HOST, int($SSH_PORT)), timeout=2).close()    modules=socket
    Skip If    not ${listening}
    ...    Nothing listens on ${SSH_HOST}:${SSH_PORT}; start sshd there or set SSH_HOST / SSH_PORT.
    Skip If    not ($SSH_IDENTITY_FILE or $SSH_PASSWORD)
    ...    Set SSH_IDENTITY_FILE (a private key ${SSH_HOST} accepts for ${SSH_USER}) or SSH_PASSWORD.
    ${template}=    OperatingSystem.Get File    ${CURDIR}/../exporter.yaml
    ${config}=    Replace Variables    ${template}
    Create File    ${OUTPUT DIR}/exporter-ssh.yaml    ${config}
    Start Local Exporter    ${OUTPUT DIR}/exporter-ssh.yaml
    ${password}=    Set Variable If    $SSH_PASSWORD    ${SSH_PASSWORD}    ${None}
    Open SSH Connection    ssh    alias=device    password=${password}
