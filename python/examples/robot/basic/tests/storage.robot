*** Settings ***
Documentation       Flash an image through the storage mux, and call any driver method.
...
...                 ``Storage ...`` keywords come from the storage driver. A local path
...                 is uploaded to the exporter for you.

Resource            ../client.resource


*** Test Cases ***
Flash Image And Boot
    Dut Power Off
    Storage Host
    Storage Flash    ${CURDIR}/../data/image.bin
    Storage Dut
    Dut Power On
    Dut Power Should Be On

Call Any Driver Method
    [Documentation]    ``Call Driver`` reaches any client method by driver path, converting
    ...    text arguments to the method's types.
    Call Driver    dut.power    cycle    wait=0
    ${keywords}=    List Driver Keywords    dut.power
    Should Contain    ${keywords}    Dut Power Cycle
