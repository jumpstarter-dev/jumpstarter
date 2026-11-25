"""Fail the docs build when a driver package has no page in the driver
reference or no entry in its index."""

import os
import re
from pathlib import Path

from sphinx.util import logging

logger = logging.getLogger(__name__)

LISTED_DRIVER = re.compile(r"\{doc\}`[^`]*?<[^>]+>`\s*\(`jumpstarter-driver-([^`]+)`\)")
README_LINK = re.compile(r"jumpstarter-driver-([^/]+)/README\.md$")


def index_problems(packages, pages, index_text):
    listed = set(LISTED_DRIVER.findall(index_text))
    problems = []
    for package in packages:
        if package not in pages:
            problems.append(f"jumpstarter-driver-{package} has no page in the driver reference")
        if package not in listed:
            problems.append(f"jumpstarter-driver-{package} is not listed in the driver index")
    return problems


def check_driver_index(app):
    drivers = Path(app.srcdir) / "reference" / "package-apis" / "drivers"
    packages_dir = Path(app.srcdir).parents[1] / "python" / "packages"
    package_dirs = (path for path in packages_dir.glob("jumpstarter-driver-*") if path.is_dir())
    packages = sorted(path.name.removeprefix("jumpstarter-driver-") for path in package_dirs)
    pages = set()
    for page in drivers.glob("*.md"):
        if page.is_symlink() and (match := README_LINK.search(os.readlink(page))):
            pages.add(match.group(1))
    for problem in index_problems(packages, pages, (drivers / "index.md").read_text()):
        logger.warning(problem, type="driver-index")


def setup(app):
    app.connect("builder-inited", check_driver_index)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
