---
title: Jumpstarter
---

{{< blocks/cover title="Welcome to the jumpstarter project" image_anchor="top" height="full" >}}

<img class="front-logo" src="jumpstarter.svg"/>

<a class="btn btn-lg btn-primary me-3 mb-4" href="/docs/">
  Learn More <i class="fas fa-arrow-alt-circle-right ms-2"></i>
</a>
<a class="btn btn-lg btn-secondary me-3 mb-4" href="https://github.com/jumpstarter-dev/jumpstarter/releases">
  Download <i class="fab fa-github ms-2 "></i>
</a>

<img class="front-console" src="jumpstarter-console2.png">

<p class="lead mt-5"><h2>Enabling Hardware in the Loop for Edge devices &mdash; in the datacenter, lab and development environment!</h2></p>
{{< blocks/link-down color="info" >}}
{{< /blocks/cover >}}


{{% blocks/lead color="primary" %}}
Jumpstarter helps you test your software stack in your hardware stack in CI/CD pipelines and streamline your development workflow.
Where traditional cloud software has been testing this way for a long time now, testing
software for edge devices has been a challenge, in many cases <b>emulators for the hardware are not available</b>
, the GPU, the specific sensors, etc.

Jumpstarter helps you test your software in the real hardware, and <b>eliminates the need for manual
test operations</b>.

Jumpstarter is a <b>free and open source</b> project, and is <b>currently developed</b> by <a href="https://next.redhat.com">Red Hat ET</a>.

{{% /blocks/lead %}}


{{% blocks/section color="dark" type="row" %}}
{{% blocks/feature icon="fa-robot" title="Automate testing" %}}
The jumpstarter scripting language will enable you to interact with the hardware in your test environment:
power, reset, image flashing, serial console interaction, and more.

{{% /blocks/feature %}}


{{% blocks/feature icon="fab fa-github" title="Contributions welcome!"  %}}
We do a [Pull Request](https://github.com/jumpstarter-dev/jumpstarter/pulls) contributions workflow on **GitHub**. New users are always welcome!
{{% /blocks/feature %}}


{{% blocks/feature icon="fa fa-microchip" title="Open Hardware included" url="https://github.com/jumpstarter-dev/jumpstarter-board" %}}
The jumpstarter project works with the jumpstarter-board, a small board that can be used to control the power and storage
of your device under test.

But it is designed to allow other harware to work with the jumpstarter stack via a driver design.

{{% /blocks/feature %}}


{{% /blocks/section %}}



