*** Settings ***
Documentation       Android suites: one device for the whole folder, booted once.
...
...                 The suite setup skips every Android test, with the reason, when the
...                 local tooling is missing; see mobile/README.md.

Resource            ../../resources/android.resource

Suite Setup         Prepare Device
Suite Teardown      Release Device
Test Tags           mobile    android
