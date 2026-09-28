param(
    [string]$PodmanPath = "$env:LOCALAPPDATA/Programs/Podman/podman.exe",
    [string]$Image = "localhost/jumpstarter-windows-e2e:latest",
    [string]$Version = "0.10.0.dev112"
)

$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
if (-not (Test-Path -LiteralPath $PodmanPath -PathType Leaf)) {
    throw "Podman executable not found: $PodmanPath"
}

# A fresh task-owned directory avoids stale files and never deletes host data.
$contextPath = Join-Path $repoRoot ".e2e/windows-client/image-context-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $contextPath -Force | Out-Null
$previousConnection = $env:CONTAINER_CONNECTION
Push-Location $repoRoot
try {
    $revision = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-fA-F]{40,64}$') {
        throw "Cannot determine the source Git revision"
    }
    # Include uncommitted changes and new source files, but not ignored virtual
    # environments, caches, secrets, or the .git and .e2e directories.
    $sources = @(git -c core.quotepath=false ls-files --cached --others --exclude-standard python/packages python/examples python/native)
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot enumerate Python source files"
    }
    $sources += @(
        "python/pyproject.toml",
        "python/uv.lock",
        "e2e/windows/Containerfile",
        "e2e/windows/exporter.yaml",
        "e2e/windows/windows_exporter_fixture.py"
    )
    foreach ($source in ($sources | Sort-Object -Unique)) {
        $sourcePath = Join-Path $repoRoot $source
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            continue # A deleted tracked file is absent from the current source.
        }
        $destination = Join-Path $contextPath $source
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $sourcePath -Destination $destination
    }

    $env:CONTAINER_CONNECTION = "podman-machine-default-root"
    Write-Host "Building $Image from $contextPath (source revision $revision)"
    & $PodmanPath build --tag $Image --file e2e/windows/Containerfile `
        --build-arg "HATCH_VCS_PRETEND_VERSION=$Version" `
        --build-arg "SOURCE_REVISION=$revision" $contextPath
    if ($LASTEXITCODE -ne 0) {
        throw "Exporter image build failed with exit code $LASTEXITCODE"
    }
}
finally {
    $env:CONTAINER_CONNECTION = $previousConnection
    Pop-Location
}
