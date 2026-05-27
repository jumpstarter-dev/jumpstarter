# Welcome to Jumpstarter

```{eval-rst}
.. image:: https://img.shields.io/badge/GitHub-Repository-blue?logo=github
   :target: https://github.com/jumpstarter-dev/jumpstarter
   :alt: GitHub Repository

.. image:: https://img.shields.io/badge/PyPI-Packages-blue?logo=pypi
   :target: https://pypi.org/project/jumpstarter/
   :alt: Python Packages

.. image:: https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue
   :target: https://matrix.to/#/#jumpstarter:matrix.org
   :alt: Matrix Chat

.. image:: https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad
   :target: https://etherpad.jumpstarter.dev/pad-lister
   :alt: Etherpad Notes

.. image:: https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet
   :target: https://meet.google.com/gzd-hhbd-hpu
   :alt: Weekly Meeting
```

Jumpstarter is a free and open source test automation framework. It bridges the gap
between embedded development workflows and deployment environments, enabling
consistent automated testing across real hardware and virtual environments with
CI/CD integration. Every interface is programmatic, so human developers, test
scripts, CI pipelines, and AI agents interact with devices through the same APIs.

See Jumpstarter in action:

```{raw} html
<div id="demo-player"></div>
<script>
  document.addEventListener('DOMContentLoaded', function() {
    AsciinemaPlayer.create('_static/demo.cast', document.getElementById('demo-player'), {
      fit: 'width',
      autoPlay: false,
      poster: 'npt:0:05',
      loop: true,
      speed: 1.5,
      rows: 25,
      markers: [
        [16.7, "lease device"],
        [24.0, "power cycle"],
        [34.3, "serial start"],
        [41.5, "pytest"]
      ]
    });
  });
</script>
```

One tool, any target. Jumpstarter decouples devices from test runners, letting
you use identical automation scripts everywhere - your *Makefile* for device
testing. Every interface is programmatic, so human developers, test scripts, CI
pipelines, and AI agents all interact with hardware through the same APIs.

```{include} ../../../README.md
:start-after: "## Highlights"
:end-before: "##"
```

Ready to get started? Use the navigation menu to find documentation that fits
your needs.

```{toctree}
:maxdepth: 3
:hidden:

introduction/index.md
getting-started/index.md

contributing.md
glossary.md

reference/index.md
```
