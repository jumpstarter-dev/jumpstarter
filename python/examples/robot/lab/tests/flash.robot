*** Settings ***
Documentation       Flash an image and boot from it.

Resource            ../client.resource


*** Test Cases ***
Flash Image And Boot
    Dut Power Off
    Storage Host
    Storage Flash    ${CURDIR}/../../basic/data/image.bin
    Storage Dut
    Dut Power On
    Dut Power Should Be On
