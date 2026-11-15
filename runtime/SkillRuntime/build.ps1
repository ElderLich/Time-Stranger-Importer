param(
    [string]$ReloadedInterfacesPath,
    [string]$ExecutablePath,
    [string]$Net9ReferencePackage,
    [switch]$CoreOnly
)
$ErrorActionPreference = 'Stop'
$runtimeRoot = $PSScriptRoot
$artifactRoot = Join-Path $runtimeRoot 'artifacts'
New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null

function Invoke-DotNetStep([string]$Name, [string[]]$Arguments) {
    & dotnet @Arguments 2>&1 | Tee-Object -FilePath (Join-Path $artifactRoot ($Name + '.log'))
    if ($LASTEXITCODE -ne 0) { throw "dotnet step '$Name' failed with exit code $LASTEXITCODE" }
}
function Pin-File([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    return [ordered]@{ path = $item.FullName; bytes = $item.Length; sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
}
Push-Location $runtimeRoot
try {
    $sdk = (& dotnet --version).Trim()
    Invoke-DotNetStep 'restore-core-tests' @('restore', 'SkillRuntime.Tests/SkillRuntime.Tests.csproj', '--configfile', 'NuGet.Config')
    Invoke-DotNetStep 'build-core-tests' @('build', 'SkillRuntime.Tests/SkillRuntime.Tests.csproj', '-c', 'Release', '--no-restore')
    Invoke-DotNetStep 'core-tests' @('SkillRuntime.Tests/bin/Release/net10.0/SkillRuntime.Tests.dll')
    $coreTests = (Get-Content -LiteralPath (Join-Path $artifactRoot 'core-tests.log') | Select-Object -Last 1) | ConvertFrom-Json
    $pins = @((Pin-File 'SkillRuntime.Core/bin/Release/netstandard2.1/SkillRuntime.Core.dll'), (Pin-File 'SkillRuntime.Tests/bin/Release/net10.0/SkillRuntime.Tests.dll'))
    $dependency = $null
    $hostTests = $null
    $referencePackage = $null
    $packageSources = @()
    if (!$CoreOnly) {
        if (!$ReloadedInterfacesPath) { throw 'Pass -ReloadedInterfacesPath to the reviewed local Reloaded.Mod.Interfaces.dll, or use -CoreOnly.' }
        $ReloadedInterfacesPath = (Resolve-Path -LiteralPath $ReloadedInterfacesPath).Path
        $dependency = Pin-File $ReloadedInterfacesPath
        if ($dependency.sha256 -ne '98b33df0f380ca7361bffa9a0328c63c7d96140fc2070d180864fa157f00c88a') { throw 'Unreviewed Reloaded interface fingerprint.' }
        $reference = '-p:ReloadedInterfacesPath=' + $ReloadedInterfacesPath
        if ($Net9ReferencePackage) { & (Join-Path $runtimeRoot 'prepare-net9-reference.ps1') -PackagePath $Net9ReferencePackage }
        elseif (!(Test-Path -LiteralPath 'artifacts/dependencies/microsoft.netcore.app.ref.9.0.22.nupkg')) { & (Join-Path $runtimeRoot 'prepare-net9-reference.ps1') }
        $packHash = (Get-FileHash -LiteralPath 'artifacts/dependencies/microsoft.netcore.app.ref.9.0.22.nupkg' -Algorithm SHA256).Hash
        if ($packHash -ne 'D54EB743BFB407095944D32FC99183DA6E8A7F50F1BA930D600A2BCB55CC8813') { throw 'Reference package fingerprint differs.' }
        $referencePackage = Pin-File 'artifacts/dependencies/microsoft.netcore.app.ref.9.0.22.nupkg'
        $packageSources = @('artifacts/dependencies')
        $boundedRestore = '-p:DisableTransitiveFrameworkReferenceDownloads=true'
        Invoke-DotNetStep 'restore-host-tests' @('restore', 'SkillRuntime.HostTests/SkillRuntime.HostTests.csproj', '--source', 'artifacts/dependencies', '--packages', 'artifacts/packages', $boundedRestore, $reference)
        Invoke-DotNetStep 'build-host-tests' @('build', 'SkillRuntime.HostTests/SkillRuntime.HostTests.csproj', '-c', 'Release', '--no-restore', $boundedRestore, $reference)
        Invoke-DotNetStep 'host-tests' @('SkillRuntime.HostTests/bin/Release/net9.0/SkillRuntime.HostTests.dll')
        $hostTests = (Get-Content -LiteralPath (Join-Path $artifactRoot 'host-tests.log') | Select-Object -Last 1) | ConvertFrom-Json
        $pins += Pin-File 'SkillRuntime.Reloaded/bin/Release/net9.0/SkillRuntime.Reloaded.dll'
    }
    $assessment = $null
    if ($ExecutablePath) {
        Invoke-DotNetStep 'executable-assessment' @('SkillRuntime.Tests/bin/Release/net10.0/SkillRuntime.Tests.dll', '--assess', $ExecutablePath)
        $assessment = Get-Content -Raw -LiteralPath (Join-Path $artifactRoot 'executable-assessment.log') | ConvertFrom-Json
    }
    [ordered]@{
        schema = 'skill-runtime-offline-build-v1'; sdk = $sdk
        core_framework = 'netstandard2.1'; host_framework = 'net9.0'; core_tests_framework = 'net10.0'; host_tests_framework = 'net9.0'
        package_sources = $packageSources; reference_package = $referencePackage; reloaded_interface = $dependency; outputs = $pins
        core_tests = $coreTests; host_tests = $hostTests
        native_profile = $assessment; native_runtime_enabled = $false; live_loader_tested = $false
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $artifactRoot 'build-report.json') -Encoding UTF8
}
finally { Pop-Location }
