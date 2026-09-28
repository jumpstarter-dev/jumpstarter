# Test bootstrap only: production config saving still needs Windows ACL support.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Kubeconfig,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$Kubectl = 'kubectl',
    [string]$Context = 'kind-jumpstarter-windows-e2e'
)
$ErrorActionPreference = 'Stop'
$namespace = 'jumpstarter-windows-e2e'

function Read-KubeJson {
    param([string[]]$Arguments)
    $output = & $Kubectl --kubeconfig $Kubeconfig --context $Context -n $namespace @Arguments -o json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read E2E Kubernetes resource' }
    return $output | ConvertFrom-Json
}

if (Test-Path -LiteralPath $OutputDirectory) {
    throw 'Use a fresh output directory so existing permissions or credential files cannot be reused'
}
$directory = New-Item -ItemType Directory -Path $OutputDirectory
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# Protect the directory before writing test credentials; do not change user configs.
& icacls.exe $directory.FullName /inheritance:r /grant:r "*${identity}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict access to E2E credentials' }
$ca = Read-KubeJson @('get', 'configmap', 'jumpstarter-service-ca-cert')
$caEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($ca.data.'ca.crt'))
foreach ($item in @(
    @{ Resource = 'client'; Name = 'windows-e2e-client'; Kind = 'ClientConfig'; File = 'client.json' },
    @{ Resource = 'exporter'; Name = 'windows-e2e-linux'; Kind = 'ExporterConfig'; File = 'exporter.json' }
)) {
    $resource = Read-KubeJson @('get', $item.Resource, $item.Name)
    if (-not $resource.status.credential.name) { throw "Credentials not ready for $($item.Name)" }
    $secret = Read-KubeJson @('get', 'secret', $resource.status.credential.name)
    $config = @{
        apiVersion = 'jumpstarter.dev/v1alpha1'
        kind = $item.Kind
        metadata = @{ name = $item.Name; namespace = $namespace }
        endpoint = $resource.status.endpoint
        token = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($secret.data.token))
        tls = @{ ca = $caEncoded }
    }
    if ($item.Kind -eq 'ClientConfig') {
        $config.drivers = @{ allow = @(
            'jumpstarter_driver_composite.client.CompositeClient',
            'jumpstarter_driver_power.client.PowerClient',
            'jumpstarter_driver_network.client.NetworkClient',
            'jumpstarter_driver_pyserial.client.PySerialClient'
        ) }
    } else {
        $config.export = @{
            power = @{ type = 'jumpstarter_driver_power.driver.MockPower' }
            network = @{
                type = 'jumpstarter_driver_network.driver.TcpNetwork'
                config = @{ host = '127.0.0.1'; port = 19091 }
            }
            serial = @{
                type = 'jumpstarter_driver_pyserial.driver.PySerial'
                config = @{ url = 'loop://' }
            }
        }
    }
    $path = Join-Path $directory.FullName $item.File
    [IO.File]::WriteAllText($path, ($config | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
    Write-Output "Wrote protected E2E config: $path"
}
