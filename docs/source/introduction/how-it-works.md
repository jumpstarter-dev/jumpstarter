# How Jumpstarter Works

Jumpstarter bridges the gap between your local development environment, 
CI/CD pipelines, and the target device you're developing for.

We accomplish this by providing ways to decouple the target hardware from
your CI runners and development systems. This means that you can setup a test
lab with low-cost hosts such as Raspberry Pis or mini PCs, while still using the
same CI systems you currently host in the cloud.

Since we provide remote access to hardware, Jumpstarter also makes sharing
limited hardware across multiple developers easy weather they are sitting at the
next desk over or on the other side of the earth.

Jumpstarter provides two modes of operation, a distributed mode, and a local-only mode.

* The *local-only mode* is useful for development and testing of Jumpstarter drivers and
  for very small labs where only one developer is working on a project.

<!-- TODO: image here -->

* The *distributed mode* is useful for bigger, more distributed labs where collaboration
  across teams, seamless CI integration and device sharing is needed.

<!-- TODO: image here -->

The following sections provide more information on the basics of Jumpstarter,
its core components, and how they work together to make hardware testing easier.