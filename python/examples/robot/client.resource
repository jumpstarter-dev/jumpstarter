*** Settings ***
Documentation       The mock board of this example. It declares one session and
...                 imports one keyword library for each driver in ``exporter.yaml``.
...                 The ``client`` argument names the client class of the driver;
...                 the keywords and their documentation come from that class.

Library             jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/exporter.yaml    auto_import=False
Library             jumpstarter_robot.Driver    power    client=jumpstarter_driver_power.client.PowerClient    AS    power
Library             jumpstarter_robot.Driver    storage    client=jumpstarter_driver_opendal.client.StorageMuxFlasherClient    AS    storage
Library             jumpstarter_robot.Driver    console    client=jumpstarter_driver_network.client.NetworkClient    AS    console
Library             jumpstarter_robot.Driver    shell    client=jumpstarter_driver_shell.client.ShellClient    methods=hello,fail    AS    shell
