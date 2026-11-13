*** Settings ***
Documentation       Connect the board once for all lab suites.
...
...                 ``Acquire Lease`` attaches to your lease for the testbed's selector or
...                 acquires one (``testbeds/lab.yaml``), acquires a fresh one per pabot
...                 process (``testbeds/lab-pool.yaml``), and serves the mock board
...                 in-process when the session has only an exporter config
...                 (``testbeds/mock.yaml``, or no testbed). A lease it acquired is
...                 released when the run ends.

Resource            ../client.resource

Suite Setup         Acquire Lease    session=board
