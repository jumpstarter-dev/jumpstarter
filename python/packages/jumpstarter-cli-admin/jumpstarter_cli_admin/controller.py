import aiohttp
import click
import semver
from jumpstarter_cli_common.version import get_client_version
from packaging.version import Version


async def get_latest_compatible_controller_version(
    client_version: str | None = None,
):
    if client_version is None:
        client_version = Version(get_client_version())
    else:
        client_version = Version(client_version)

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

        if version.major == client_version.major and version.minor == client_version.minor:
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
