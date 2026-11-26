*** Settings ***
Documentation       Shell methods configured in ``exporter.yaml`` become keywords.
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
