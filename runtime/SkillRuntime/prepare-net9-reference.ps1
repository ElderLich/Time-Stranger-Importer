param([string]$PackagePath = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.nuget/packages/microsoft.netcore.app.ref/9.0.22/microsoft.netcore.app.ref.9.0.22.nupkg'))
$ErrorActionPreference = 'Stop'
$expected = 'D54EB743BFB407095944D32FC99183DA6E8A7F50F1BA930D600A2BCB55CC8813'
if ((Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash -ne $expected) { throw 'Reference package differs from reviewed official Microsoft.NETCore.App.Ref 9.0.22.' }
$feed = Join-Path $PSScriptRoot 'artifacts/dependencies'
New-Item -ItemType Directory -Force -Path $feed | Out-Null
$destination = Join-Path $feed 'microsoft.netcore.app.ref.9.0.22.nupkg'
Copy-Item -LiteralPath $PackagePath -Destination $destination -Force
[ordered]@{
    package = 'Microsoft.NETCore.App.Ref'; version = '9.0.22'; bytes = (Get-Item -LiteralPath $destination).Length
    sha256 = $expected.ToLowerInvariant(); source = 'https://api.nuget.org/v3/index.json'
    provenance = 'Existing local NuGet cache, Microsoft author, embedded NuGet signature, matching pinned SHA256'
    runtime_repository_commit = 'faaecba82df8933d7af60972138ce25f9c776cbd'
    workspace_feed = $feed; installed_runtimes_changed = $false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $feed 'reference-package.json') -Encoding UTF8
Write-Output "Prepared workspace-local net9 reference feed: $feed"
