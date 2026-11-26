*** Settings ***
Documentation       Flash an image through the storage mux. A local path is
...                 uploaded to the exporter for you.

Resource            ../client.resource


*** Test Cases ***
Flash Image And Boot
    Power Off
    Storage Host
    Storage Flash    ${CURDIR}/../data/image.bin
    Storage Dut
    Power On
