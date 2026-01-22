from typing import Optional

import aiohttp
import semver
from packaging.version import Version

from .exceptions import JumpstarterKubernetesError


async def get_latest_compatible_controller_version(client_version: Optional[str]):  # noqa: C901
    """Get the latest compatible controller version for a given client version"""
    if client_version is None:
        # Return the latest available version when no client version is specified
        use_fallback_only = True
        client_version_parsed = None
    else:
        use_fallback_only = False
        # Strip leading "v" for parsing but keep original for error messages
        version_to_parse = client_version[1:] if client_version.startswith("v") else client_version
        try:
            client_version_parsed = Version(version_to_parse)
        except Exception as e:
            raise JumpstarterKubernetesError(
                f"Invalid client version '{client_version}': {e}"
            ) from e

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
            raise JumpstarterKubernetesError(f"Failed to fetch controller versions: {e}") from e

    compatible = set()
    fallback = set()

    if not isinstance(resp, dict) or "tags" not in resp or not isinstance(resp["tags"], list):
        raise JumpstarterKubernetesError("Unexpected response fetching controller version")

    for tag in resp["tags"]:
        if not isinstance(tag, dict) or "name" not in tag:
            continue  # Skip malformed tag entries

        tag_name = tag["name"]
        # Strip leading "v" for parsing but keep original tag name
        version_str = tag_name[1:] if tag_name.startswith("v") else tag_name

        try:
            version = semver.VersionInfo.parse(version_str)
        except ValueError:
            continue  # ignore invalid versions

        if use_fallback_only:
            # When no client version specified, all versions are candidates
            fallback.add((version, tag_name))
        elif version.major == client_version_parsed.major and version.minor == client_version_parsed.minor:
            compatible.add((version, tag_name))
        else:
            fallback.add((version, tag_name))

    if compatible:
        # max() on tuples compares by first element (version), then second (tag_name)
        selected_version, selected_tag = max(compatible)
    elif fallback:
        selected_version, selected_tag = max(fallback)
    else:
        raise JumpstarterKubernetesError("No valid controller versions found in the repository")

    # Return the original tag string (not str(Version) or VersionInfo)
    return selected_tag
