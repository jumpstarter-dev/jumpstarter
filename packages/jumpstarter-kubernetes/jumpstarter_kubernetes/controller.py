from typing import Optional

import aiohttp
import click
import semver
from packaging.version import Version


async def get_latest_compatible_controller_version(client_version: Optional[str]):  # noqa: C901
    """Get the latest compatible controller version for a given client version"""
    if client_version is None:
        # Return the latest available version when no client version is specified
        use_fallback_only = True
        client_version_parsed = None
    else:
        use_fallback_only = False
        client_version_parsed = Version(client_version)

    async with aiohttp.ClientSession(
        raise_for_status=True,
    ) as session:
        try:
            async with session.get(
                "https://quay.io/api/v1/repository/jumpstarter-dev/helm/jumpstarter/tag/",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp = await resp.json()
        except Exception as e:
            raise click.ClickException(f"Failed to fetch controller versions: {e}") from e

    compatible = set()
    fallback = set()

    if not isinstance(resp, dict) or "tags" not in resp or not isinstance(resp["tags"], list):
        raise click.ClickException("Unexpected response fetching controller version")

    for tag in resp["tags"]:
        if not isinstance(tag, dict) or "name" not in tag:
            continue  # Skip malformed tag entries

        try:
            version = semver.VersionInfo.parse(tag["name"])
        except ValueError:
            continue  # ignore invalid versions

        if use_fallback_only:
            # When no client version specified, all versions are candidates
            fallback.add(version)
        elif version.major == client_version_parsed.major and version.minor == client_version_parsed.minor:
            compatible.add(version)
        else:
            fallback.add(version)

    if compatible:
        selected = max(compatible)
    elif fallback:
        selected = max(fallback)
    else:
        raise ValueError("No valid controller versions found in the repository")

    return str(selected)
