*** Settings ***
Documentation       iOS suites: one device for the whole folder, booted once.
...
...                 The suite setup skips every iOS test, with the reason, when the
...                 local tooling is missing; see mobile/README.md.

Resource            ../../resources/ios.resource

Suite Setup         Prepare Device
Suite Teardown      Release Device
Test Tags           mobile    ios
