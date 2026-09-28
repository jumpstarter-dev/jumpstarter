# Stamp python/native/jumpstarter-core with the version hatch-vcs gives the other
# Jumpstarter packages for this commit, so the exact pin in jumpstarter's Windows
# dependencies resolves. On a release, the version must match RELEASE_TAG.
# Exports JUMPSTARTER_VERSION for later workflow steps.
$ErrorActionPreference = "Stop"

Push-Location python/packages/jumpstarter
try {
    $version = uvx --from hatchling --with hatch-vcs hatchling version | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Cannot determine the Jumpstarter version" }
}
finally { Pop-Location }

$arguments = @("python/scripts/set_core_version.py", $version.Trim())
if ($env:RELEASE_TAG) { $arguments += @("--tag", $env:RELEASE_TAG) }
$stamped = uv run --no-project --python 3.12 --with packaging python @arguments | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { throw "Cannot stamp the jumpstarter-core version" }

Write-Output "jumpstarter-core version: $stamped"
if ($env:GITHUB_ENV) { "JUMPSTARTER_VERSION=$stamped" >> $env:GITHUB_ENV }
