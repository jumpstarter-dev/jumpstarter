# Robot Framework

`jumpstarter-robot` connects [Robot Framework](https://robotframework.org/)
suites to Jumpstarter exporters. Start with the
{doc}`Robot Framework guide <../../../getting-started/guides/robot-framework>`;
the pages here cover the details and the more complex setups.

- {doc}`Client resources <client-resources>`: generating resources from an
  exporter config, a committed driver mapping or a live exporter; keyword
  names and aliases; sharing resources; zero configuration
- {doc}`Sessions and leases <sessions-and-leases>`: testbeds, several sessions
  and identical devices, the three ways to get a lease, lease reuse, lease
  ends, CI and parallel runs with pabot
- {doc}`Devices and apps <devices-and-apps>`: consoles, driver contexts and
  streams, port forwards and remote libraries, Linux over SSH, Android over
  ADB, iOS, and one set of Appium tests for Android and iOS
- {doc}`Reference <reference>`: every library argument, keyword and setting

```{toctree}
:maxdepth: 1
:hidden:
client-resources.md
sessions-and-leases.md
devices-and-apps.md
reference.md
```
