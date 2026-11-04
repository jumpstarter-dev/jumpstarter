*** Settings ***
Documentation       The built-in Settings app: no download needed.

Resource            ../../resources/android.resource

Suite Setup         Prepare Device
Suite Teardown      Release Device


*** Test Cases ***
Settings Search Finds Wi-Fi
    Settings Search Finds    Wi-Fi
