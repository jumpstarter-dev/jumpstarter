*** Settings ***
Documentation       Shell methods configured on the exporter.
...
...                 ``exporter.yaml`` gives the shell driver the methods ``hello`` and
...                 ``fail``, so the resource has ``Shell Hello`` and ``Shell Fail``.
...                 Named arguments become environment variables.

Resource            ../client.resource


*** Test Cases ***
Shell Method Returns Output
    ${result}=    Shell Hello    world    GREETING=hi
    Should Contain    ${result.stdout}    hi world

Shell Method Return Codes
    ${result}=    Shell Fail    expected_rc=3
    Should Contain    ${result.stderr}    failing on purpose
    Run Keyword And Expect Error    *returned 3, expected 0*    Shell Fail
